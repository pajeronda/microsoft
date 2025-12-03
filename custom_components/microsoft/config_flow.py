"""Config flow for Microsoft Text-to-Speech (TTS) integration."""

from __future__ import annotations

import logging
import time
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_API_KEY, CONF_LANGUAGE
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv

from .const import (
    CONF_REGION,
    CONF_VOICE,
    CONF_OUTPUT_FORMAT,
    DEFAULT_OUTPUT_FORMAT,
    DOMAIN,
    CONF_RATE,
    CONF_PITCH,
    CONF_VOLUME,
    CONF_STYLE,
    CONF_STYLE_DEGREE,
    CONF_ROLE,
    VOICES_CACHE_TTL,
    AZURE_VOICES_LIST_URL,
    AZURE_SPEECH_REGIONS,
    CONF_REGION_DROPDOWN,
    CONF_REGION_CUSTOM,
    AUDIO_FORMATS,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_API_KEY): cv.string,
        vol.Optional(CONF_REGION_DROPDOWN, default=""): vol.In(
            [""] + AZURE_SPEECH_REGIONS
        ),
        vol.Optional(CONF_REGION_CUSTOM, default=""): cv.string,
    }
)


async def get_voices(hass: HomeAssistant, key: str, region: str) -> list[dict]:
    """Fetch voices from Azure."""
    # Check cache first
    cache_data = hass.data.get(DOMAIN, {}).get("voices_cache")
    if cache_data:
        cached_voices, cached_time = cache_data
        if time.time() - cached_time < VOICES_CACHE_TTL:
            _LOGGER.debug("Using cached voices (age: %.0fs)", time.time() - cached_time)
            return cached_voices

    session = async_get_clientsession(hass)
    url = AZURE_VOICES_LIST_URL.format(region=region)
    headers = {"Ocp-Apim-Subscription-Key": key}

    try:
        async with session.get(url, headers=headers) as response:
            if response.status == 200:
                voices = await response.json()
                # Cache the result with timestamp
                hass.data.setdefault(DOMAIN, {})["voices_cache"] = (voices, time.time())
                _LOGGER.debug("Fetched and cached %d voices", len(voices))
                return voices
    except Exception as ex:
        _LOGGER.error("Error fetching voices: %s", ex)
    return []


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Microsoft Text-to-Speech (TTS)."""

    VERSION = 1

    def __init__(self):
        """Initialize."""
        self._data = {}
        self._voices = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            # Hybrid region selection: dropdown OR custom text field
            region_dropdown = user_input.get(CONF_REGION_DROPDOWN, "").strip()
            region_custom = user_input.get(CONF_REGION_CUSTOM, "").strip()

            # Priority: custom field > dropdown
            final_region = region_custom if region_custom else region_dropdown

            if not final_region:
                errors["base"] = "region_required"
            else:
                # Store the final region in CONF_REGION for backwards compatibility
                user_input[CONF_REGION] = final_region

                self._voices = await get_voices(
                    self.hass, user_input[CONF_API_KEY], final_region
                )
                if not self._voices:
                    errors["base"] = "cannot_connect"
                else:
                    self._data.update(user_input)
                    return await self.async_step_language()

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    async def async_step_language(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step to select language."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_voice()

        languages = sorted(list({v["Locale"] for v in self._voices}))
        default_lang = "it-IT"
        # Try to find a smart default
        for l in languages:
            if l.startswith(self.hass.config.language):
                default_lang = l
                break

        return self.async_show_form(
            step_id="language",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_LANGUAGE, default=default_lang): vol.In(
                        languages
                    ),
                }
            ),
        )

    async def async_step_voice(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step to select voice model."""
        if user_input is not None:
            self._data.update(user_input)
            return self.async_create_entry(
                title="Microsoft Text-to-Speech (TTS)", data=self._data
            )

        selected_lang = self._data[CONF_LANGUAGE]
        voices_list = {
            v["ShortName"]: f"{v['LocalName']} ({v['Gender']})"
            for v in self._voices
            if v["Locale"] == selected_lang
        }

        return self.async_show_form(
            step_id="voice",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_VOICE): vol.In(voices_list),
                }
            ),
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Create the options flow."""
        return OptionsFlowHandler()


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow."""

    def __init__(self) -> None:
        """Initialize options flow."""
        self._data = {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options - Language selection."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_voice()

        # Get current Language
        current_lang = self.config_entry.options.get(
            CONF_LANGUAGE, self.config_entry.data.get(CONF_LANGUAGE)
        )

        # Re-fetch voices to get available languages
        key = self.config_entry.data[CONF_API_KEY]
        region = self.config_entry.data[CONF_REGION]
        voices = await get_voices(self.hass, key, region)

        # Get all languages for the language selector
        languages = sorted(list({v["Locale"] for v in voices}))
        if current_lang not in languages and languages:
            current_lang = languages[0]

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_LANGUAGE, default=current_lang): vol.In(
                        languages
                    ),
                }
            ),
        )

    async def async_step_voice(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step to select voice and other options based on chosen language."""
        if user_input is not None:
            self._data.update(user_input)
            return self.async_create_entry(title="", data=self._data)

        # Get the language selected in previous step
        selected_lang = self._data.get(CONF_LANGUAGE)

        # Get current values
        current_voice = self.config_entry.options.get(
            CONF_VOICE, self.config_entry.data.get(CONF_VOICE)
        )
        current_rate = self.config_entry.options.get(CONF_RATE, "0%")
        current_pitch = self.config_entry.options.get(CONF_PITCH, "default")
        current_volume = self.config_entry.options.get(CONF_VOLUME, "default")
        current_style = self.config_entry.options.get(CONF_STYLE, "")
        current_style_degree = self.config_entry.options.get(CONF_STYLE_DEGREE, "1")
        current_role = self.config_entry.options.get(CONF_ROLE, "")
        current_format = self.config_entry.options.get(
            CONF_OUTPUT_FORMAT, DEFAULT_OUTPUT_FORMAT
        )

        # Re-fetch voices
        key = self.config_entry.data[CONF_API_KEY]
        region = self.config_entry.data[CONF_REGION]
        voices = await get_voices(self.hass, key, region)

        # Filter voices for the SELECTED language
        voices_list = {
            v["ShortName"]: f"{v['LocalName']} ({v['Gender']})"
            for v in voices
            if v["Locale"] == selected_lang
        }

        # If current voice is not compatible with new language, pick first
        if current_voice not in voices_list and voices_list:
            current_voice = list(voices_list.keys())[0]

        return self.async_show_form(
            step_id="voice",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_VOICE, default=current_voice): vol.In(
                        voices_list
                    ),
                    vol.Optional(CONF_RATE, default=current_rate): cv.string,
                    vol.Optional(CONF_PITCH, default=current_pitch): cv.string,
                    vol.Optional(CONF_VOLUME, default=current_volume): cv.string,
                    vol.Optional(CONF_STYLE, default=current_style): cv.string,
                    vol.Optional(
                        CONF_STYLE_DEGREE, default=current_style_degree
                    ): cv.string,
                    vol.Optional(CONF_ROLE, default=current_role): cv.string,
                    vol.Optional(CONF_OUTPUT_FORMAT, default=current_format): vol.In(
                        AUDIO_FORMATS
                    ),
                }
            ),
        )
