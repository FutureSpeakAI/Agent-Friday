"""
agent_friday.core.auth — HTTP authentication, session handling, and login throttle.

The login throttle state is persisted in SQLite so failed-attempt counts survive
server restarts (preventing brute-force via restart-cycling).

Import from here for auth-only concerns::

    from agent_friday.core.auth import login_required, _login_attempt_ok
"""

from agent_friday.core import (  # noqa: F401
    FRIDAY_USERNAME,
    FRIDAY_PASSWORD,
    FRIDAY_TRUST_LOOPBACK,
    FRIDAY_WS_TOKEN,
    _HTTP_AUTH_KEY,
    _API_SESSION_TOKEN,
    _current_api_token,
    _api_token_valid,
    _LOGIN_LOCK,
    _LOGIN_MAX,
    _LOGIN_WINDOW,
    _LOOPBACK_ADDRS,
    _is_local_request,
    _loopback_trusted,
    _login_attempt_ok,
    _login_attempt_fail,
    _login_attempt_reset,
    _get_throttle_db,
    login_required,
    LOGIN_HTML,
)
