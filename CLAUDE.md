# CLAUDE.md

A Home Assistant add-on (app) store: `repository.yaml` names it, and each
top-level directory is one add-on. Most build here into
`ghcr.io/caraar12345/ha-addon-<slug>`; a few are git submodules that build in
their own repositories.

## This repository is public

Everything here is world-readable: code, docs, commit messages, PR titles and
descriptions, review comments and CI logs. Never put any of these in them:

- private hostnames, domains, internal IPs or URLs of the homelab
- the names of providers or services behind a setup (a seedbox company, an
  ISP account, a VPN provider) or account names on them
- credentials, tokens, or anything that hints at one
- personal details (names, addresses, emails, schedules)

Describe setups generically: "a Transmission 3.00 server behind a reverse
proxy", not who hosts it or where. Examples in docs and option descriptions
use `example` hosts (`http://loki.example:3100`) or well-known defaults
(`192.168.100.1` for a modem). This slipped once, in PR #26's description, so
reread the PR body and every commit message for it before pushing. Forgejo
issue references (`aaroncarson/linear-part-two#<n>`) are fine.

## Layout

- `<addon>/`: one add-on, built by `.github/workflows/build-addons.yml` when
  listed in its `ADDONS`. Current ones: `promtail`, `cadvisor`,
  `cloudflare-speedtest`, `ma-ingress-proxy`, `prometheus`, `scanservjs`,
  `transmission-mqtt`, `vm-hub5-exporter`.
- Submodules (`.gitmodules`): `caddy-2`, `caddy-webhook`, `grafana`,
  `hass-n8n`, `speedtest2mqtt`, `tvheadend`. Their code and builds live in
  their own repos. `auto-update.yaml` bumps a submodule pointer when that
  repo sends an `addon_update` `repository_dispatch` (or by hand from the
  Actions tab); Dependabot also bumps them daily. Grafana builds here, through
  `docker-build-grafana.yaml` (home-assistant/builder), not `build-addons.yml`.
- Legacy add-ons not in `ADDONS` and not built by CI (`configurator`,
  `portainer`, `tailscale`, `fluent-bit`, `scrypted`,
  `SuperQ_smokeping_prober`): leave them alone unless asked. Moving one onto
  the shared build means making it follow the rules below.
- `.github/dependabot.yml`: submodules daily, Actions weekly in one grouped PR.
  Actions are pinned by commit SHA with the tag in a comment; keep it that way.

## Adding an add-on

Copy the shape of `transmission-mqtt` or `vm-hub5-exporter` (small Python
services) or `prometheus` (wraps an upstream binary):

```
<slug>/
  config.yaml         # name, version, slug, description, url, arch, image, init: false, options/schema
  Dockerfile          # ARG BUILD_FROM=ghcr.io/hassio-addons/base:<ver>, then the label block
  build.yaml          # optional: only to pin a different BUILD_FROM (e.g. debian-base)
  DOCS.md             # the Documentation tab: what it does, entities/metrics, every option
  README.md           # two or three lines and a link to DOCS.md
  translations/en.yaml  # a name and description for every option (and network: for each port)
  rootfs/
    etc/services.d/<slug>/run      # s6 service: #!/command/with-contenv bashio, exec the program
    etc/services.d/<slug>/finish   # halt the add-on on a crash (copy an existing one)
    opt/<slug>/<program>.py
```

Then add the directory to `ADDONS` in `build-addons.yml`. The workflow checks,
and fails the PR otherwise, that:

- `image` is exactly `ghcr.io/caraar12345/ha-addon-<slug>`. The slug can't
  change later: Home Assistant would treat it as a different add-on.
- `arch` is exactly `aarch64` and `amd64` (each builds natively on its own
  runner, then one multi-arch image is published).
- a `build.yaml`, if there is one, names the same multi-arch image for both
  arches.

**Editing `build-addons.yml` rebuilds every add-on in `ADDONS` on that PR**
(without the version check) to prove the pipeline still works. That is
expected, and slow; adding an add-on to `ADDONS` triggers it too. Keep
unrelated workflow edits out of add-on PRs.

## Versions and releases

- `config.yaml`'s `version` is the image tag Home Assistant checks for
  updates. **Bump it with every change to an add-on**, docs included: the
  build fails, on the PR, if that version is already published.
- Own code uses plain semver (`1.0.1`). An add-on wrapping upstream software
  uses `v<upstream>-aaroncarson.<n>` (`v3.16.0-aaroncarson.1`), resetting
  `<n>` when upstream moves.
- A PR builds changed add-ons without pushing (and warms the GHCR layer
  cache). The merge to `main` builds from that cache and publishes
  `latest`, `<version>` and the commit SHA. That merge is the release; there
  is no tag or GitHub release. A `workflow_dispatch` can rebuild one add-on
  or `all`, with `republish` to overwrite an existing version.
- Add a `CHANGELOG.md` entry if the add-on has one (most don't).

## Base image and dependencies

- Default base: `ghcr.io/hassio-addons/base` (Alpine), pinned in the
  Dockerfile's `ARG BUILD_FROM` with a link to its releases. `promtail` and
  `ma-ingress-proxy` use `debian-base` via `build.yaml`.
- Install Python and libraries with `apk add --no-cache python3 py3-<lib>`,
  not pip. **Alpine ships older versions than PyPI, and code must work with
  the distro's.** For example the base ships paho-mqtt 1.6.1, so
  `transmission-mqtt` uses `mqtt.CallbackAPIVersion` only when it exists.
  Check what you're getting before relying on an API:
  `docker run --rm --entrypoint sh ghcr.io/hassio-addons/base:<ver> -c 'apk add -q --no-cache py3-<lib> && apk info -v py3-<lib>'`.
  The version moves when the base image is bumped, so keep fallbacks for the
  older API rather than assuming the newer one.
- Prefer the standard library where it's enough: `vm-hub5-exporter` and
  `transmission-mqtt` do HTTP with `urllib` and need no other packages.
- The Dockerfile `chmod a+x`s the s6 scripts (don't rely on git file modes)
  and runs `python3 -m py_compile` on the program, so a syntax error fails
  the build.

## Conventions

- **Options**: the program reads `/data/options.json` itself, with an
  environment variable to point it elsewhere for local runs
  (`TRANSMISSION_MQTT_OPTIONS`). The s6 `run` script only does what needs
  bashio: e.g. filling `MQTT_HOST`/`PORT`/`USERNAME`/`PASSWORD` from
  `bashio::services mqtt` (with `services: [mqtt:want]` in `config.yaml`) when
  no broker is configured, and exporting `BUILD_VERSION`.
- **Schema**: secrets are `password?`, optional fields end in `?`, numbers
  get ranges (`int(10,3600)`), choices are `list(a|b)`. Give `log_level` as
  `list(debug|info|warning|error)?`. Every option needs an entry in
  `translations/en.yaml` and in DOCS.md's options table.
- **Services**: `init: false`; the `finish` script halts the add-on on a
  crash (exit code other than 0 or 256) so the Supervisor's watchdog
  restarts it. Expose a `watchdog:` URL when there's an HTTP endpoint
  (`vm-hub5-exporter`'s `/healthz`, `prometheus`'s `/-/healthy`).
- **MQTT add-ons**: publish through MQTT discovery as one device, set
  availability so entities go unavailable when the source or the add-on is
  down, and match the names and units of the equivalent core integration
  where there is one (as `transmission-mqtt` does), so the new entities can
  take over the old entity IDs and their history once the integration's
  entry is deleted (its DOCS.md describes the steps).
- **Labels**: keep the Dockerfile's `io.hass.*` / `org.opencontainers.*`
  block; the workflow passes every `BUILD_*` argument it uses.
- **Writing**: DOCS.md and option descriptions in plain, specific sentences
  for someone installing the add-on, generic about the setup it was written
  for (see the top of this file).
- Shell: `set -euo pipefail` in scripts, shellcheck clean.

## Testing

There's no repo-wide test suite. Before opening a PR:

- Run the program locally against its real target where possible, with an
  options file and the env var override (and a local broker such as
  `eclipse-mosquitto` for MQTT add-ons).
- Build the image for at least one arch:
  `docker build --build-arg BUILD_ARCH=amd64 -t <slug>:test <slug>`
  (add `--build-arg BUILD_FROM=...` when there's a `build.yaml`). The PR's CI
  builds both arches; it doesn't run the add-on.
- `ma-ingress-proxy/test/` has a compose-based smoke test harness standing in
  for the Supervisor's ingress network (see its `run_tests.sh`).
- On the Home Assistant box, a copy in `/addons/<slug>` runs as
  `local_<slug>`. Delete the `image` line from that copy's `config.yaml`
  first, or the Supervisor pulls the published image instead of building
  your change (never commit it removed). Then `ha supervisor reload`,
  `ha addon install local_<slug>` (or `ha addon update local_<slug>` if it's
  already installed), `ha addon start local_<slug>` and
  `ha addon logs local_<slug>`.
- After merge, check the published add-on updates in Home Assistant and its
  logs look right.

## Workflow

- Track work as issues in Forgejo `aaroncarson/linear-part-two`; creating and updating them is always allowed. Older
  work is in Linear (team AARON), so search both. Branch `lp2-<n>` and
  reference `aaroncarson/linear-part-two#<n>` in commits.
- One add-on per PR where possible, small self-contained commits, and the
  version bump in the same PR as the change.
- Ask before merging: a merge publishes the add-on.
