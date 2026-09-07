# Music Assistant Ingress Proxy

Puts an **externally hosted** [Music Assistant](https://music-assistant.io) server in the
Home Assistant sidebar, with per-Home-Assistant-user authentication, without running the
Music Assistant server itself on the Home Assistant host.

## Why this add-on exists

Music Assistant's ingress support is verified at the socket level, not by headers. When a
request arrives, `is_request_from_ingress()` checks whether it landed on the specific
TCP listener Music Assistant binds for Supervisor's ingress network
(`music_assistant/controllers/webserver/helpers/auth_middleware.py`). Only then does it
trust the `X-Remote-User-*` headers Supervisor attaches. An externally hosted Music
Assistant server has no such listener - it never binds anything on Supervisor's private
`172.30.32.x` network - so those headers are never trusted, by design, regardless of what
proxies you put in front of it.

That means the *only* supported way to authenticate against an external Music Assistant
server is with an access token. This add-on's sidecar service holds one Music Assistant
admin credential, and uses Music Assistant's own `auth/token/create` API (which permits
minting tokens for other users when the caller holds the `users.manage` scope) to give
each Home Assistant user their own Music Assistant account, with their own role and
player/provider filters.

Music Assistant itself is not modified. There are no patched files and no bind mounts over
its image.

## How it works

Two processes run in this add-on's container:

1. **Caddy** receives Supervisor's ingress traffic on an internal port, checks it actually
   came from Supervisor's address, and reverse-proxies it to your Music Assistant server.
2. **A small Python sidecar** maps the Home Assistant user making the request to a Music
   Assistant long-lived token, which Caddy injects as the `Authorization` header on every
   proxied request.

On the very first request for a page load, the sidecar also has Caddy redirect the browser
to itself with `?code=<token>` appended. Music Assistant's frontend already reads that
query parameter (the same mechanism its "Sign in with Home Assistant" OAuth provider's
callback uses) and stores the token for its own use - this is how the interactive panel
ends up logged in as your Music Assistant account without ever showing a Music Assistant
login screen. See "Known limitations" below for why this detail matters for future
maintenance.

## Setup

1. In Music Assistant, create a **dedicated admin account** for this add-on - do not
   reuse a real person's account. It needs the `admin` role (which carries the
   `users.manage` scope this add-on needs to provision users and mint their tokens).
2. Install this add-on and set:
   - **Music Assistant URL**: the base URL of your externally hosted server, e.g.
     `https://musicassistant.example.com`.
   - **Admin username** / **Admin password**: the account from step 1.
   - **Verify SSL**: leave enabled unless your server uses a self-signed certificate.
   - **Default role**: the Music Assistant role newly seen Home Assistant users get
     (`user` or `admin`).
   - **Admin HA user IDs**: Home Assistant user IDs (not names) that should be
     provisioned as Music Assistant admins instead of the default role.
3. Start the add-on and open it from the sidebar.

### What happens on first run

The first time a given Home Assistant user opens the panel, the sidecar:

1. Creates a Music Assistant user for them (username derived from their Home Assistant
   username, falling back to `ha-<first 8 chars of their user id>` if Home Assistant
   doesn't supply one), with a random password that is used once to satisfy Music
   Assistant's account-creation API and then discarded - nobody ever needs it, since
   authentication only ever happens via the minted token.
2. Mints a long-lived (1 year) Music Assistant access token for that account.
3. Records the Home Assistant user ID -> Music Assistant user ID mapping in this add-on's
   `/data/mapping.json`, and caches the token in memory.

This runs roughly once per user per add-on lifetime (tokens don't expire for a year and
don't need to be re-minted on every request), not on every page load or request.

### Removing a user

Delete their account from the Music Assistant admin UI, then remove their line from
`/data/mapping.json` in this add-on's data directory. If you skip the second step, a
Home Assistant user who is re-provisioned under the same ID would otherwise fail closed
(see "Security model") rather than silently getting a new account - which is deliberate,
but means the stale mapping entry should be cleaned up if you want that Home Assistant
user to get a *fresh* Music Assistant account rather than staying locked out.

### Options reference

| Option | Type | Notes |
|---|---|---|
| `ma_url` | url | Base URL of the external Music Assistant server. |
| `ma_admin_username` | string | Dedicated admin account for this add-on. |
| `ma_admin_password` | password | Password for that account. Never logged. |
| `verify_ssl` | bool | Default `true`. Disable only for a trusted self-signed certificate. |
| `default_role` | `user` \| `admin` | Role for newly seen Home Assistant users. Default `user`. |
| `admin_ha_user_ids` | list of strings | Home Assistant user IDs to provision as Music Assistant admins. |
| `log_level` | list | Log verbosity for Caddy and the sidecar. Default `info`. |

## Security model

- Caddy only accepts traffic on its ingress-facing port from Supervisor's fixed internal
  address (`172.30.32.2`); everything else gets a 403 before it reaches anything else.
- The sidecar only listens on `127.0.0.1` inside the container - nothing but Caddy, in
  the same container, can reach it.
- A request with no `X-Remote-User-Id` header is refused (403); nothing is forwarded to
  Music Assistant without an identity.
- A client-supplied `Authorization` header is always overwritten by the one Caddy injects
  from the sidecar - a client cannot present its own credentials to bypass identity
  resolution.
- `X-Remote-User-*` headers are stripped before the request reaches Music Assistant. They
  are meaningless to it (it never trusts headers off its ingress listener) and are not
  forwardable by a client through this proxy either way.
- A disabled or deleted Music Assistant account's requests are refused (fail closed): the
  sidecar never silently re-provisions a replacement account to work around an admin's
  deliberate decision, and Music Assistant's own token validation independently rejects
  that token's use for every real command, session cache or not (see "Known limitations").
- Guest accounts are excluded by design - Music Assistant itself refuses to mint
  long-lived tokens for the `guest` role, and the sidecar surfaces that as a 403 rather
  than working around it.

## Known limitations

- **The `?code=` bootstrap is a documented but internal Music Assistant frontend
  behaviour, not a stable public API.** It was confirmed against the `music-assistant/frontend`
  repository's `Login.vue` (`authManager.setToken(authCode)`) and the server's own
  `build_code_redirect_url` helper (used by its HA-OAuth login callback) at the time this
  add-on was built. A future Music Assistant frontend release could change or remove it.
  If the panel starts showing Music Assistant's own login screen instead of logging you
  in silently, this is the first thing to re-check upstream.
- **A cached token does not immediately reflect a Music Assistant admin disabling that
  user.** The sidecar caches minted tokens in memory (by design - re-minting on every
  request would defeat the point of a year-long token) and does not re-check the
  account's enabled state on every cache hit. Music Assistant itself independently
  rejects a disabled user's token for every real command it's used against, so this is a
  UX staleness window (the disabled user may briefly still get a `200` from this add-on's
  `/authorize` before Music Assistant itself starts refusing them), not a security gap.
  Restart the add-on to clear the cache immediately after disabling someone.
- **The Home Assistant ingress path must stay stable for token persistence to work
  across page reloads.** The frontend binds its stored token to the connection address
  it saw, which for an ingress session is derived from the ingress URL. This is normally
  stable for the life of an add-on installation.
- The "Sign in with Home Assistant" OAuth provider built into Music Assistant is the
  long-term, first-party answer to this problem, but at the time this add-on was written
  it failed with "Invalid redirect URI" when embedded as an HA panel or opened in the
  companion app (see music-assistant/support issues #5173 and #4880). Re-check whether
  that's been fixed upstream before assuming this add-on is still needed.

## Testing

`test/` contains a Docker Compose harness that stands in for Supervisor's ingress
network: fixed addresses on a `172.30.32.0/23` network play the parts of Supervisor
(`172.30.32.2`, the only address the shipped Caddyfile trusts) and an outside attacker,
alongside a real `ghcr.io/music-assistant/server:beta` container and this add-on's own
Caddy + sidecar.

```sh
docker build --build-arg BUILD_FROM=ghcr.io/hassio-addons/debian-base:8.1.1 \
    -t ma-ingress-proxy:test .
cd test
./run_tests.sh
```

This exercises: an unknown user's first request being provisioned and reaching Music
Assistant; a client-supplied `Authorization` header being overridden; a non-Supervisor
source address and a missing identity header both being refused; two distinct Home
Assistant users getting two distinct Music Assistant identities; Music Assistant going
down and the sidecar recovering without a restart; and, as a stand-in for a real browser,
a raw WebSocket client using the bootstrap redirect's token to authenticate a session the
same way Music Assistant's own frontend does. It does not drive an actual browser end to
end - if you change the Caddyfile or the sidecar's `/bootstrap` route, load the panel in a
real browser afterwards.
