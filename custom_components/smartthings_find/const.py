"""Constants for SmartThings Find."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "smartthings_find"

# Config entry data. ``auth_method`` is kept for entries created by upstream
# releases; only the cookie method is supported.
CONF_AUTH_METHOD: Final = "auth_method"
CONF_COOKIE: Final = "cookie"
AUTH_METHOD_COOKIE: Final = "cookie"

# Options (key names kept compatible with upstream v1.4.x entries).
CONF_UPDATE_INTERVAL: Final = "update_interval"
CONF_KEEPALIVE_INTERVAL: Final = "keepalive_interval"
CONF_ACTIVE_MODE_SMARTTAGS: Final = "active_mode_smarttags"
CONF_ACTIVE_MODE_OTHERS: Final = "active_mode_others"

DEFAULT_UPDATE_INTERVAL: Final = 120
DEFAULT_KEEPALIVE_INTERVAL: Final = 180
DEFAULT_ACTIVE_MODE_SMARTTAGS: Final = False
DEFAULT_ACTIVE_MODE_OTHERS: Final = False

MIN_UPDATE_INTERVAL: Final = 30
MIN_KEEPALIVE_INTERVAL: Final = 60

# Consecutive rejected update cycles before asking for a new cookie. A single
# ``Logout`` body is occasionally transient.
AUTH_FAILURES_BEFORE_REAUTH: Final = 3

# Delays (seconds) between chkLogin.do attempts while setting up.
SETUP_AUTH_RETRY_DELAYS: Final[tuple[int, ...]] = (2, 5, 15)

# Delays (seconds) between getOperationResult.do polls after a location
# request. The web client waits up to 30 s for CHECK_CONNECTION_WITH_LOCATION.
LOCATION_RESULT_POLL_DELAYS: Final[tuple[int, ...]] = (3, 5, 7, 10, 10)

RING_MESSAGE: Final = "Home Assistant is ringing your device!"

SIGNAL_NEW_DEVICES: Final = f"{DOMAIN}_new_devices_{{entry_id}}"

REQUEST_PENDING: Final = "pending"
REQUEST_OK: Final = "ok"
REQUEST_TIMEOUT: Final = "timeout"
REQUEST_FAILED: Final = "failed"
