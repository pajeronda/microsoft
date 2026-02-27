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
from .ssml_utils import (
    RAW_SSML_SPEAK_CLOSE_RE,
    apply_default_prosody_to_raw_ssml,
    extract_complete_top_level_ssml_units,
    sanitize_raw_ssml_light,
    ssml_to_plain_text,
    wrap_raw_ssml_unit,
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
RAW_SSML_SPEAK_OPEN_RE = re.compile(r"<speak\b[^>]*>", re.IGNORECASE)


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

    _LOGGER.debug(
        "Mapped output format '%s' to extension '%s'", output_format, extension
    )
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

    @staticmethod
    def _coerce_bool(value: Any, default: bool = False) -> bool:
        """Convert common bool-like values to bool."""
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"1", "true", "yes", "on"}:
                return True
            if lowered in {"0", "false", "no", "off"}:
                return False
            return default
        if isinstance(value, (int, float)):
            return value != 0
        return default

    def _is_raw_ssml_globally_enabled(self) -> bool:
        """Return global raw SSML setting from options/data."""
        raw_setting = self._config_entry.options.get(
            CONF_ALLOW_RAW_SSML,
            self._config_entry.data.get(CONF_ALLOW_RAW_SSML, False),
        )
        return self._coerce_bool(raw_setting, default=False)

    def _build_request_headers(self) -> dict[str, str]:
        """Build Azure TTS request headers."""
        return {
            "Ocp-Apim-Subscription-Key": self._apikey,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": self._output_format,
            "User-Agent": "HomeAssistant-MicrosoftAzureTTS",
        }

    def _prepare_synthesis_context(
        self, language: str, options: dict[str, Any]
    ) -> tuple[str, str, dict[str, str], bool]:
        """Resolve synthesis context shared by sync and streaming paths."""
        voice, lang_to_use = self._resolve_voice_and_language(language, options)
        prosody_options = self._normalize_prosody_options(options)
        allow_raw_ssml = self._resolve_raw_ssml_enabled(options)
        return voice, lang_to_use, prosody_options, allow_raw_ssml

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
        _LOGGER.debug(
            "Initialized TTS entity with output format: %s", self._output_format
        )

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
            CONF_RAW_SSML: self._is_raw_ssml_globally_enabled(),
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
        """Resolve raw SSML mode with per-call override.

        Precedence: per-call option > global setting.
        """
        global_setting = self._is_raw_ssml_globally_enabled()

        if CONF_RAW_SSML not in options:
            return global_setting

        return self._coerce_bool(options.get(CONF_RAW_SSML), default=global_setting)

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
            return apply_default_prosody_to_raw_ssml(stripped_message, prosody_options)

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
        # In raw mode, only inject unescaped content when it looks like an SSML fragment.
        # Plain text must still be escaped to avoid XML parsing issues.
        if allow_raw_ssml:
            looks_like_ssml_fragment = stripped_message.startswith(
                "<"
            ) and stripped_message.endswith(">")
            if looks_like_ssml_fragment:
                xml_doc += message
            else:
                xml_doc += escape(message).replace('"', "&quot;")
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
        # Safety net: when input is already a full SSML document, treat it as raw.
        # This avoids speaking XML tags if global/per-call raw flags are not propagated.
        force_raw_from_message = message.strip().lower().startswith("<speak")
        effective_raw_ssml = allow_raw_ssml or force_raw_from_message

        ssml = self._build_ssml(
            message=message,
            voice=voice,
            language=language,
            prosody_options=prosody_options,
            allow_raw_ssml=effective_raw_ssml,
        )

        if not effective_raw_ssml:
            return ssml

        try:
            ET.fromstring(ssml)
            return ssml
        except ET.ParseError as ex:
            _LOGGER.warning(
                "Invalid raw SSML received, attempting light repair: %s", ex
            )
            # Try a non-destructive SSML repair pass before falling back.
            repaired_message = sanitize_raw_ssml_light(message)
            repaired_ssml = self._build_ssml(
                message=repaired_message,
                voice=voice,
                language=language,
                prosody_options=prosody_options,
                allow_raw_ssml=effective_raw_ssml,
            )
            try:
                ET.fromstring(repaired_ssml)
                return repaired_ssml
            except ET.ParseError as repair_ex:
                _LOGGER.warning(
                    "Invalid raw SSML after light repair, falling back to escaped text: %s",
                    repair_ex,
                )

            is_raw_like_markup = message.strip().startswith("<")
            _LOGGER.warning(
                "Falling back to plain text synthesis for malformed raw SSML."
            )
            # If raw SSML is malformed, avoid speaking XML tags out loud:
            # convert markup to plain text before normal synthesis.
            fallback_message = (
                ssml_to_plain_text(repaired_message)
                if force_raw_from_message or is_raw_like_markup
                else message
            )
            return self._build_ssml(
                message=fallback_message,
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

    async def _async_synthesize_audio_bytes(
        self,
        ssml: str,
        retries: int,
        error_context: str,
    ) -> bytes | None:
        """Send SSML to Azure and return full audio bytes with retry policy."""
        headers = self._build_request_headers()
        url = AZURE_TTS_BASE_URL.format(region=self._region)

        for attempt in range(retries + 1):
            try:
                async with self._session.post(
                    url, headers=headers, data=ssml.encode("utf-8")
                ) as response:
                    if response.status == 200:
                        return await response.read()

                    error_text = await response.text()
                    if response.status in STREAM_RETRYABLE_STATUS and attempt < retries:
                        _LOGGER.warning(
                            "Retrying Azure TTS after HTTP %d (attempt %d/%d) for %s",
                            response.status,
                            attempt + 2,
                            retries + 1,
                            error_context,
                        )
                        await asyncio.sleep(
                            STREAM_RETRY_BACKOFF_SECONDS * (attempt + 1)
                        )
                        continue

                    _LOGGER.error(
                        "Error %d from Azure TTS (%s): %s",
                        response.status,
                        error_context,
                        error_text,
                    )
                    return None
            except aiohttp.ClientError as ex:
                if attempt < retries:
                    _LOGGER.warning(
                        "Network error while calling Azure TTS, retrying (attempt %d/%d) for %s: %s",
                        attempt + 2,
                        retries + 1,
                        error_context,
                        ex,
                    )
                    await asyncio.sleep(STREAM_RETRY_BACKOFF_SECONDS * (attempt + 1))
                    continue

                _LOGGER.error(
                    "Error occurred for Microsoft Azure TTS (%s): %s", error_context, ex
                )
                return None

        return None

    async def _stream_sentence_audio(
        self,
        sentence: str,
        voice: str,
        language: str,
        prosody_options: dict[str, str],
        allow_raw_ssml: bool,
    ) -> AsyncGenerator[bytes]:
        """Synthesize one sentence and yield audio chunks."""
        ssml = self._build_validated_ssml(
            sentence, voice, language, prosody_options, allow_raw_ssml
        )
        audio_bytes = await self._async_synthesize_audio_bytes(
            ssml=ssml,
            retries=STREAM_MAX_RETRIES,
            error_context=f"sentence '{sentence[:50]}...'",
        )
        if not audio_bytes:
            return

        for idx in range(0, len(audio_bytes), AUDIO_CHUNK_SIZE):
            yield audio_bytes[idx : idx + AUDIO_CHUNK_SIZE]

    async def _stream_raw_ssml_message(
        self,
        message_gen: AsyncGenerator[str],
        voice: str,
        language: str,
        prosody_options: dict[str, str],
        allow_raw_ssml: bool,
    ) -> AsyncGenerator[bytes]:
        """Stream raw SSML content preserving XML unit boundaries."""
        ssml_buffer = ""
        saw_speak_open = False

        async def flush_complete_ssml_units() -> AsyncGenerator[bytes]:
            nonlocal ssml_buffer
            units, remainder, _ = extract_complete_top_level_ssml_units(ssml_buffer)
            ssml_buffer = remainder

            for unit in units:
                if not unit:
                    continue
                sentence = (
                    wrap_raw_ssml_unit(unit, language) if unit.startswith("<") else unit
                )
                async for audio_chunk in self._stream_sentence_audio(
                    sentence, voice, language, prosody_options, allow_raw_ssml
                ):
                    yield audio_chunk

        async for text_chunk in message_gen:
            if not text_chunk:
                continue
            ssml_buffer += text_chunk

            if not saw_speak_open and RAW_SSML_SPEAK_OPEN_RE.search(ssml_buffer):
                saw_speak_open = True
                open_match = RAW_SSML_SPEAK_OPEN_RE.search(ssml_buffer)
                if open_match:
                    ssml_buffer = ssml_buffer[open_match.end() :]

            if saw_speak_open:
                async for audio_chunk in flush_complete_ssml_units():
                    yield audio_chunk

        if not saw_speak_open:
            plain_text = ssml_buffer.strip()
            if plain_text:
                async for audio_chunk in self._stream_sentence_audio(
                    plain_text, voice, language, prosody_options, allow_raw_ssml
                ):
                    yield audio_chunk
            return

        async for audio_chunk in flush_complete_ssml_units():
            yield audio_chunk

        trailing_content = RAW_SSML_SPEAK_CLOSE_RE.sub("", ssml_buffer).strip()
        if not trailing_content:
            return

        sentence = (
            wrap_raw_ssml_unit(trailing_content, language)
            if trailing_content.startswith("<")
            else trailing_content
        )
        async for audio_chunk in self._stream_sentence_audio(
            sentence, voice, language, prosody_options, allow_raw_ssml
        ):
            yield audio_chunk

    async def _stream_text_message(
        self,
        message_gen: AsyncGenerator[str],
        voice: str,
        language: str,
        prosody_options: dict[str, str],
        allow_raw_ssml: bool,
    ) -> AsyncGenerator[bytes]:
        """Stream plain text content using sentence segmentation."""
        sentence_buffer = ""

        async for text_chunk in message_gen:
            if not text_chunk:
                continue

            sentence_buffer += text_chunk

            while match := SENTENCE_ENDINGS.search(sentence_buffer):
                sentence_end = match.end()
                sentence = sentence_buffer[:sentence_end].strip()
                sentence_buffer = sentence_buffer[sentence_end:]
                if not sentence:
                    continue
                async for audio_chunk in self._stream_sentence_audio(
                    sentence, voice, language, prosody_options, allow_raw_ssml
                ):
                    yield audio_chunk

            if len(sentence_buffer) >= STREAM_FORCE_FLUSH_CHARS:
                split_idx = sentence_buffer.rfind(" ", 0, STREAM_FORCE_FLUSH_CHARS)
                if split_idx <= 0:
                    split_idx = STREAM_FORCE_FLUSH_CHARS
                partial_sentence = sentence_buffer[:split_idx].strip()
                sentence_buffer = sentence_buffer[split_idx:]
                if partial_sentence:
                    async for audio_chunk in self._stream_sentence_audio(
                        partial_sentence,
                        voice,
                        language,
                        prosody_options,
                        allow_raw_ssml,
                    ):
                        yield audio_chunk

        remaining_text = sentence_buffer.strip()
        if remaining_text:
            async for audio_chunk in self._stream_sentence_audio(
                remaining_text, voice, language, prosody_options, allow_raw_ssml
            ):
                yield audio_chunk

    async def async_get_tts_audio(
        self, message: str, language: str, options: dict | None = None
    ) -> TtsAudioType:
        """Load TTS from Azure (legacy method, non-streaming)."""
        if options is None:
            options = {}

        voice, lang_to_use, prosody_options, allow_raw_ssml = (
            self._prepare_synthesis_context(language, options)
        )

        xml_doc = self._build_validated_ssml(
            message, voice, lang_to_use, prosody_options, allow_raw_ssml
        )
        data = await self._async_synthesize_audio_bytes(
            ssml=xml_doc,
            retries=0,
            error_context="legacy request",
        )
        if data is None:
            return None, None

        file_extension = _get_file_extension_from_format(self._output_format)
        return file_extension, data

    async def async_stream_tts_audio(
        self, request: TTSAudioRequest
    ) -> TTSAudioResponse:
        """Stream TTS audio from Azure using sentence-by-sentence synthesis."""
        options = request.options or {}
        voice, lang_to_use, prosody_options, allow_raw_ssml = (
            self._prepare_synthesis_context(request.language, options)
        )

        async def data_gen() -> AsyncGenerator[bytes]:
            try:
                message_gen = getattr(request, "message_gen", None)
                if message_gen is None:
                    full_message = getattr(request, "message", "").strip()
                    if full_message:
                        async for audio_chunk in self._stream_sentence_audio(
                            full_message,
                            voice,
                            lang_to_use,
                            prosody_options,
                            allow_raw_ssml,
                        ):
                            yield audio_chunk
                    return

                if allow_raw_ssml:
                    async for audio_chunk in self._stream_raw_ssml_message(
                        message_gen,
                        voice,
                        lang_to_use,
                        prosody_options,
                        allow_raw_ssml,
                    ):
                        yield audio_chunk
                    return

                async for audio_chunk in self._stream_text_message(
                    message_gen,
                    voice,
                    lang_to_use,
                    prosody_options,
                    allow_raw_ssml,
                ):
                    yield audio_chunk
            except Exception as ex:
                _LOGGER.error("Unexpected error in streaming TTS: %s", ex)
                raise

        file_extension = _get_file_extension_from_format(self._output_format)
        return TTSAudioResponse(extension=file_extension, data_gen=data_gen())
