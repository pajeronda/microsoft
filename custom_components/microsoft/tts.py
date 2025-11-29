"""Support for Microsoft Text-to-Speech (TTS)."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any
from xml.sax.saxutils import escape

import aiohttp
import voluptuous as vol

from homeassistant.components.tts import (
    ATTR_VOICE,
    TextToSpeechEntity,
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
    DEFAULT_OUTPUT_FORMAT,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


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
            configuration_url="https://portal.azure.com",
        )

    async def async_fetch_voices(self):
        """Fetch available voices from Azure."""
        # Check global cache first
        if cached := self.hass.data.get(DOMAIN, {}).get("voices"):
            self._voices_data = cached
            _LOGGER.debug("Used cached voices")
            return

        url = f"https://{self._region}.tts.speech.microsoft.com/cognitiveservices/voices/list"
        headers = {"Ocp-Apim-Subscription-Key": self._apikey}
        try:
            async with self._session.get(url, headers=headers) as response:
                if response.status == 200:
                    self._voices_data = await response.json()
                    # Update global cache
                    self.hass.data.setdefault(DOMAIN, {})["voices"] = self._voices_data
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
        ]

    @property
    def default_options(self) -> Mapping[str, Any]:
        """Return a mapping with the default options."""
        return {
            ATTR_VOICE: self._default_voice,
        }

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
        """Load TTS from Azure."""
        if options is None:
            options = {}

        # 1. Voice selection
        voice = options.get(ATTR_VOICE, self._default_voice)

        # 2. Resolve Azure Locale
        # HA might pass 'it-it', Azure needs 'it-IT'
        azure_locale = self._find_azure_locale(language)

        # If we couldn't resolve it exactly, use the requested one (fallback)
        # or if we found it, use the correct case.
        lang_to_use = azure_locale if azure_locale else language

        # 3. Smart Voice Fallback (Language Mismatch)
        # If the requested language (normalized) is different from default,
        # and no specific voice was requested, try to pick a voice for that language.
        if self._voices_data and azure_locale:
            # Check if current 'voice' fits the requested language
            # (Simple check: retrieve voice details if possible, or just trust options)
            if ATTR_VOICE not in options and self._language.lower() != language.lower():
                # The user didn't pick a voice, and changed language.
                # Pick a compatible female voice for the new language.
                for v in self._voices_data:
                    if v["Locale"] == azure_locale:
                        if v["Gender"] == "Female":
                            voice = v["ShortName"]
                            break
                        # Fallback to first found if no female
                        voice = v["ShortName"]

        # 4. Options (Priority: Service Call > Config Options > Default)
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

        # Construct SSML with namespace for mstts
        xml_doc = (
            f"<speak version='1.0' xmlns:mstts='https://www.w3.org/2001/mstts' xml:lang='{lang_to_use}'>"
            f"<voice xml:lang='{lang_to_use}' name='{voice}'>"
        )

        # Logic for express-as
        is_express_as = False
        if style:
            is_express_as = True
            xml_doc += f"<mstts:express-as style='{style}'"
            if role:
                xml_doc += f" role='{role}'"
            if style_degree:
                xml_doc += f" styledegree='{style_degree}'"
            xml_doc += ">"

        # Prosody wrapping text
        xml_doc += f"<prosody rate='{rate}' pitch='{pitch}' volume='{volume}'>"
        xml_doc += escape(message)
        xml_doc += "</prosody>"

        if is_express_as:
            xml_doc += "</mstts:express-as>"

        xml_doc += "</voice></speak>"

        headers = {
            "Ocp-Apim-Subscription-Key": self._apikey,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": self._output_format,
            "User-Agent": "HomeAssistant-MicrosoftAzureTTS",
        }

        url = f"https://{self._region}.tts.speech.microsoft.com/cognitiveservices/v1"

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

        return "mp3", data
        """Load TTS from Azure."""
        if options is None:
            options = {}

        # 1. Voice selection priority:
        #    a) 'voice' option passed in service call (e.g. automations)
        #    b) Default voice configured in Options/Config Flow
        voice = options.get(ATTR_VOICE, self._default_voice)

        # 2. Handle language mismatch fallback
        #    If automation asks for 'it-IT' but default is 'en-US', verify voice compatibility
        if self._voices_data and language:
            # Check if selected 'voice' supports the requested 'language'
            # This is expensive to check every time, but safer.
            # Simplified: if language changed and voice wasn't explicit, try to find a match.
            if language != self._language and ATTR_VOICE not in options:
                fallback_found = False
                for v in self._voices_data:
                    if v["Locale"].lower() == language.lower():
                        if v["Gender"] == "Female":
                            voice = v["ShortName"]
                            fallback_found = True
                            break
                        if not fallback_found:
                            voice = v["ShortName"]  # Take any if no female found yet
                            fallback_found = True

        # 3. Options
        rate = options.get(CONF_RATE, self._config_entry.options.get(CONF_RATE, "0%"))
        pitch = options.get(CONF_PITCH, "default")

        # Smart Rate Handling (0.87 -> -13%)
        if isinstance(rate, (int, float)):
            rate_val = float(rate)
            if 0.1 <= abs(rate_val) <= 3.0 and isinstance(rate, float):
                percent = int((rate_val - 1.0) * 100)
                rate = f"{'+' if percent >= 0 else ''}{percent}%"
            else:
                rate = f"{int(rate)}%"

        # SSML
        lang = language or self._language

        xml_doc = (
            f"<speak version='1.0' xml:lang='{lang}'>"
            f"<voice xml:lang='{lang}' name='{voice}'>"
            f"<prosody rate='{rate}' pitch='{pitch}'>"
            f"{escape(message)}"
            f"</prosody></voice></speak>"
        )

        headers = {
            "Ocp-Apim-Subscription-Key": self._apikey,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": self._output_format,
            "User-Agent": "HomeAssistant-NeuralAzureTTS",
        }

        url = f"https://{self._region}.tts.speech.microsoft.com/cognitiveservices/v1"

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
            _LOGGER.error("Error occurred for Neural Azure TTS: %s", ex)
            return None, None

        return "mp3", data
