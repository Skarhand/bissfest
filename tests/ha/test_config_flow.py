"""Tests für den Config-Flow."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.bissfest.const import CONF_SCAN_INTERVAL, DOMAIN

from .conftest import API, TOKEN, URL


async def test_user_flow_success(hass: HomeAssistant, aioclient_mock, status, mock_setup_entry) -> None:
    aioclient_mock.get(f"{API}/status", json=status)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"url": "Bissfest.Example.de/", "token": f" {TOKEN} "}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Bissfest – sebastian"
    assert result["data"] == {"url": URL, "token": TOKEN}
    assert result["result"].unique_id == f"{URL}#sebastian"
    assert aioclient_mock.mock_calls[0][3]["Authorization"] == f"Bearer {TOKEN}"


async def test_user_flow_errors(hass: HomeAssistant, aioclient_mock, status, mock_setup_entry) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"url": URL, "token": "falsch"})
    assert result["errors"] == {"token": "invalid_token_format"}

    for http_status, error in ((401, "invalid_auth"), (404, "api_not_found"), (429, "rate_limited"), (500, "cannot_connect")):
        aioclient_mock.clear_requests()
        aioclient_mock.get(f"{API}/status", status=http_status, json={"detail": "x"})
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"url": URL, "token": TOKEN})
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": error}, http_status

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{API}/status", exc=TimeoutError())
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"url": URL, "token": TOKEN})
    assert result["errors"] == {"base": "cannot_connect"}

    # Erholt sich nach Fehler
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{API}/status", json=status)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"url": URL, "token": TOKEN})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_already_configured(hass: HomeAssistant, aioclient_mock, status, config_entry) -> None:
    config_entry.add_to_hass(hass)
    aioclient_mock.get(f"{API}/status", json=status)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"url": URL, "token": TOKEN})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth(hass: HomeAssistant, aioclient_mock, status, config_entry, mock_setup_entry) -> None:
    config_entry.add_to_hass(hass)
    aioclient_mock.get(f"{API}/status", json=status)
    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"token": "bf_neu"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data["token"] == "bf_neu"


async def test_reauth_wrong_account(hass: HomeAssistant, aioclient_mock, status, config_entry) -> None:
    config_entry.add_to_hass(hass)
    aioclient_mock.get(f"{API}/status", json={**status, "user": "jemand"})
    result = await config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"token": "bf_neu"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert config_entry.data["token"] == TOKEN


async def test_options_flow(hass: HomeAssistant, aioclient_mock, status, config_entry) -> None:
    aioclient_mock.get(f"{API}/status", json=status)
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_SCAN_INTERVAL: 15})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert config_entry.options == {CONF_SCAN_INTERVAL: 15}
    assert config_entry.runtime_data.update_interval.total_seconds() == 900
