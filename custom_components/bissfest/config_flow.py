"""Config-Flow für Bissfest."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_TOKEN, CONF_URL
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    BissfestApiClient,
    BissfestAuthError,
    BissfestError,
    BissfestNotFoundError,
    BissfestRateLimitError,
    normalize_url,
)
from .const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    TOKEN_PREFIX,
)
from .coordinator import BissfestConfigEntry

_LOGGER = logging.getLogger(__name__)

TOKEN_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


def _unique_id(url: str, user: str) -> str:
    return f"{normalize_url(url)}#{user}"


class BissfestConfigFlow(ConfigFlow, domain=DOMAIN):
    """Bissfest-Konto per URL und API-Token verbinden."""

    VERSION = 1

    async def _validate(self, url: str, token: str) -> tuple[dict[str, Any] | None, dict[str, str]]:
        """Token gegen /api/v1/status prüfen. Gibt (Status, Fehler) zurück."""
        if not token.startswith(TOKEN_PREFIX):
            return None, {CONF_TOKEN: "invalid_token_format"}
        client = BissfestApiClient(async_get_clientsession(self.hass), url, token)
        try:
            status = await client.get_status()
        except BissfestAuthError:
            return None, {"base": "invalid_auth"}
        except BissfestNotFoundError:
            return None, {"base": "api_not_found"}
        except BissfestRateLimitError:
            return None, {"base": "rate_limited"}
        except BissfestError:
            return None, {"base": "cannot_connect"}
        except Exception:
            _LOGGER.exception("Unerwarteter Fehler bei der Prüfung")
            return None, {"base": "unknown"}
        if not status.get("user"):
            return None, {"base": "api_not_found"}
        return status, {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            url = normalize_url(user_input[CONF_URL])
            token = user_input[CONF_TOKEN].strip()
            status, errors = await self._validate(url, token)
            if status is not None:
                await self.async_set_unique_id(_unique_id(url, status["user"]))
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Bissfest – {status['user']}",
                    data={CONF_URL: url, CONF_TOKEN: token},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(CONF_URL): TextSelector(
                            TextSelectorConfig(type=TextSelectorType.URL)
                        ),
                        vol.Required(CONF_TOKEN): TOKEN_SELECTOR,
                    }
                ),
                user_input or {},
            ),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            token = user_input[CONF_TOKEN].strip()
            status, errors = await self._validate(entry.data[CONF_URL], token)
            if status is not None:
                if _unique_id(entry.data[CONF_URL], status["user"]) != entry.unique_id:
                    return self.async_abort(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_TOKEN: token}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_TOKEN): TOKEN_SELECTOR}),
            description_placeholders={"url": entry.data[CONF_URL]},
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: BissfestConfigEntry) -> BissfestOptionsFlow:
        return BissfestOptionsFlow()


class BissfestOptionsFlow(OptionsFlow):
    """Abfrageintervall einstellen."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                data={CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL])}
            )

        current = self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SCAN_INTERVAL, default=current): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_SCAN_INTERVAL,
                            max=MAX_SCAN_INTERVAL,
                            step=1,
                            mode=NumberSelectorMode.BOX,
                            unit_of_measurement="min",
                        )
                    )
                }
            ),
        )
