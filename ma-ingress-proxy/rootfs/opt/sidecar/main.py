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
from typing import Any
from urllib.parse import urlencode

import jwt as pyjwt
from aiohttp import ClientSession, ClientTimeout, TCPConnector, web
from music_assistant_client.client import MusicAssistantClient
from music_assistant_client.exceptions import CannotConnect
from music_assistant_models.errors import (
    AuthenticationFailed,
    InsufficientPermissions,
    InvalidDataError,
    MusicAssistantError,
)

LOGGER = logging.getLogger("ma_ingress_proxy.sidecar")

DATA_DIR = Path(os.environ.get("SIDECAR_DATA_DIR", "/data"))
MAPPING_FILE = DATA_DIR / "mapping.json"
ADMIN_TOKEN_FILE = DATA_DIR / "admin_token.json"
ADMIN_TOKEN_FILE_ENCRYPTED = DATA_DIR / "admin_token.json.age"

MA_URL = os.environ["MA_URL"].rstrip("/")
MA_ADMIN_USERNAME = os.environ["MA_ADMIN_USERNAME"]
MA_ADMIN_PASSWORD = os.environ["MA_ADMIN_PASSWORD"]
VERIFY_SSL = os.environ.get("MA_VERIFY_SSL", "true").lower() != "false"
DEFAULT_ROLE = os.environ.get("MA_DEFAULT_ROLE", "user")
ADMIN_HA_USER_IDS = {
    x for x in os.environ.get("MA_ADMIN_HA_USER_IDS", "").split(",") if x
}
# What to do when a Home Assistant user's derived username (see _derive_username) is
# already taken by an unrelated, pre-existing Music Assistant account: "adopt" links the
# HA user to that account outright; "create_new" (default) provisions a disambiguated
# account instead, so an unrelated account can never be silently hijacked just because a
# username happens to collide.
ADOPT_EXISTING_USERNAME = "adopt"
CREATE_NEW_USERNAME = "create_new"
ON_USERNAME_CONFLICT = os.environ.get("MA_ON_USERNAME_CONFLICT", CREATE_NEW_USERNAME)
LISTEN_PORT = int(os.environ.get("SIDECAR_PORT", "9000"))
# Only Caddy, in the same container, needs to reach this service; it stays bound to
# loopback in production so nothing else on the container's network can call it directly
# and bypass Caddy's Supervisor-IP allowlist. Overridable for the split-container test harness.
LISTEN_HOST = os.environ.get("SIDECAR_HOST", "127.0.0.1")
# Mirrors this repository's caddy-2 add-on's age_identity option: an X25519 age
# identity (private key), pasted directly into the add-on config. Unset skips
# encryption entirely, same as caddy-2's "secret decryption is skipped" behaviour.
AGE_IDENTITY = os.environ.get("AGE_IDENTITY", "").strip()
ADMIN_TOKEN_NAME = "ha-ingress-proxy:admin"
TOKEN_NAME_PREFIX = "ha-ingress:"
# No longer minted (the bootstrap redirect now hands out the same cached ha-ingress:
# token /authorize does - see create_app()'s /bootstrap route for why), kept only so
# wipe_stale_tokens() still cleans up any leftover tokens an installation minted under
# this name before that change.
BOOTSTRAP_TOKEN_NAME_PREFIX = "ha-ingress-bootstrap:"
# Refresh a cached/minted token this long before its real expiry, so it never gets
# handed out and then rejected by Music Assistant moments later.
EXPIRY_SAFETY_MARGIN_SECONDS = 300
# Bounds the one-off startup token wipe (see Sidecar.wipe_stale_tokens): the HTTP server
# does not start accepting connections - not even /healthz - until this completes, so an
# unreachable (not just refused) Music Assistant server at boot must not be able to stall
# it for aiohttp's default multi-minute connection timeout.
WIPE_STALE_TOKENS_TIMEOUT_SECONDS = 10


class AuthzError(Exception):
    """Raised to fail an /authorize or /bootstrap request with a specific HTTP status."""

    def __init__(self, status: int, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


async def _run_age(*args: str, input_bytes: bytes | None = None) -> bytes:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE if input_bytes is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate(input_bytes)
    if proc.returncode != 0:
        raise RuntimeError(f"{args[0]} failed: {stderr.decode(errors='replace').strip()}")
    return stdout


class AgeCrypto:
    """Encrypts/decrypts small values at rest using the configured age identity.

    Mirrors this repository's caddy-2 add-on: a single `age_identity` option holds an
    X25519 age identity (private key), pasted directly into the add-on config. Unlike
    caddy-2 - which only ever decrypts a secrets file a human encrypted themselves with
    `age -r <recipient>` - this sidecar originates the secret it needs to persist (the
    admin token) itself, so it also needs the matching recipient (public key) to encrypt
    with. That's derived from the identity via `age-keygen -y` rather than asking the
    user to separately track and paste a public key too.
    """

    def __init__(self, identity: str) -> None:
        self.enabled = bool(identity)
        self._identity_path: Path | None = None
        self._recipient: str | None = None
        if self.enabled:
            # /run is tmpfs: the plaintext identity never touches the persistent /data
            # volume, and is gone the moment the container stops.
            self._identity_path = Path(f"/run/age-identity-{secrets.token_hex(8)}")
            self._identity_path.write_text(identity + "\n")
            self._identity_path.chmod(0o600)

    async def _recipient_key(self) -> str:
        if self._recipient is None:
            out = await _run_age("age-keygen", "-y", str(self._identity_path))
            self._recipient = out.decode().strip()
        return self._recipient

    async def encrypt(self, plaintext: bytes) -> bytes:
        recipient = await self._recipient_key()
        return await _run_age("age", "-r", recipient, input_bytes=plaintext)

    async def decrypt(self, ciphertext: bytes) -> bytes:
        return await _run_age("age", "-d", "-i", str(self._identity_path), input_bytes=ciphertext)


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
        self._age = AgeCrypto(AGE_IDENTITY)
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

        await self._save_encrypted_json(
            {"token": admin_token}, ADMIN_TOKEN_FILE, ADMIN_TOKEN_FILE_ENCRYPTED
        )
        LOGGER.info("Minted a new admin token for the sidecar")
        return admin_token

    async def _load_admin_token(self) -> str | None:
        data = await self._load_encrypted_json(
            ADMIN_TOKEN_FILE, ADMIN_TOKEN_FILE_ENCRYPTED, "admin token"
        )
        try:
            return None if data is None else data["token"]
        except KeyError:
            LOGGER.warning("Stored admin token file missing its token field, re-bootstrapping")
            return None

    # --------------------------------------------------------- encrypted storage

    async def _save_encrypted_json(
        self, data: dict[str, Any], plain_path: Path, encrypted_path: Path
    ) -> None:
        """Persist a JSON-serializable value, encrypted at rest when age is configured.

        Removes any plaintext copy left over from before encryption was turned on, so a
        secret is never readable from two places at once.

        Never raises: a failure here (e.g. a malformed age_identity) must not undo work
        already done against Music Assistant by the caller - _bootstrap_admin_token()
        mints (and revokes the previous) admin token before calling this, and losing
        that token because it merely couldn't be written to disk would force every
        subsequent request to repeat the whole login+revoke+mint cycle with the admin
        password. Worst case here is falling back to in-memory-only for this process's
        lifetime, exactly as if age_identity had never been set.
        """
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, indent=2, sort_keys=True).encode()
        try:
            if self._age.enabled:
                encrypted = await self._age.encrypt(payload)
                tmp_path = encrypted_path.with_suffix(".tmp")
                tmp_path.write_bytes(encrypted)
                tmp_path.replace(encrypted_path)
                if plain_path.exists():
                    plain_path.unlink()
            else:
                tmp_path = plain_path.with_suffix(".tmp")
                tmp_path.write_bytes(payload)
                tmp_path.replace(plain_path)
                # Clean up a stale encrypted copy left over from age_identity being
                # turned off - it can never be decrypted again without the identity
                # that produced it, so leaving it in place is only ever misleading.
                if encrypted_path.exists():
                    LOGGER.info(
                        "age_identity is not set; removing the now-unreadable "
                        "encrypted copy at %s",
                        encrypted_path,
                    )
                    encrypted_path.unlink()
        except Exception:
            LOGGER.warning(
                "Failed to persist %s to disk; continuing with it held in memory only "
                "for this run (check age_identity if this is unexpected)",
                plain_path.name,
                exc_info=True,
            )

    async def _load_encrypted_json(
        self, plain_path: Path, encrypted_path: Path, description: str
    ) -> dict[str, Any] | None:
        """Load a value persisted by _save_encrypted_json(), decrypting when required.

        Migrates a plaintext file left over from before encryption was turned on: reads
        it once, then immediately re-persists it so it ends up encrypted and the
        plaintext copy is removed, rather than waiting for its next natural rewrite.
        """
        if self._age.enabled:
            if encrypted_path.exists():
                try:
                    decrypted = await self._age.decrypt(encrypted_path.read_bytes())
                    return json.loads(decrypted)
                except Exception:
                    LOGGER.warning("Stored %s could not be decrypted", description, exc_info=True)
                    return None
            data = self._read_plain_json(plain_path, description)
            if data is not None:
                LOGGER.info("Migrating stored %s to age-encrypted storage", description)
                await self._save_encrypted_json(data, plain_path, encrypted_path)
            return data
        return self._read_plain_json(plain_path, description)

    def _read_plain_json(self, path: Path, description: str) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            LOGGER.warning("Stored %s unreadable", description)
            return None

    async def _get_admin_token(self) -> str:
        if self._admin_token:
            return self._admin_token
        async with self._admin_token_lock:
            if self._admin_token:
                return self._admin_token
            stored = await self._load_admin_token()
            if stored is not None:
                self._admin_token = stored
                return self._admin_token
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

    async def _ensure_ma_user(
        self,
        client: MusicAssistantClient,
        ha_user_id: str,
        ha_username: str | None,
        ha_display_name: str | None,
    ) -> str:
        """Look up this Home Assistant user's Music Assistant user id, provisioning it if new."""
        mapping = self._load_mapping()
        ma_user_id = mapping.get(ha_user_id)
        if ma_user_id is not None:
            return ma_user_id

        ma_user_id = await self._provision_user(client, ha_user_id, ha_username, ha_display_name)
        async with self._mapping_lock:
            mapping = self._load_mapping()
            mapping[ha_user_id] = ma_user_id
            self._save_mapping(mapping)
        return ma_user_id

    async def _revoke_and_mint(
        self, client: MusicAssistantClient, ma_user_id: str, token_name: str, ha_user_id: str
    ) -> str:
        """Revoke any existing token with this exact name for the user, then mint a fresh one.

        Re-minted at most once per sidecar process lifetime per user (see resolve_token()'s
        cache), not on every request, so revoking a stale token left behind by a previous
        process is safe here and prevents another 365-day token being left behind on
        every restart.
        """
        for existing in await client.auth.get_tokens(ma_user_id):
            if existing.name == token_name:
                await client.auth.revoke_token(existing.token_id)
        return await self._mint_token(client, ma_user_id, token_name, ha_user_id)

    async def _mint_token(
        self, client: MusicAssistantClient, ma_user_id: str, token_name: str, ha_user_id: str
    ) -> str:
        try:
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

    async def _provision_and_mint(
        self, ha_user_id: str, ha_username: str | None, ha_display_name: str | None
    ) -> str:
        async with await self._admin_client() as client:
            ma_user_id = await self._ensure_ma_user(
                client, ha_user_id, ha_username, ha_display_name
            )
            return await self._revoke_and_mint(
                client, ma_user_id, f"{TOKEN_NAME_PREFIX}{ha_user_id}", ha_user_id
            )

    async def _provision_user(
        self,
        client: MusicAssistantClient,
        ha_user_id: str,
        ha_username: str | None,
        ha_display_name: str | None,
    ) -> str:
        username = _derive_username(ha_user_id, ha_username)
        role = "admin" if ha_user_id in ADMIN_HA_USER_IDS else DEFAULT_ROLE
        try:
            user = await self._create_ma_user(client, username, role, ha_display_name)
        except MusicAssistantError as err:
            if not _is_username_conflict(err):
                raise
            user = await self._resolve_username_conflict(
                client, username, role, ha_user_id, ha_display_name
            )
        LOGGER.info(
            "Provisioned Music Assistant user %s (role=%s) for HA user %s",
            user.user_id,
            role,
            ha_user_id,
        )
        return user.user_id

    @staticmethod
    async def _create_ma_user(
        client: MusicAssistantClient, username: str, role: str, display_name: str | None
    ):
        password = secrets.token_urlsafe(32)  # used once to satisfy the API, then discarded
        return await client.auth.create_user(
            username=username,
            password=password,
            role=role,
            display_name=display_name,
        )

    async def _resolve_username_conflict(
        self,
        client: MusicAssistantClient,
        username: str,
        role: str,
        ha_user_id: str,
        ha_display_name: str | None,
    ):
        """Handle Music Assistant already having a user named `username`.

        `_derive_username` doesn't (and can't, without asking Music Assistant) know
        whether its guess collides with an unrelated pre-existing account - e.g. a human
        set one up by hand before this add-on ever ran, or the HA username matches
        someone else's. Controlled by ON_USERNAME_CONFLICT: "adopt" links the HA user to
        that existing account; "create_new" (default) provisions a fresh, disambiguated
        one so a pre-existing account can never be silently hijacked.
        """
        existing = next(
            (
                u
                for u in await client.auth.list_users()
                if u.username.lower() == username.lower()
            ),
            None,
        )
        if existing is None:
            # Some other UNIQUE constraint fired - nothing to adopt or disambiguate from.
            raise

        if ON_USERNAME_CONFLICT == ADOPT_EXISTING_USERNAME:
            LOGGER.warning(
                "Music Assistant already has a user named %r; adopting existing user %s "
                "for HA user %s (on_username_conflict=adopt)",
                username,
                existing.user_id,
                ha_user_id,
            )
            return existing

        # create_new: the fallback string only needs to be deterministic and collision-free
        # for *this* HA user, not short or memorable - the human-friendly username was
        # already tried and lost the race to an unrelated account.
        fallback_username = f"ha-{ha_user_id}".lower()
        LOGGER.warning(
            "Music Assistant already has a user named %r; provisioning %r instead for HA "
            "user %s (set on_username_conflict=adopt to link to the existing account "
            "instead)",
            username,
            fallback_username,
            ha_user_id,
        )
        return await self._create_ma_user(client, fallback_username, role, ha_display_name)

    async def wipe_stale_tokens(self) -> None:
        """Revoke every ha-ingress/ha-ingress-bootstrap token, for every user, at startup.

        The per-user token cache lives in memory only and does not survive a restart, so
        this gives Music Assistant a matching clean slate: a restart is a firm boundary
        after which no previously minted per-user token remains valid, rather than
        leaving old tokens to linger on Music Assistant's side until the next time that
        particular user happens to reopen the panel - which may be a long time, or never,
        if they've since lost access. The admin token (a different name prefix,
        `ha-ingress-proxy:admin`) is untouched - it's infrastructure the sidecar needs to
        do this wipe in the first place, not a per-request artifact.

        This runs from an aiohttp on_startup hook, before the HTTP server (including
        /healthz) starts accepting connections at all - so it is bounded by a timeout,
        not just wrapped in a bare except. Without one, an MA_URL that is merely
        unreachable at the network layer (dropped SYN, VPN not up yet at HA boot) rather
        than actively refused would stall the underlying aiohttp ClientSession's default
        300s timeout, during which the sidecar would never open its listening port -
        turning "Music Assistant is briefly unreachable" into "Supervisor's watchdog
        never sees a healthy container." Best-effort either way: anything left behind
        because of a timeout or any other failure here is still bounded by the existing
        revoke-before-mint step the next time that user opens the panel.
        """
        try:
            await asyncio.wait_for(
                self._wipe_stale_tokens_now(), timeout=WIPE_STALE_TOKENS_TIMEOUT_SECONDS
            )
        except TimeoutError:
            LOGGER.warning(
                "Timed out after %ds wiping stale per-user tokens at startup (Music "
                "Assistant may be unreachable); starting anyway",
                WIPE_STALE_TOKENS_TIMEOUT_SECONDS,
            )
        except Exception:  # noqa: BLE001 - never block startup over this
            LOGGER.warning("Failed to wipe stale per-user tokens at startup", exc_info=True)

    async def _wipe_stale_tokens_now(self) -> None:
        # Sequential by necessity, not oversight: MusicAssistantClient.send_command()
        # reads its response directly off the shared websocket when not in
        # start_listening() mode (which this short-lived admin connection never enters),
        # so concurrent calls race on the same underlying receive() - verified live,
        # asyncio.gather() over these calls raises "Concurrent call to receive() is not
        # allowed" from aiohttp.
        async with await self._admin_client() as client:
            revoked = 0
            for user in await client.auth.list_users():
                user_tokens = await client.auth.get_tokens(user.user_id)
                # Music Assistant's auth/tokens command caps at TOKEN_LIST_LIMIT (100,
                # newest first) with no pagination exposed here - if a single user has
                # accumulated more than that many matching tokens, the older ones are
                # invisible to this call and can never be reached by it. Not expected in
                # practice (each restart normally leaves at most one), but log it if it
                # ever happens rather than silently under-delivering on "revoke every".
                if len(user_tokens) >= 100:
                    LOGGER.warning(
                        "User %s has %d+ auth tokens; Music Assistant's API only returns "
                        "the newest 100, so older ha-ingress tokens may not be revoked "
                        "here",
                        user.user_id,
                        len(user_tokens),
                    )
                for token in user_tokens:
                    if token.name.startswith(TOKEN_NAME_PREFIX) or token.name.startswith(
                        BOOTSTRAP_TOKEN_NAME_PREFIX
                    ):
                        await client.auth.revoke_token(token.token_id)
                        revoked += 1
            if revoked:
                LOGGER.info("Startup: revoked %d per-user token(s) from a previous run", revoked)


def _derive_username(ha_user_id: str, ha_username: str | None) -> str:
    if ha_username:
        candidate = ha_username.strip().lower()
        if len(candidate) >= 2:
            return candidate
    return f"ha-{ha_user_id[:8]}".lower()


def _is_username_conflict(err: MusicAssistantError) -> bool:
    """True if `err` is Music Assistant's raw SQLite UNIQUE-constraint failure on username.

    The server has no dedicated error type for this - it's the base MusicAssistantError
    with the driver's message text as its only distinguishing signal.
    """
    message = str(err)
    return "UNIQUE constraint failed" in message and "username" in message


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

        The Music Assistant frontend accepts a `?code=<access_token>` query parameter
        (`Login.vue`'s `authManager.setToken(authCode)`, gated on `authCode.length > 8` to
        tell it apart from the unrelated 8-character party/QR join code) and stores it for
        the browser session - the same mechanism the server's own `build_code_redirect_url`
        helper uses for its "Sign in with Home Assistant" OAuth callback and first-run setup
        redirects. This lets a Home Assistant user land in a fully authenticated Music
        Assistant session without ever seeing a Music Assistant login form, without any
        change to Music Assistant itself. Confirmed empirically that the alternative -
        Caddy injecting the Authorization header directly on the /ws upgrade request - does
        NOT authenticate the resulting session: Music Assistant's websocket handler never
        reads that header, only its own in-band `auth` command or a real ingress-bound
        socket (see README's "Known limitations"), so a token has to reach the browser's own
        JS one way or another.

        Hands out the exact same cached, long-lived token /authorize injects as the
        Authorization header - not a separate short-lived one. An earlier version of this
        add-on minted a dedicated token here and proactively revoked it about two minutes
        later, on the assumption it was only ever needed for the instant of the WS
        handshake. In practice the Music Assistant frontend keeps using that same token
        for the life of the browser session (WS reconnects included), so revoking it out
        from under an open tab logged the user out - and with two devices open for the
        same Home Assistant user, each fresh /bootstrap call revoked the other device's
        token immediately by name. Reusing the ordinary cached token sidesteps both: nothing
        issued here is ever revoked early, and every device sees the same durable token
        Caddy already trusts. The tradeoff is this token can now end up in browser history
        or an access log with its full ~1 year lifetime rather than ~2 minutes - the same
        exposure the Authorization header already carries, and no worse than a normal
        Music Assistant login already accepts (its own tokens are just as long-lived and
        stored client-side indefinitely too).
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

    async def _on_startup(_app: web.Application) -> None:
        # Runs before the server starts accepting connections, so no request can mint a
        # fresh per-user token that this wipe would then immediately (and incorrectly)
        # revoke out from under it.
        await sidecar.wipe_stale_tokens()

    app = web.Application()
    app.add_routes(routes)
    app.on_startup.append(_on_startup)
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
