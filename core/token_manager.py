"""
Upstox daily token manager for TradeDesk.

Upstox access tokens expire at midnight IST each day.
Responsibilities:
  - Validate the current token with a live API call (/user/profile)
  - Semi-automated refresh: open browser → local OAuth callback server on
    port 8765 → exchange auth code → write new token back to .env
  - Module-level convenience functions used by startup and data feeds
"""

import logging
import os
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

import requests

try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(Path(".env"), override=False)
except Exception:
    pass

logger = logging.getLogger(__name__)

UPSTOX_AUTH_URL  = "https://api.upstox.com/v2/login/authorization/dialog"
UPSTOX_TOKEN_URL = "https://api.upstox.com/v2/login/authorization/token"
UPSTOX_PROFILE_URL = "https://api.upstox.com/v2/user/profile"

CALLBACK_PORT    = 8765
CALLBACK_PATH    = "/upstox/callback"
CALLBACK_TIMEOUT = 120  # seconds before giving up on browser login

ENV_PATH = Path(".env")

# Shared result container for the OAuth callback thread
_oauth_result: dict = {"code": None}


# ---------------------------------------------------------------------------
# Named exception
# ---------------------------------------------------------------------------

class TokenError(Exception):
    """Raised when a valid Upstox access token cannot be obtained."""


# ---------------------------------------------------------------------------
# Local OAuth2 callback HTTP server
# ---------------------------------------------------------------------------

class _CallbackHandler(BaseHTTPRequestHandler):
    """Single-request handler — captures ?code= from Upstox redirect."""

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != CALLBACK_PATH:
            self._reply(404, b"<html><body>Not found</body></html>")
            return

        params = parse_qs(parsed.query)
        code = params.get("code", [None])[0]

        if code:
            _oauth_result["code"] = code
            self._reply(
                200,
                b"<html><body style='font-family:sans-serif;padding:2em'>"
                b"<h2 style='color:#26a69a'>TradeDesk: Login successful.</h2>"
                b"<p>You can close this tab and return to the app.</p>"
                b"</body></html>",
            )
        else:
            error = params.get("error", ["unknown_error"])[0]
            self._reply(
                400,
                f"<html><body><h2>Login failed: {error}</h2></body></html>".encode(),
            )

    def _reply(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args) -> None:  # noqa: A002
        pass  # suppress default HTTPServer console noise


def _run_callback_server(stop_event: threading.Event) -> None:
    """Run local HTTP server until auth code arrives or stop is signalled."""
    try:
        server = HTTPServer(("127.0.0.1", CALLBACK_PORT), _CallbackHandler)
        server.timeout = 1.5
        while not stop_event.is_set() and _oauth_result["code"] is None:
            server.handle_request()
        server.server_close()
    except OSError as exc:
        logger.error(f"OAuth callback server failed to start on port {CALLBACK_PORT}: {exc}")
        stop_event.set()


# ---------------------------------------------------------------------------
# Token Manager
# ---------------------------------------------------------------------------

class TokenManager:
    """
    Manages the Upstox access token lifecycle.

    Expected .env keys:
      UPSTOX_API_KEY       — Upstox app API key (from developer.upstox.com)
      UPSTOX_API_SECRET    — Upstox app secret
      UPSTOX_REDIRECT_URI  — Registered redirect URI (default: http://127.0.0.1:8765/upstox/callback)
      UPSTOX_ACCESS_TOKEN  — Current token; updated automatically after each refresh
    """

    def __init__(self) -> None:
        self._api_key      = os.environ.get("UPSTOX_API_KEY", "")
        self._api_secret   = os.environ.get("UPSTOX_API_SECRET", "")
        self._redirect_uri = os.environ.get(
            "UPSTOX_REDIRECT_URI",
            f"http://127.0.0.1:{CALLBACK_PORT}{CALLBACK_PATH}",
        )
        self._token = os.environ.get("UPSTOX_ACCESS_TOKEN", "")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_access_token(self) -> str:
        """
        Return a valid Upstox access token.
        Triggers browser-based refresh if the stored token is missing or expired.
        Raises TokenError if a valid token cannot be obtained.
        """
        if self._token and self.check_token_valid(self._token):
            logger.info("Upstox access token is valid.")
            return self._token

        logger.info("Upstox token missing or expired — starting refresh flow.")
        self._token = self.refresh_token()
        return self._token

    def check_token_valid(self, token: Optional[str] = None) -> bool:
        """
        Validate a token by calling GET /user/profile.
        Returns True if HTTP 200, False for 401/403 or any network error.
        """
        t = token or self._token
        if not t:
            return False
        try:
            resp = requests.get(
                UPSTOX_PROFILE_URL,
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {t}",
                },
                timeout=8,
            )
            if resp.status_code == 200:
                return True
            logger.debug(f"Token check returned HTTP {resp.status_code}.")
            return False
        except requests.RequestException as exc:
            logger.warning(f"Token validity check failed (network): {exc}")
            return False

    def refresh_token(self) -> str:
        """
        Run semi-automated OAuth2 token refresh:
          1. Open Upstox login URL in default browser.
          2. Start local HTTP server on port CALLBACK_PORT.
          3. Wait up to CALLBACK_TIMEOUT seconds for ?code= redirect.
          4. Exchange code for access_token via POST.
          5. Persist new token to .env and os.environ.

        Returns the new access token string.
        Raises TokenError on missing credentials, timeout, or exchange failure.
        """
        if not self._api_key or not self._api_secret:
            raise TokenError(
                "UPSTOX_API_KEY and UPSTOX_API_SECRET must be set in .env "
                "before token refresh can proceed."
            )

        _oauth_result["code"] = None  # reset from any previous run

        auth_url = (
            f"{UPSTOX_AUTH_URL}"
            f"?response_type=code"
            f"&client_id={self._api_key}"
            f"&redirect_uri={self._redirect_uri}"
        )

        logger.info(f"Opening browser for Upstox login...")
        print(
            "\n[TradeDesk] Upstox token expired or missing. Opening browser for login...\n"
            f"  If the browser does not open, visit:\n  {auth_url}\n"
            f"  Waiting up to {CALLBACK_TIMEOUT} seconds...\n"
        )

        stop_event = threading.Event()
        server_thread = threading.Thread(
            target=_run_callback_server,
            args=(stop_event,),
            daemon=True,
            name="upstox-oauth-callback",
        )
        server_thread.start()

        webbrowser.open(auth_url)

        deadline = time.monotonic() + CALLBACK_TIMEOUT
        while _oauth_result["code"] is None:
            if time.monotonic() > deadline:
                stop_event.set()
                raise TokenError(
                    f"Upstox login timed out after {CALLBACK_TIMEOUT} seconds. "
                    "Please restart TradeDesk and complete login promptly."
                )
            if stop_event.is_set():
                raise TokenError(
                    "Upstox OAuth callback server failed to start. "
                    f"Ensure port {CALLBACK_PORT} is not in use."
                )
            time.sleep(0.5)

        stop_event.set()
        auth_code: str = _oauth_result["code"]
        _oauth_result["code"] = None

        logger.info("Auth code received. Exchanging for access token...")
        new_token = self._exchange_code(auth_code)

        self._save_token_to_env(new_token)
        self._token = new_token
        os.environ["UPSTOX_ACCESS_TOKEN"] = new_token
        logger.info("New Upstox access token obtained and saved.")
        return new_token

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _exchange_code(self, code: str) -> str:
        """POST auth code to Upstox token endpoint. Returns access_token."""
        try:
            resp = requests.post(
                UPSTOX_TOKEN_URL,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
                data={
                    "code": code,
                    "client_id": self._api_key,
                    "client_secret": self._api_secret,
                    "redirect_uri": self._redirect_uri,
                    "grant_type": "authorization_code",
                },
                timeout=15,
            )
            resp.raise_for_status()
        except requests.HTTPError as exc:
            try:
                body = exc.response.json()
            except Exception:
                body = exc.response.text[:300]
            raise TokenError(
                f"Token exchange failed (HTTP {exc.response.status_code}): {body}"
            ) from exc
        except requests.RequestException as exc:
            raise TokenError(f"Token exchange network error: {exc}") from exc

        data = resp.json()
        token = data.get("access_token")
        if not token:
            raise TokenError(f"No access_token in Upstox response: {data}")
        return token

    def _save_token_to_env(self, token: str) -> None:
        """Persist UPSTOX_ACCESS_TOKEN to .env. Uses dotenv.set_key if available."""
        try:
            from dotenv import set_key
            set_key(str(ENV_PATH), "UPSTOX_ACCESS_TOKEN", token)
            return
        except ImportError:
            pass
        except Exception as exc:
            logger.warning(f"dotenv.set_key failed: {exc}. Using fallback writer.")

        _write_env_key(ENV_PATH, "UPSTOX_ACCESS_TOKEN", token)


# ---------------------------------------------------------------------------
# .env write helper (fallback when dotenv unavailable or errors)
# ---------------------------------------------------------------------------

def _write_env_key(env_path: Path, key: str, value: str) -> None:
    """Insert or replace a KEY="value" line in an .env file."""
    lines: list[str] = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()

    prefix = f"{key}="
    replacement = f'{key}="{value}"'
    updated = False

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(prefix):
            lines[i] = replacement
            updated = True
            break

    if not updated:
        lines.append(replacement)

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info(f"Wrote {key} to {env_path}.")


# ---------------------------------------------------------------------------
# Module-level singleton convenience
# ---------------------------------------------------------------------------

_default_manager: Optional[TokenManager] = None


def _get_manager() -> TokenManager:
    global _default_manager
    if _default_manager is None:
        _default_manager = TokenManager()
    return _default_manager


def get_access_token() -> str:
    """Return a valid Upstox access token, refreshing if necessary."""
    return _get_manager().get_access_token()


def check_token_valid(token: Optional[str] = None) -> bool:
    """Return True if the given (or stored) token passes a live API check."""
    return _get_manager().check_token_valid(token)


def reset_manager() -> None:
    """Force a fresh TokenManager on next call (used in tests)."""
    global _default_manager
    _default_manager = None
