# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "aiohttp>=3.10",
#   "music-assistant-client>=1.3.4",
#   "PyJWT>=2.9",
# ]
# ///
"""Ingress identity sidecar for the Music Assistant ingress proxy add-on.

Caddy calls GET /authorize (forward_auth) on every proxied request and GET /bootstrap
for the bare SPA entrypoint. Both resolve the Supervisor-injected X-Remote-User-* headers
to a Music Assistant long-lived token, provisioning a Music Assistant user on first sight
of a Home Assistant user. Never returns success without a token: Caddy proxies an
unauthenticated request whenever this service does not supply one.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import ssl
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

import jwt as pyjwt
from aiohttp import ClientSession, ClientTimeout, TCPConnector, web
from music_assistant_client.client import MusicAssistantClient
from music_assistant_client.exceptions import CannotConnect
from music_assistant_models.errors import (
    AuthenticationFailed,
    InsufficientPermissions,
    InvalidDataError,
)

LOGGER = logging.getLogger("ma_ingress_proxy.sidecar")

DATA_DIR = Path(os.environ.get("SIDECAR_DATA_DIR", "/data"))
MAPPING_FILE = DATA_DIR / "mapping.json"
ADMIN_TOKEN_FILE = DATA_DIR / "admin_token.json"

MA_URL = os.environ["MA_URL"].rstrip("/")
MA_ADMIN_USERNAME = os.environ["MA_ADMIN_USERNAME"]
MA_ADMIN_PASSWORD = os.environ["MA_ADMIN_PASSWORD"]
VERIFY_SSL = os.environ.get("MA_VERIFY_SSL", "true").lower() != "false"
DEFAULT_ROLE = os.environ.get("MA_DEFAULT_ROLE", "user")
ADMIN_HA_USER_IDS = {
    x for x in os.environ.get("MA_ADMIN_HA_USER_IDS", "").split(",") if x
}
LISTEN_PORT = int(os.environ.get("SIDECAR_PORT", "9000"))
# Only Caddy, in the same container, needs to reach this service; it stays bound to
# loopback in production so nothing else on the container's network can call it directly
# and bypass Caddy's Supervisor-IP allowlist. Overridable for the split-container test harness.
LISTEN_HOST = os.environ.get("SIDECAR_HOST", "127.0.0.1")
ADMIN_TOKEN_NAME = "ha-ingress-proxy:admin"
TOKEN_NAME_PREFIX = "ha-ingress:"
# Refresh a cached/minted token this long before its real expiry, so it never gets
# handed out and then rejected by Music Assistant moments later.
EXPIRY_SAFETY_MARGIN_SECONDS = 300


class AuthzError(Exception):
    """Raised to fail an /authorize or /bootstrap request with a specific HTTP status."""

    def __init__(self, status: int, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


@dataclass
class CachedToken:
    token: str
    expires_at: float


class Sidecar:
    def __init__(self) -> None:
        self._cache: dict[str, CachedToken] = {}
        self._user_locks: dict[str, asyncio.Lock] = {}
        self._mapping_lock = asyncio.Lock()
        self._admin_token: str | None = None
        self._admin_token_lock = asyncio.Lock()
        self._ssl_context: ssl.SSLContext | None = None
        if not VERIFY_SSL:
            self._ssl_context = ssl.create_default_context()
            self._ssl_context.check_hostname = False
            self._ssl_context.verify_mode = ssl.CERT_NONE

    # ---------------------------------------------------------------- mapping

    def _load_mapping(self) -> dict[str, str]:
        if not MAPPING_FILE.exists():
            return {}
        try:
            return json.loads(MAPPING_FILE.read_text())
        except (json.JSONDecodeError, OSError) as err:
            LOGGER.error("Failed to read mapping file, treating as empty: %s", err)
            return {}

    def _save_mapping(self, mapping: dict[str, str]) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp_path = MAPPING_FILE.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(mapping, indent=2, sort_keys=True))
        tmp_path.replace(MAPPING_FILE)

    def _lock_for(self, ha_user_id: str) -> asyncio.Lock:
        lock = self._user_locks.get(ha_user_id)
        if lock is None:
            lock = self._user_locks[ha_user_id] = asyncio.Lock()
        return lock

    # ------------------------------------------------------------ admin auth

    async def _admin_login(self) -> str:
        """Log in as the admin via the REST auth/login endpoint and return a session token.

        Music Assistant's admin bootstrap has to happen before we hold any token, so it
        cannot go over the token-authenticated websocket API - it uses the plain REST
        endpoint instead. This posts the request body shape auth/login actually expects
        (credentials nested under "credentials") rather than the music-assistant-client
        package's own auth_helpers.login() helper, which posts a flat body and reads back
        an "access_token" field - both mismatched against the server's current contract.
        """
        connector = TCPConnector(ssl=self._ssl_context) if self._ssl_context else None
        async with ClientSession(connector=connector, timeout=ClientTimeout(total=10)) as session:
            try:
                async with session.post(
                    f"{MA_URL}/auth/login",
                    json={
                        "provider_id": "builtin",
                        "credentials": {
                            "username": MA_ADMIN_USERNAME,
                            "password": MA_ADMIN_PASSWORD,
                        },
                    },
                ) as resp:
                    data = await resp.json()
            except Exception as err:  # noqa: BLE001 - surfaced as a 502 to Caddy
                raise AuthzError(502, f"Admin login to Music Assistant failed: {err}") from err

        if not data.get("success"):
            raise AuthzError(502, f"Admin login to Music Assistant failed: {data.get('error')}")
        token = data.get("token") or data.get("access_token")
        if not token:
            raise AuthzError(502, "Admin login to Music Assistant returned no token")
        return token

    async def _bootstrap_admin_token(self) -> str:
        """Log in with the admin username/password and mint a durable admin token.

        Called only on first use or if the persisted admin token has stopped working
        (e.g. it was revoked, or MA's auth database was reset), so the admin password
        is not spent on every request.
        """
        session_token = await self._admin_login()

        async with MusicAssistantClient(MA_URL, None, session_token, self._ssl_context) as client:
            for existing in await client.auth.get_tokens():
                if existing.name == ADMIN_TOKEN_NAME:
                    await client.auth.revoke_token(existing.token_id)
            admin_token: str = await client.auth.create_token(ADMIN_TOKEN_NAME)

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        ADMIN_TOKEN_FILE.write_text(json.dumps({"token": admin_token}))
        LOGGER.info("Minted a new admin token for the sidecar")
        return admin_token

    async def _get_admin_token(self) -> str:
        if self._admin_token:
            return self._admin_token
        async with self._admin_token_lock:
            if self._admin_token:
                return self._admin_token
            if ADMIN_TOKEN_FILE.exists():
                try:
                    self._admin_token = json.loads(ADMIN_TOKEN_FILE.read_text())["token"]
                    return self._admin_token
                except (json.JSONDecodeError, OSError, KeyError):
                    LOGGER.warning("Stored admin token unreadable, re-bootstrapping")
            self._admin_token = await self._bootstrap_admin_token()
            return self._admin_token

    async def _admin_client(self) -> MusicAssistantClient:
        """Connect an admin-authenticated client, re-bootstrapping once on auth failure."""
        token = await self._get_admin_token()
        client = MusicAssistantClient(MA_URL, None, token, self._ssl_context)
        try:
            await client.connect()
        except AuthenticationFailed:
            LOGGER.warning("Stored admin token was rejected, re-bootstrapping")
            self._admin_token = None
            async with self._admin_token_lock:
                self._admin_token = self._admin_token or await self._bootstrap_admin_token()
            client = MusicAssistantClient(MA_URL, None, self._admin_token, self._ssl_context)
            await client.connect()
        except (OSError, CannotConnect, TimeoutError) as err:
            raise AuthzError(502, f"Cannot reach Music Assistant: {err}") from err
        return client

    # -------------------------------------------------------------- identity

    async def resolve_token(
        self, ha_user_id: str, ha_username: str | None, ha_display_name: str | None
    ) -> str:
        """Return a valid Music Assistant token for this Home Assistant user.

        Raises AuthzError on anything that must not result in a forwarded, authenticated
        request: unknown/disabled/guest accounts, or an unreachable Music Assistant server.
        """
        now = time.time()
        cached = self._cache.get(ha_user_id)
        if cached and cached.expires_at - EXPIRY_SAFETY_MARGIN_SECONDS > now:
            return cached.token

        async with self._lock_for(ha_user_id):
            # Another request may have refreshed the cache while we waited for the lock.
            cached = self._cache.get(ha_user_id)
            if cached and cached.expires_at - EXPIRY_SAFETY_MARGIN_SECONDS > now:
                return cached.token

            token = await self._provision_and_mint(ha_user_id, ha_username, ha_display_name)
            expires_at = _decode_jwt_expiry(token) or (now + 3600)
            self._cache[ha_user_id] = CachedToken(token=token, expires_at=expires_at)
            return token

    async def _provision_and_mint(
        self, ha_user_id: str, ha_username: str | None, ha_display_name: str | None
    ) -> str:
        async with await self._admin_client() as client:
            mapping = self._load_mapping()
            ma_user_id = mapping.get(ha_user_id)

            if ma_user_id is None:
                ma_user_id = await self._provision_user(
                    client, ha_user_id, ha_username, ha_display_name
                )
                async with self._mapping_lock:
                    mapping = self._load_mapping()
                    mapping[ha_user_id] = ma_user_id
                    self._save_mapping(mapping)

            # Revoke any previous ingress token for this user before minting a new one:
            # token values are not recoverable from Music Assistant, so a sidecar restart
            # cannot reuse the last one, and every restart would otherwise leave behind
            # another 365-day token.
            token_name = f"{TOKEN_NAME_PREFIX}{ha_user_id}"
            try:
                for existing in await client.auth.get_tokens(ma_user_id):
                    if existing.name == token_name:
                        await client.auth.revoke_token(existing.token_id)

                return await client.auth.create_token(token_name, user_id=ma_user_id)
            except InsufficientPermissions as err:
                # Guests cannot hold long-lived tokens by design - never silently
                # re-provision a fresh account to work around that.
                LOGGER.warning(
                    "Refusing to mint a token for HA user %s (ma user %s): %s",
                    ha_user_id,
                    ma_user_id,
                    err,
                )
                raise AuthzError(403, str(err)) from err
            except InvalidDataError as err:
                # The mapped MA user no longer exists or was disabled - fail closed
                # rather than silently provisioning a replacement account, which
                # would undo an admin's deliberate decision.
                LOGGER.warning(
                    "Refusing to mint a token for HA user %s (ma user %s): %s",
                    ha_user_id,
                    ma_user_id,
                    err,
                )
                raise AuthzError(403, str(err)) from err

    async def _provision_user(
        self,
        client: MusicAssistantClient,
        ha_user_id: str,
        ha_username: str | None,
        ha_display_name: str | None,
    ) -> str:
        username = _derive_username(ha_user_id, ha_username)
        role = "admin" if ha_user_id in ADMIN_HA_USER_IDS else DEFAULT_ROLE
        password = secrets.token_urlsafe(32)  # used once to satisfy the API, then discarded
        user = await client.auth.create_user(
            username=username,
            password=password,
            role=role,
            display_name=ha_display_name,
        )
        LOGGER.info(
            "Provisioned Music Assistant user %s (role=%s) for HA user %s",
            user.user_id,
            role,
            ha_user_id,
        )
        return user.user_id


def _derive_username(ha_user_id: str, ha_username: str | None) -> str:
    if ha_username:
        candidate = ha_username.strip().lower()
        if len(candidate) >= 2:
            return candidate
    return f"ha-{ha_user_id[:8]}".lower()


def _decode_jwt_expiry(token: str) -> float | None:
    try:
        claims = pyjwt.decode(token, options={"verify_signature": False})
    except pyjwt.PyJWTError:
        return None
    exp = claims.get("exp")
    return float(exp) if exp is not None else None


def _extract_ha_headers(request: web.Request) -> tuple[str, str | None, str | None]:
    ha_user_id = request.headers.get("X-Remote-User-Id")
    if not ha_user_id:
        raise AuthzError(403, "Missing X-Remote-User-Id header")
    return (
        ha_user_id,
        request.headers.get("X-Remote-User-Name"),
        request.headers.get("X-Remote-User-Display-Name"),
    )


def create_app(sidecar: Sidecar) -> web.Application:
    routes = web.RouteTableDef()

    @routes.get("/authorize")
    async def authorize(request: web.Request) -> web.Response:
        try:
            ha_user_id, ha_username, ha_display_name = _extract_ha_headers(request)
            token = await sidecar.resolve_token(ha_user_id, ha_username, ha_display_name)
        except AuthzError as err:
            LOGGER.info("Denying request: %s", err.reason)
            return web.Response(status=err.status, text=err.reason)
        except Exception as err:  # noqa: BLE001 - never forward a request unauthenticated
            LOGGER.exception("Unexpected error resolving token: %s", err)
            return web.Response(status=502, text="Unexpected error")
        return web.Response(status=200, headers={"Authorization": f"Bearer {token}"})

    @routes.get("/bootstrap")
    async def bootstrap(request: web.Request) -> web.Response:
        """Redirect the bare SPA entrypoint to itself with a bearer token attached.

        The Music Assistant frontend accepts a `?code=<access_token>` query parameter on
        its login screen (the same mechanism its Home Assistant OAuth provider uses on
        its callback) and stores it for the browser session. This lets a Home Assistant
        user land in a fully authenticated Music Assistant session without ever seeing a
        Music Assistant login form, without any change to Music Assistant itself.
        """
        try:
            ha_user_id, ha_username, ha_display_name = _extract_ha_headers(request)
            token = await sidecar.resolve_token(ha_user_id, ha_username, ha_display_name)
        except AuthzError as err:
            LOGGER.info("Denying bootstrap request: %s", err.reason)
            return web.Response(status=err.status, text=err.reason)
        except Exception as err:  # noqa: BLE001 - never redirect with a stale/missing token
            LOGGER.exception("Unexpected error resolving token: %s", err)
            return web.Response(status=502, text="Unexpected error")

        query = dict(request.query)
        query["code"] = token
        location = f"?{urlencode(query)}"
        return web.Response(status=302, headers={"Location": location})

    @routes.get("/healthz")
    async def healthz(_request: web.Request) -> web.Response:
        return web.Response(status=200, text="ok")

    app = web.Application()
    app.add_routes(routes)
    return app


def _configure_logging() -> None:
    level_name = os.environ.get("LOG_LEVEL", "info").upper()
    level = {
        "TRACE": logging.DEBUG,
        "DEBUG": logging.DEBUG,
        "NOTICE": logging.INFO,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "FATAL": logging.CRITICAL,
    }.get(level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        stream=sys.stdout,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )


def main() -> None:
    _configure_logging()
    sidecar = Sidecar()
    app = create_app(sidecar)
    web.run_app(app, host=LISTEN_HOST, port=LISTEN_PORT, print=None)


if __name__ == "__main__":
    main()
