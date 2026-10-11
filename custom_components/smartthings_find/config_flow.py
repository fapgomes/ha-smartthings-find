"""Config flow for SmartThings Find (Samsung Account or browser Cookie header)."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .account import (
    AccountAuthError,
    AccountGrant,
    AccountLoginExpiredError,
    AccountRedirectError,
    PendingLogin,
    async_complete_login,
    async_start_login,
    async_web_login,
)
from .api import (
    BASE_URL,
    StfAuthError,
    StfClient,
    StfConnectionError,
    StfDevice,
    StfError,
    create_session,
    parse_cookie_header,
)
from .const import (
    AUTH_METHOD_ACCOUNT,
    AUTH_METHOD_COOKIE,
    CONF_ACCOUNT,
    CONF_ACTIVE_INTERVAL,
    CONF_ACTIVE_MODE_OTHERS,
    CONF_ACTIVE_MODE_SMARTTAGS,
    CONF_AUTH_METHOD,
    CONF_COOKIE,
    CONF_KEEPALIVE_INTERVAL,
    CONF_MAX_LOCATION_AGE,
    CONF_UPDATE_INTERVAL,
    DEFAULT_ACTIVE_INTERVAL,
    DEFAULT_ACTIVE_MODE_OTHERS,
    DEFAULT_ACTIVE_MODE_SMARTTAGS,
    DEFAULT_KEEPALIVE_INTERVAL,
    DEFAULT_MAX_LOCATION_AGE,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    MIN_ACTIVE_INTERVAL,
    MIN_KEEPALIVE_INTERVAL,
    MIN_UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

# Form field names differ from the stored option keys (upstream compatible).
_FIELD_MODE_SMARTTAGS = "mode_smarttags"
_FIELD_REDIRECT_URI = "redirect_uri"
_FIELD_MODE_OTHERS = "mode_others"
_MODE_PASSIVE = "passive"
_MODE_ACTIVE = "active"

_COOKIE_SELECTOR = selector.TextSelector(
    selector.TextSelectorConfig(multiline=True, type=selector.TextSelectorType.TEXT)
)
_REDIRECT_SELECTOR = selector.TextSelector(
    selector.TextSelectorConfig(multiline=True, type=selector.TextSelectorType.TEXT)
)
_MODE_SELECTOR = selector.SelectSelector(
    selector.SelectSelectorConfig(
        options=[_MODE_PASSIVE, _MODE_ACTIVE],
        mode=selector.SelectSelectorMode.DROPDOWN,
        translation_key="mode",
    )
)


async def async_validate_cookie(cookie_line: str) -> list[StfDevice]:
    """Check a cookie against SmartThings Find; return the account's devices."""
    cookies = parse_cookie_header(cookie_line)
    if not cookies:
        raise StfAuthError("empty cookie")
    session = create_session(cookies)
    try:
        client = StfClient(session)
        await client.check_login()
        return await client.get_devices()
    finally:
        await session.close()


async def _cookie_errors(cookie_line: str) -> dict[str, str]:
    try:
        devices = await async_validate_cookie(cookie_line)
    except StfAuthError:
        return {"base": "invalid_auth"}
    except StfConnectionError:
        return {"base": "cannot_connect"}
    except StfError as err:
        _LOGGER.warning("Unexpected SmartThings Find response: %s", err)
        return {"base": "unknown"}
    return {} if devices else {"base": "no_devices"}


async def async_validate_grant(grant: AccountGrant) -> list[StfDevice]:
    """Sign a web session in with the grant; return the account's devices."""
    session = create_session({})
    try:
        await async_web_login(session, grant)
        client = StfClient(session)
        await client.check_login()
        return await client.get_devices()
    finally:
        await session.close()


def _settings_schema(options: Mapping[str, Any]) -> dict[Any, Any]:
    def mode(key: str, default: bool) -> str:
        return _MODE_ACTIVE if options.get(key, default) else _MODE_PASSIVE

    return {
        vol.Required(
            CONF_UPDATE_INTERVAL,
            default=options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
        ): vol.All(vol.Coerce(int), vol.Range(min=MIN_UPDATE_INTERVAL, max=86400)),
        vol.Required(
            CONF_KEEPALIVE_INTERVAL,
            default=options.get(CONF_KEEPALIVE_INTERVAL, DEFAULT_KEEPALIVE_INTERVAL),
        ): vol.All(vol.Coerce(int), vol.Range(min=MIN_KEEPALIVE_INTERVAL, max=86400)),
        vol.Required(
            _FIELD_MODE_SMARTTAGS,
            default=mode(CONF_ACTIVE_MODE_SMARTTAGS, DEFAULT_ACTIVE_MODE_SMARTTAGS),
        ): _MODE_SELECTOR,
        vol.Required(
            _FIELD_MODE_OTHERS,
            default=mode(CONF_ACTIVE_MODE_OTHERS, DEFAULT_ACTIVE_MODE_OTHERS),
        ): _MODE_SELECTOR,
        vol.Required(
            CONF_ACTIVE_INTERVAL,
            default=options.get(CONF_ACTIVE_INTERVAL, DEFAULT_ACTIVE_INTERVAL),
        ): vol.All(vol.Coerce(int), vol.Range(min=MIN_ACTIVE_INTERVAL, max=86400)),
        vol.Required(
            CONF_MAX_LOCATION_AGE,
            default=options.get(CONF_MAX_LOCATION_AGE, DEFAULT_MAX_LOCATION_AGE),
        ): vol.All(vol.Coerce(int), vol.Range(min=0, max=10080)),
    }


def _options_from_input(user_input: Mapping[str, Any]) -> dict[str, Any]:
    return {
        CONF_UPDATE_INTERVAL: int(user_input[CONF_UPDATE_INTERVAL]),
        CONF_KEEPALIVE_INTERVAL: int(user_input[CONF_KEEPALIVE_INTERVAL]),
        CONF_ACTIVE_MODE_SMARTTAGS: user_input[_FIELD_MODE_SMARTTAGS] == _MODE_ACTIVE,
        CONF_ACTIVE_MODE_OTHERS: user_input[_FIELD_MODE_OTHERS] == _MODE_ACTIVE,
        CONF_ACTIVE_INTERVAL: int(user_input[CONF_ACTIVE_INTERVAL]),
        CONF_MAX_LOCATION_AGE: int(user_input[CONF_MAX_LOCATION_AGE]),
    }


def _cookie_data(cookie_line: str) -> dict[str, Any]:
    return {CONF_AUTH_METHOD: AUTH_METHOD_COOKIE, CONF_COOKIE: cookie_line.strip()}


def _account_data(grant: AccountGrant) -> dict[str, Any]:
    return {CONF_AUTH_METHOD: AUTH_METHOD_ACCOUNT, CONF_ACCOUNT: grant.as_dict()}


_PLACEHOLDERS = {"stf_url": str(BASE_URL).rstrip("/")}


class StfConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up one SmartThings Find account."""

    VERSION = 1

    def __init__(self) -> None:
        # Entry being re-authenticated or reconfigured (None when creating).
        self._entry: ConfigEntry | None = None
        self._pending: PendingLogin | None = None
        self._login_url = ""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        # Only one account for now: entities are keyed by device id alone.
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        return self.async_show_menu(step_id="user", menu_options=["account", "cookie"])

    async def async_step_reauth(
        self, _entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        self._entry = self._get_reauth_entry()
        return self.async_show_menu(
            step_id="reauth_confirm", menu_options=["account", "cookie"]
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        self._entry = self._get_reconfigure_entry()
        return self.async_show_menu(
            step_id="reconfigure", menu_options=["account", "cookie"]
        )

    def _finish(
        self, data: dict[str, Any], user_input: Mapping[str, Any]
    ) -> ConfigFlowResult:
        if self._entry is not None:
            return self.async_update_reload_and_abort(self._entry, data=data)
        return self.async_create_entry(
            title="SmartThings Find", data=data, options=_options_from_input(user_input)
        )

    def _schema(self, fields: dict[Any, Any]) -> vol.Schema:
        """The step's own fields, plus the settings when creating the entry."""
        if self._entry is None:
            fields = {**fields, **_settings_schema({})}
        return vol.Schema(fields)

    async def async_step_cookie(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await _cookie_errors(user_input[CONF_COOKIE])
            if not errors:
                return self._finish(_cookie_data(user_input[CONF_COOKIE]), user_input)
        return self.async_show_form(
            step_id="cookie",
            data_schema=self._schema({vol.Required(CONF_COOKIE): _COOKIE_SELECTOR}),
            description_placeholders=_PLACEHOLDERS,
            errors=errors,
        )

    async def _async_new_login(self) -> None:
        country = (self.hass.config.country or "US").lower()
        language = self.hass.config.language or "en"
        locale = language if "-" in language else f"{language}-{country.upper()}"
        # Re-authentication keeps the installation id Samsung already knows.
        previous = (
            AccountGrant.from_dict(self._entry.data.get(CONF_ACCOUNT))
            if self._entry is not None
            else None
        )
        session = create_session({})
        try:
            self._login_url, self._pending = await async_start_login(
                session,
                country,
                locale,
                device_id=previous.device_id if previous else None,
            )
        finally:
            await session.close()

    async def async_step_account(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None and self._pending is not None:
            errors, grant = await self._async_account_errors(
                self._pending, str(user_input.get(_FIELD_REDIRECT_URI) or "")
            )
            if grant is not None:
                return self._finish(_account_data(grant), user_input)
            if errors.get("base") != "invalid_redirect":
                # A failed exchange spends the code: the next try needs a new link.
                self._pending = None

        if self._pending is None or self._pending.expired:
            try:
                await self._async_new_login()
            except StfError as err:
                _LOGGER.warning("Cannot start the Samsung Account sign-in: %s", err)
                return self.async_abort(reason="cannot_connect")

        return self.async_show_form(
            step_id="account",
            data_schema=self._schema(
                {vol.Required(_FIELD_REDIRECT_URI): _REDIRECT_SELECTOR}
            ),
            description_placeholders={"login_url": self._login_url},
            errors=errors,
        )

    @staticmethod
    async def _async_account_errors(
        pending: PendingLogin, redirect_uri: str
    ) -> tuple[dict[str, str], AccountGrant | None]:
        session = create_session({})
        try:
            grant = await async_complete_login(session, pending, redirect_uri)
        except AccountLoginExpiredError:
            return {"base": "login_expired"}, None
        except AccountRedirectError:
            return {"base": "invalid_redirect"}, None
        except AccountAuthError:
            return {"base": "account_rejected"}, None
        except StfConnectionError:
            return {"base": "cannot_connect"}, None
        except StfError as err:
            _LOGGER.warning("Unexpected Samsung Account response: %s", err)
            return {"base": "unknown"}, None
        finally:
            await session.close()

        try:
            devices = await async_validate_grant(grant)
        except StfAuthError:
            return {"base": "account_rejected"}, None
        except StfConnectionError:
            return {"base": "cannot_connect"}, None
        except StfError as err:
            _LOGGER.warning("Unexpected SmartThings Find response: %s", err)
            return {"base": "unknown"}, None
        if not devices:
            return {"base": "no_devices"}, None
        return {}, grant

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return StfOptionsFlow()


class StfOptionsFlow(OptionsFlow):
    """Polling options, with an optional cookie replacement for cookie entries."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self.config_entry
        cookie_entry = (
            entry.data.get(CONF_AUTH_METHOD, AUTH_METHOD_COOKIE) == AUTH_METHOD_COOKIE
        )
        errors: dict[str, str] = {}
        if user_input is not None:
            cookie_line = str(user_input.get(CONF_COOKIE) or "").strip()
            if cookie_line and cookie_line != entry.data.get(CONF_COOKIE):
                errors = await _cookie_errors(cookie_line)
                if not errors:
                    # Data and options in one update, so the entry reloads once.
                    self.hass.config_entries.async_update_entry(
                        entry,
                        data=_cookie_data(cookie_line),
                        options=_options_from_input(user_input),
                    )
            if not errors:
                return self.async_create_entry(data=_options_from_input(user_input))

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    **({vol.Optional(CONF_COOKIE): _COOKIE_SELECTOR} if cookie_entry else {}),
                    **_settings_schema(entry.options),
                }
            ),
            description_placeholders=_PLACEHOLDERS,
            errors=errors,
        )
