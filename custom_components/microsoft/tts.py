"""Support for Microsoft Text-to-Speech (TTS)."""

from __future__ import annotations

import logging
import re
import time
import asyncio
from collections.abc import AsyncGenerator, Mapping
from typing import Any
from xml.sax.saxutils import escape
import xml.etree.ElementTree as ET

import aiohttp

from homeassistant.components.tts import (
    ATTR_VOICE,
    TextToSpeechEntity,
    TTSAudioRequest,
    TTSAudioResponse,
    TtsAudioType,
    Voice,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY, CONF_LANGUAGE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_OUTPUT_FORMAT,
    CONF_PITCH,
    CONF_RATE,
    CONF_REGION,
    CONF_VOICE,
    CONF_VOLUME,
    CONF_STYLE,
    CONF_STYLE_DEGREE,
    CONF_ROLE,
    CONF_ALLOW_RAW_SSML,
    CONF_RAW_SSML,
    DEFAULT_OUTPUT_FORMAT,
    DOMAIN,
    VOICES_CACHE_TTL,
    AZURE_TTS_BASE_URL,
    AZURE_VOICES_LIST_URL,
    AZURE_PORTAL_URL,
    SSML_NAMESPACE,
    AUDIO_CHUNK_SIZE,
)

_LOGGER = logging.getLogger(__name__)

# Universal sentence-ending pattern supporting multiple languages
# Covers: Latin (.!?), CJK (。！？｡), Arabic (؟۔), Indic (।॥), and more
SENTENCE_ENDINGS = re.compile(
    r"[.!?।॥。！？｡؟۔‽⁇⁈⁉\u0964\u0965\u06D4\u061F\u3002\uFF01\uFF1F\uFF61]"
    r"(?!"  # Negative lookahead - NOT followed by:
    r"[a-z0-9]"  # lowercase letter or digit (avoids domains/decimals)
    r")"
    r"(?:[\s\u3000]+|(?=[\u3000-\u303F\u4E00-\u9FFF\uAC00-\uD7AF])|$)"
    # Followed by: space(s) OR CJK character OR end of string
)

STREAM_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
STREAM_MAX_RETRIES = 1
STREAM_RETRY_BACKOFF_SECONDS = 0.4
STREAM_FORCE_FLUSH_CHARS = 280


def _get_file_extension_from_format(output_format: str) -> str:
    """Extract the correct file extension from Azure output format.

    Args:
        output_format: Azure output format string (e.g., 'audio-24khz-96kbitrate-mono-mp3')

    Returns:
        File extension without dot (e.g., 'mp3', 'opus', 'wav', 'webm')
    """
    # Map Azure format patterns to file extensions
    if "mp3" in output_format:
        extension = "mp3"
    elif "webm" in output_format:
        extension = "webm"
    elif "ogg" in output_format or "opus" in output_format:
        extension = "ogg"
    elif "g722" in output_format:
        extension = "g722"
    elif "amr" in output_format:
        extension = "amr"
    elif "mulaw" in output_format:
        extension = "ulaw"
    elif "alaw" in output_format:
        extension = "alaw"
    elif "pcm" in output_format or "raw" in output_format:
        extension = "raw"
    elif "riff" in output_format:
        extension = "wav"
    elif "flac" in output_format:
        extension = "flac"
    else:
        # Default fallback
        extension = "mp3"

    _LOGGER.debug("Mapped output format '%s' to extension '%s'", output_format, extension)
    return extension


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Microsoft Text-to-Speech (TTS) entity."""
    entity = AzureTTSEntity(hass, config_entry)
    # Attempt to fetch voices immediately
    await entity.async_fetch_voices()
    async_add_entities([entity])


class AzureTTSEntity(TextToSpeechEntity):
    """The Microsoft Text-to-Speech (TTS) API entity."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        """Init Microsoft Text-to-Speech (TTS) service."""
        self.hass = hass
        self._config_entry = config_entry
        self._apikey = config_entry.data[CONF_API_KEY]
        self._region = config_entry.data[CONF_REGION]
        self._language = config_entry.options.get(
            CONF_LANGUAGE, config_entry.data[CONF_LANGUAGE]
        )

        # Default voice from Options (if changed by user later) or Initial Setup
        self._default_voice = config_entry.options.get(
            CONF_VOICE, config_entry.data.get(CONF_VOICE)
        )
        self._output_format = config_entry.options.get(
            CONF_OUTPUT_FORMAT,
            config_entry.data.get(CONF_OUTPUT_FORMAT, DEFAULT_OUTPUT_FORMAT),
        )
        _LOGGER.debug("Initialized TTS entity with output format: %s", self._output_format)

        self._session = async_get_clientsession(hass)
        self._voices_data = []

    @property
    def device_info(self) -> DeviceInfo:
        """Return device specific attributes."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._config_entry.entry_id)},
            name="Microsoft Text-to-Speech (TTS)",
            manufacturer="Microsoft",
            model="Cognitive Services",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=AZURE_PORTAL_URL,
        )

    async def async_fetch_voices(self):
        """Fetch available voices from Azure."""
        # Check global cache first
        cache_data = self.hass.data.get(DOMAIN, {}).get("voices_cache")
        if cache_data:
            cached_voices, cached_time = cache_data
            if time.time() - cached_time < VOICES_CACHE_TTL:
                self._voices_data = cached_voices
                _LOGGER.debug(
                    "Used cached voices (age: %.0fs)", time.time() - cached_time
                )
                return

        url = AZURE_VOICES_LIST_URL.format(region=self._region)
        headers = {"Ocp-Apim-Subscription-Key": self._apikey}
        try:
            async with self._session.get(url, headers=headers) as response:
                if response.status == 200:
                    self._voices_data = await response.json()
                    # Update global cache with timestamp
                    self.hass.data.setdefault(DOMAIN, {})["voices_cache"] = (
                        self._voices_data,
                        time.time(),
                    )
                    _LOGGER.debug(
                        "Fetched %d voices from Azure", len(self._voices_data)
                    )
                else:
                    _LOGGER.error("Failed to fetch voices: %s", response.status)
        except Exception as ex:
            _LOGGER.error("Error fetching voices: %s", ex)

    def _find_azure_locale(self, language: str) -> str | None:
        """Resolve a case-insensitive language code to the correct Azure locale."""
        if not self._voices_data:
            return None

        language_lower = language.lower()
        for v in self._voices_data:
            if v["Locale"].lower() == language_lower:
                return v["Locale"]
        return None

    @property
    def name(self) -> str:
        """Return name of the entity."""
        return "Microsoft Text-to-Speech (TTS)"

    @property
    def unique_id(self) -> str:
        """Return unique ID."""
        return self._config_entry.entry_id

    @property
    def default_language(self) -> str:
        """Return the default language."""
        # Return lowercase to match supported_languages behavior
        return self._language.lower()

    @property
    def supported_languages(self) -> list[str]:
        """Return list of supported languages."""
        if self._voices_data:
            # Return BOTH original (it-IT) and lowercase (it-it) versions
            # This satisfies strict lowercase validation in Assist Pipelines
            # AND standard case-sensitive checks in Media Browser.
            langs = set()
            for v in self._voices_data:
                locale = v["Locale"]
                langs.add(locale)  # it-IT
                langs.add(locale.lower())  # it-it
            return sorted(list(langs))

        # Fallback: return config language and its lower variant
        return sorted(list({self._language.lower()}))

    @property
    def supported_options(self) -> list[str]:
        """Return list of supported options."""
        return [
            ATTR_VOICE,
            CONF_RATE,
            CONF_PITCH,
            CONF_VOLUME,
            CONF_STYLE,
            CONF_STYLE_DEGREE,
            CONF_ROLE,
            CONF_RAW_SSML,
        ]

    @property
    def default_options(self) -> Mapping[str, Any]:
        """Return a mapping with the default options."""
        return {
            ATTR_VOICE: self._default_voice,
        }

    def _resolve_voice_and_language(
        self, language: str, options: dict[str, Any]
    ) -> tuple[str, str]:
        """Resolve voice and Azure locale for the given language and options.

        Returns:
            Tuple of (voice_name, azure_locale)
        """
        # Voice selection
        voice = options.get(ATTR_VOICE, self._default_voice)

        # Resolve Azure Locale (HA might pass 'it-it', Azure needs 'it-IT')
        azure_locale = self._find_azure_locale(language)
        lang_to_use = azure_locale if azure_locale else language

        # Smart Voice Fallback (Language Mismatch)
        if self._voices_data and azure_locale:
            if ATTR_VOICE not in options and self._language.lower() != language.lower():
                # Pick a compatible female voice for the new language
                for v in self._voices_data:
                    if v["Locale"] == azure_locale:
                        if v["Gender"] == "Female":
                            voice = v["ShortName"]
                            break
                        # Fallback to first found if no female
                        voice = v["ShortName"]

        return voice, lang_to_use

    def _normalize_prosody_options(self, options: dict[str, Any]) -> dict[str, str]:
        """Normalize and validate prosody options (rate, pitch, volume, style, etc.).

        Returns:
            Dictionary with normalized options
        """
        # Get options with priority: Service Call > Config Options > Default
        rate = options.get(CONF_RATE, self._config_entry.options.get(CONF_RATE, "0%"))
        pitch = options.get(
            CONF_PITCH, self._config_entry.options.get(CONF_PITCH, "default")
        )
        volume = options.get(
            CONF_VOLUME, self._config_entry.options.get(CONF_VOLUME, "default")
        )
        style = options.get(CONF_STYLE, self._config_entry.options.get(CONF_STYLE, ""))
        style_degree = options.get(
            CONF_STYLE_DEGREE, self._config_entry.options.get(CONF_STYLE_DEGREE, "1")
        )
        role = options.get(CONF_ROLE, self._config_entry.options.get(CONF_ROLE, ""))

        # Smart Rate Handling
        if isinstance(rate, (int, float)):
            rate_val = float(rate)
            if 0.1 <= abs(rate_val) <= 3.0 and isinstance(rate, float):
                percent = int((rate_val - 1.0) * 100)
                rate = f"{'+' if percent >= 0 else ''}{percent}%"
            else:
                rate = f"{int(rate)}%"

        # Validate pitch (Azure accepts: x-low, low, medium, high, x-high, default, or ±50%)
        valid_pitch_names = {"x-low", "low", "medium", "high", "x-high", "default"}
        if isinstance(pitch, str) and pitch.lower() not in valid_pitch_names:
            if not (pitch.endswith("%") or pitch.endswith("Hz")):
                pitch = "default"

        # Validate style_degree (Azure accepts 0.01-2.0)
        if style_degree:
            try:
                degree_val = float(style_degree)
                if not (0.01 <= degree_val <= 2.0):
                    style_degree = "1"
            except (ValueError, TypeError):
                style_degree = "1"

        return {
            "rate": rate,
            "pitch": pitch,
            "volume": volume,
            "style": style,
            "style_degree": style_degree,
            "role": role,
        }

    def _resolve_raw_ssml_enabled(self, options: dict[str, Any]) -> bool:
        """Resolve raw SSML mode with per-call override."""
        global_setting = self._config_entry.options.get(CONF_ALLOW_RAW_SSML, False)

        if CONF_RAW_SSML not in options:
            return bool(global_setting)

        value = options.get(CONF_RAW_SSML)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        if isinstance(value, (int, float)):
            return value != 0

        return bool(global_setting)

    def _build_ssml(
        self,
        message: str,
        voice: str,
        language: str,
        prosody_options: dict[str, str],
        allow_raw_ssml: bool = False,
    ) -> str:
        """Build SSML document for Azure TTS.

        Args:
            message: The text to synthesize
            voice: Voice name (e.g., 'it-IT-ElsaNeural')
            language: Language locale (e.g., 'it-IT')
            prosody_options: Dictionary with rate, pitch, volume, style, style_degree, role

        Returns:
            Complete SSML document as string
        """
        stripped_message = message.strip()

        # Raw SSML mode: if message is a full SSML document, use as-is.
        if allow_raw_ssml and stripped_message.lower().startswith("<speak"):
            return message

        xml_doc = (
            f"<speak version='1.0' xmlns:mstts='{SSML_NAMESPACE}' "
            f"xml:lang='{language}'>"
            f"<voice xml:lang='{language}' name='{voice}'>"
        )

        # Logic for express-as
        style = prosody_options["style"]
        if style:
            xml_doc += f"<mstts:express-as style='{style}'"
            if prosody_options["role"]:
                xml_doc += f" role='{prosody_options['role']}'"
            if prosody_options["style_degree"]:
                xml_doc += f" styledegree='{prosody_options['style_degree']}'"
            xml_doc += ">"

        # Prosody wrapping text
        xml_doc += (
            f"<prosody rate='{prosody_options['rate']}' "
            f"pitch='{prosody_options['pitch']}' "
            f"volume='{prosody_options['volume']}'>"
        )
        if allow_raw_ssml:
            xml_doc += message
        else:
            xml_doc += escape(message).replace('"', "&quot;")
        xml_doc += "</prosody>"

        if style:
            xml_doc += "</mstts:express-as>"

        xml_doc += "</voice></speak>"

        return xml_doc

    def _build_validated_ssml(
        self,
        message: str,
        voice: str,
        language: str,
        prosody_options: dict[str, str],
        allow_raw_ssml: bool,
    ) -> str:
        """Build SSML and validate XML when raw SSML mode is enabled.

        Falls back to escaped text when raw SSML is invalid.
        """
        ssml = self._build_ssml(
            message=message,
            voice=voice,
            language=language,
            prosody_options=prosody_options,
            allow_raw_ssml=allow_raw_ssml,
        )

        if not allow_raw_ssml:
            return ssml

        try:
            ET.fromstring(ssml)
            return ssml
        except ET.ParseError as ex:
            _LOGGER.warning(
                "Invalid raw SSML received, falling back to escaped text: %s", ex
            )
            return self._build_ssml(
                message=message,
                voice=voice,
                language=language,
                prosody_options=prosody_options,
                allow_raw_ssml=False,
            )

    @callback
    def async_get_supported_voices(self, language: str) -> list[Voice] | None:
        """Return list of supported voices for a language."""
        if not self._voices_data:
            return []

        # Resolve the correct Azure locale (e.g. 'it-it' -> 'it-IT')
        azure_locale = self._find_azure_locale(language)

        # Fallback: if exact match fails, try loose prefix matching as before
        if not azure_locale:
            # Just use the input language for the prefix check
            match_target = language.lower()
        else:
            match_target = azure_locale.lower()

        voices = [
            Voice(voice_id=v["ShortName"], name=f"{v['LocalName']} ({v['Gender']})")
            for v in self._voices_data
            if v["Locale"].lower().startswith(match_target)
        ]

        voices.sort(key=lambda x: x.name)
        return voices

    async def async_get_tts_audio(
        self, message: str, language: str, options: dict | None = None
    ) -> TtsAudioType:
        """Load TTS from Azure (legacy method, non-streaming)."""
        if options is None:
            options = {}

        # Resolve voice and language using helper
        voice, lang_to_use = self._resolve_voice_and_language(language, options)

        # Normalize prosody options using helper
        prosody_options = self._normalize_prosody_options(options)
        allow_raw_ssml = self._resolve_raw_ssml_enabled(options)

        # Build SSML using helper
        xml_doc = self._build_validated_ssml(
            message, voice, lang_to_use, prosody_options, allow_raw_ssml
        )

        # Prepare request headers
        headers = {
            "Ocp-Apim-Subscription-Key": self._apikey,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": self._output_format,
            "User-Agent": "HomeAssistant-MicrosoftAzureTTS",
        }

        url = AZURE_TTS_BASE_URL.format(region=self._region)

        try:
            async with self._session.post(
                url, headers=headers, data=xml_doc.encode("utf-8")
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    _LOGGER.error(
                        "Error %d from Azure TTS: %s", response.status, error_text
                    )
                    return None, None

                data = await response.read()

        except aiohttp.ClientError as ex:
            _LOGGER.error("Error occurred for Microsoft Azure TTS: %s", ex)
            return None, None

        file_extension = _get_file_extension_from_format(self._output_format)
        return file_extension, data

    async def async_stream_tts_audio(
        self, request: TTSAudioRequest
    ) -> TTSAudioResponse:
        """Stream TTS audio from Azure using sentence-by-sentence synthesis.

        This method provides reduced latency by:
        1. Accumulating text chunks until a sentence boundary is detected
        2. Synthesizing each complete sentence independently
        3. Streaming audio chunks as they arrive from Azure

        Supports multi-language sentence detection including:
        - Latin scripts (.!?)
        - CJK languages (。！？)
        - Arabic (؟۔)
        - Indic scripts (।॥)
        """
        options = request.options or {}

        # Resolve voice and language once for all sentences
        voice, lang_to_use = self._resolve_voice_and_language(request.language, options)

        # Normalize prosody options once for all sentences
        prosody_options = self._normalize_prosody_options(options)
        allow_raw_ssml = self._resolve_raw_ssml_enabled(options)

        # Prepare request headers (reused for all requests)
        headers = {
            "Ocp-Apim-Subscription-Key": self._apikey,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": self._output_format,
            "User-Agent": "HomeAssistant-MicrosoftAzureTTS",
        }

        url = AZURE_TTS_BASE_URL.format(region=self._region)

        async def stream_sentence_audio(sentence: str) -> AsyncGenerator[bytes]:
            """Synthesize one sentence and yield audio chunks with basic retry."""
            ssml = self._build_validated_ssml(
                sentence, voice, lang_to_use, prosody_options, allow_raw_ssml
            )

            for attempt in range(STREAM_MAX_RETRIES + 1):
                try:
                    async with self._session.post(
                        url, headers=headers, data=ssml.encode("utf-8")
                    ) as response:
                        if response.status == 200:
                            async for audio_chunk in response.content.iter_chunked(
                                AUDIO_CHUNK_SIZE
                            ):
                                yield audio_chunk
                            return

                        error_text = await response.text()
                        if (
                            response.status in STREAM_RETRYABLE_STATUS
                            and attempt < STREAM_MAX_RETRIES
                        ):
                            _LOGGER.warning(
                                "Retrying Azure TTS sentence after HTTP %d (attempt %d/%d)",
                                response.status,
                                attempt + 2,
                                STREAM_MAX_RETRIES + 1,
                            )
                            await asyncio.sleep(
                                STREAM_RETRY_BACKOFF_SECONDS * (attempt + 1)
                            )
                            continue

                        _LOGGER.error(
                            "Error %d from Azure TTS for sentence '%s...': %s",
                            response.status,
                            sentence[:50],
                            error_text,
                        )
                        return

                except aiohttp.ClientError as ex:
                    if attempt < STREAM_MAX_RETRIES:
                        _LOGGER.warning(
                            "Network error while streaming sentence, retrying (attempt %d/%d): %s",
                            attempt + 2,
                            STREAM_MAX_RETRIES + 1,
                            ex,
                        )
                        await asyncio.sleep(
                            STREAM_RETRY_BACKOFF_SECONDS * (attempt + 1)
                        )
                        continue

                    _LOGGER.error(
                        "Error streaming Azure TTS for sentence '%s...': %s",
                        sentence[:50],
                        ex,
                    )
                    return

        async def data_gen() -> AsyncGenerator[bytes]:
            """Generate audio chunks sentence-by-sentence."""
            sentence_buffer = ""

            try:
                message_gen = getattr(request, "message_gen", None)
                if message_gen is None:
                    full_message = getattr(request, "message", "").strip()
                    if full_message:
                        async for audio_chunk in stream_sentence_audio(full_message):
                            yield audio_chunk
                    return

                # Process incoming text chunks from LLM
                async for text_chunk in message_gen:
                    if not text_chunk:
                        continue

                    sentence_buffer += text_chunk

                    # Check for sentence boundaries
                    while match := SENTENCE_ENDINGS.search(sentence_buffer):
                        # Extract complete sentence (including punctuation)
                        sentence_end = match.end()
                        sentence = sentence_buffer[:sentence_end].strip()
                        sentence_buffer = sentence_buffer[sentence_end:]

                        # Skip empty sentences
                        if not sentence:
                            continue

                        async for audio_chunk in stream_sentence_audio(sentence):
                            yield audio_chunk

                    # Force flush for very long text with no sentence-ending punctuation.
                    if len(sentence_buffer) >= STREAM_FORCE_FLUSH_CHARS:
                        split_idx = sentence_buffer.rfind(" ", 0, STREAM_FORCE_FLUSH_CHARS)
                        if split_idx <= 0:
                            split_idx = STREAM_FORCE_FLUSH_CHARS

                        partial_sentence = sentence_buffer[:split_idx].strip()
                        sentence_buffer = sentence_buffer[split_idx:]
                        if partial_sentence:
                            async for audio_chunk in stream_sentence_audio(partial_sentence):
                                yield audio_chunk

                # Process any remaining text (last sentence without punctuation)
                remaining_text = sentence_buffer.strip()
                if remaining_text:
                    async for audio_chunk in stream_sentence_audio(remaining_text):
                        yield audio_chunk

            except Exception as ex:
                _LOGGER.error("Unexpected error in streaming TTS: %s", ex)
                raise

        file_extension = _get_file_extension_from_format(self._output_format)
        return TTSAudioResponse(extension=file_extension, data_gen=data_gen())
