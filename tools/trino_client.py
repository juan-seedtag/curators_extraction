"""
Trino connection using the RefreshingGoogleJWT auth pattern.

The Google OAuth token lives in `~/.config/seedtag/token.json` (outside the
project tree so it can't be shared/synced accidentally; a project-root
`token.json` is still honoured as a fallback). It is refreshed automatically on
every HTTP request, so no browser login is needed once a valid token exists.

If the token is missing, run `python -m tools.trino_client --login` once to
create it via the browser OAuth flow (requires `credentials.json`).
"""

from __future__ import annotations

import os
import threading
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import google.auth.transport.requests
from google.oauth2.credentials import Credentials
from requests.auth import AuthBase
from sqlalchemy import create_engine
from trino.auth import Authentication

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = Path(os.getenv("SEEDTAG_CONFIG_DIR", Path.home() / ".config" / "seedtag"))


def _resolve_secret(filename: str, env_var: str) -> Path:
    """Locate a credential file: $ENV_VAR → ~/.config/seedtag/ → project root."""
    if os.getenv(env_var):
        return Path(os.environ[env_var])
    cfg = CONFIG_DIR / filename
    return cfg if cfg.exists() else PROJECT_ROOT / filename


TOKEN_PATH = _resolve_secret("token.json", "SEEDTAG_TOKEN_PATH")
CREDENTIALS_PATH = _resolve_secret("credentials.json", "SEEDTAG_CREDENTIALS_PATH")

TRINO_HOST = os.getenv("TRINO_HOST", "trino-users.seedt.ag")
TRINO_PORT = int(os.getenv("TRINO_PORT", "443"))
TRINO_USER = os.getenv("TRINO_USER", "juanperez@seedtag.com")
CATALOG = os.getenv("TRINO_CATALOG", "st_datalakehouse")
SCHEMA = os.getenv("TRINO_SCHEMA", "analytics")

# Scopes the token was minted with — kept identical so refresh never triggers a
# "scope has changed" error.
SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]


@lru_cache(maxsize=1)
def _credentials() -> Credentials:
    if not TOKEN_PATH.exists():
        raise FileNotFoundError(
            f"token.json not found at {TOKEN_PATH}. Copy one to {CONFIG_DIR}/token.json, "
            f"or run `python -m tools.trino_client --login` to create it."
        )
    # No scopes arg → inherit whatever the token file was created with, avoiding
    # scope-mismatch errors on refresh.
    return Credentials.from_authorized_user_file(str(TOKEN_PATH))


_token_lock = threading.Lock()


def _google_id_token() -> str:
    """Return a valid Google ID token, refreshing only when near expiry.

    Guarded by a lock so concurrent queries (parallel report build) don't race
    on the shared Credentials object.
    """
    creds = _credentials()
    with _token_lock:
        near_expiry = (
            creds.expiry is not None
            and (creds.expiry - datetime.utcnow()).total_seconds() < 300
        )
        # id_token (not the access token) is what Trino accepts; it is only
        # populated after a refresh, so force one if we don't have it yet.
        if getattr(creds, "id_token", None) is None or not creds.valid or near_expiry:
            creds.refresh(google.auth.transport.requests.Request())
        return creds.id_token


class _RefreshingBearerAuth(AuthBase):
    """Attaches a fresh Google ID token to every HTTP request (prevents JWT expiry)."""

    def __call__(self, r):
        r.headers["Authorization"] = "Bearer " + _google_id_token()
        return r


class RefreshingGoogleJWT(Authentication):
    def set_http_session(self, http_session):
        http_session.auth = _RefreshingBearerAuth()
        return http_session

    def get_exceptions(self):
        return ()


@lru_cache(maxsize=1)
def get_engine():
    return create_engine(
        f"trino://{TRINO_USER}@{TRINO_HOST}:{TRINO_PORT}/{CATALOG}/{SCHEMA}",
        connect_args={
            "auth": RefreshingGoogleJWT(),
            "http_scheme": "https",
            "schema": SCHEMA,
        },
    )


def run_trino_query(sql: str) -> list[dict]:
    """Execute SQL against Trino and return a list of dict rows."""
    engine = get_engine()
    with engine.connect() as conn:
        # exec_driver_sql bypasses SQLAlchemy bind-param parsing so ':' and '%'
        # inside the SQL are treated as literals.
        result = conn.exec_driver_sql(sql)
        cols = list(result.keys())
        return [dict(zip(cols, row)) for row in result.fetchall()]


def _login() -> None:
    """One-time browser OAuth flow to (re)create token.json."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    login_scopes = [
        "openid",
        "https://www.googleapis.com/auth/userinfo.email",
    ]
    if not CREDENTIALS_PATH.exists():
        raise FileNotFoundError(
            f"credentials.json (OAuth client secrets) required at {CREDENTIALS_PATH}."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), login_scopes)
    creds = flow.run_local_server(port=0)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    out = CONFIG_DIR / "token.json"
    out.write_text(creds.to_json(), encoding="utf-8")
    out.chmod(0o600)
    print(f"Saved {out}")


if __name__ == "__main__":
    import sys

    if "--login" in sys.argv:
        _login()
    else:
        print("Testing Trino connection…")
        rows = run_trino_query("SELECT 1 AS ok")
        print(rows)
