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
query parameter (`Login.vue`'s `authManager.setToken(authCode)`, gated on the code being
longer than 8 characters so it isn't confused with the unrelated party/QR join code) and
stores the token for its own use - the same mechanism the server's own
`build_code_redirect_url` helper uses for its "Sign in with Home Assistant" OAuth callback
and first-run setup redirects. This is how the interactive panel ends up logged in as your
Music Assistant account without ever showing a Music Assistant login screen.

This is necessary because injecting the `Authorization` header does not, on its own,
authenticate the panel's live session: Music Assistant's frontend talks to the server over
a single WebSocket, and that connection only becomes authenticated via its own in-band
`auth` command carrying a literal token, or a real Supervisor-bound ingress socket (which
an external server can never have - see "Why this add-on exists"). Caddy injecting the
header on the `/ws` upgrade request specifically was tested directly against a real Music
Assistant server and confirmed to do nothing: the server never reads that header for the
websocket handshake. A token has to reach the browser's own JavaScript one way or another,
which is what the redirect is for.

The token in that redirect is **not** the same long-lived token Caddy injects as the
`Authorization` header on ordinary requests. A URL query parameter ends up in browser
history and, potentially, logs, so handing out a credential that stays valid for a year
there would be reckless. The sidecar instead mints a dedicated, single-purpose token for
each bootstrap redirect and proactively revokes it from Music Assistant's own token
database about two minutes later - long enough to survive a slow page load, short enough
that a copy of the URL captured afterwards is worthless. See "Known limitations" for the
full reasoning.

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
- The token handed to the browser via the bootstrap redirect is never the long-lived
  `Authorization`-header token. It is a separate, dedicated token the sidecar proactively
  revokes from Music Assistant's own token database about two minutes after issuing it, so
  a copy of that URL captured from browser history or a log line stops working almost
  immediately rather than staying valid for a year.

## Known limitations

- **The `?code=` bootstrap is a documented but internal Music Assistant frontend
  behaviour, not a stable public API.** It was confirmed three ways against the
  `music-assistant/server` and `music-assistant/frontend` repositories at the time this
  add-on was built: `Login.vue` reads `?code=` as a bearer token via
  `authManager.setToken(authCode)` (explicitly distinguished in its own comments from the
  unrelated 8-character party/QR `?join=` code); the server's `build_code_redirect_url`
  helper's docstring calls its `token` parameter "the auth token to pass along as the
  `code` query parameter"; and it was exercised end to end against a real
  `ghcr.io/music-assistant/server:beta` container with a raw WebSocket client, which
  authenticated successfully as the right user using exactly the token the bootstrap
  redirect handed out (see `test/case9_check.py`). A future Music Assistant frontend
  release could still change or remove this. If the panel starts showing Music
  Assistant's own login screen instead of logging you in silently, this is the first
  thing to re-check upstream - along with whether injecting `Authorization` directly on
  the `/ws` upgrade has since started working (it does not today: confirmed live against
  a real server that Music Assistant's websocket handler never reads that header).
- **The bootstrap token still has a nominal one-year `exp` claim; only server-side
  revocation makes its real usable lifetime about two minutes.** Music Assistant's
  admin-mint API (`auth/token/create`) has no primitive for minting an actually
  short-lived token for another user, so the sidecar mints the same kind of long-lived
  token it always does and then revokes it from Music Assistant's database shortly after
  (`BOOTSTRAP_TOKEN_LIFETIME_SECONDS` in `main.py`). This is a real revocation, not an
  expiry claim only the server would enforce eventually - Music Assistant's token
  validation requires the token's database row to still exist, so revoking it is
  immediately fatal to that token regardless of what its JWT payload says. If the sidecar
  is killed before the timer fires (or the revocation call itself fails, e.g. because
  Music Assistant is briefly unreachable), that one token is only cleaned up the next
  time the same Home Assistant user opens the panel, when it's revoked again before a
  fresh one is minted - not before its nominal one-year expiry.
- **A cached token does not immediately reflect a Music Assistant admin disabling that
  user.** The sidecar caches minted API tokens in memory (by design - re-minting on every
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
