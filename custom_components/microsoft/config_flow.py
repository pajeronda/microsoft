"""Config flow for Microsoft Text-to-Speech (TTS) integration."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
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
    DEFAULT_REGION,
    DEFAULT_OUTPUT_FORMAT,
    DOMAIN,
    CONF_RATE,
    CONF_PITCH,
    CONF_VOLUME,
    CONF_STYLE,
    CONF_STYLE_DEGREE,
    CONF_ROLE,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_API_KEY): cv.string,
        vol.Required(CONF_REGION, default=DEFAULT_REGION): cv.string,
    }
)


async def get_voices(hass: HomeAssistant, key: str, region: str) -> list[dict]:
    """Fetch voices from Azure."""
    # Check cache first
    if cached := hass.data.get(DOMAIN, {}).get("voices"):
        return cached

    session = async_get_clientsession(hass)
    url = f"https://{region}.tts.speech.microsoft.com/cognitiveservices/voices/list"
    headers = {"Ocp-Apim-Subscription-Key": key}

    try:
        async with session.get(url, headers=headers) as response:
            if response.status == 200:
                voices = await response.json()
                # Cache the result
                hass.data.setdefault(DOMAIN, {})["voices"] = voices
                return voices
    except Exception:
        pass
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
            self._voices = await get_voices(
                self.hass, user_input[CONF_API_KEY], user_input[CONF_REGION]
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

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        # Re-fetch voices to populate dropdown
        key = self.config_entry.data[CONF_API_KEY]
        region = self.config_entry.data[CONF_REGION]

        # Get current Language and Voice
        current_lang = self.config_entry.options.get(
            CONF_LANGUAGE, self.config_entry.data.get(CONF_LANGUAGE)
        )
        current_voice = self.config_entry.options.get(
            CONF_VOICE, self.config_entry.data.get(CONF_VOICE)
        )

        voices = await get_voices(self.hass, key, region)

        # Get all languages for the language selector
        languages = sorted(list({v["Locale"] for v in voices}))
        if current_lang not in languages and languages:
            # Fallback if current lang not found
            current_lang = languages[0]

        # Filter voices for the CURRENT language
        # Note: If user changes language in UI, they might need to save and re-open
        # to see voices for the new language, but we keep it single-step as requested.
        voices_list = {
            v["ShortName"]: f"{v['LocalName']} ({v['Gender']})"
            for v in voices
            if v["Locale"] == current_lang
        }

        # Ensure current voice is in the list (or handle mismatch if lang changed externally)
        if current_voice not in voices_list:
            # If the voice doesn't match the language, we still default to it
            # to avoid validation errors, or we could pick the first one.
            # But usually we just let it be, and the user will pick a new one or save.
            pass

        current_rate = self.config_entry.options.get(CONF_RATE, "0%")
        current_pitch = self.config_entry.options.get(CONF_PITCH, "default")
        current_volume = self.config_entry.options.get(CONF_VOLUME, "default")
        current_style = self.config_entry.options.get(CONF_STYLE, "")
        current_style_degree = self.config_entry.options.get(CONF_STYLE_DEGREE, "1")
        current_role = self.config_entry.options.get(CONF_ROLE, "")
        current_format = self.config_entry.options.get(
            CONF_OUTPUT_FORMAT, DEFAULT_OUTPUT_FORMAT
        )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_LANGUAGE, default=current_lang): vol.In(
                        languages
                    ),
                    vol.Optional(CONF_VOICE, default=current_voice): vol.In(
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
                    vol.Optional(CONF_OUTPUT_FORMAT, default=current_format): cv.string,
                }
            ),
        )
