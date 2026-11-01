# Home Assistant Add-on: Grafana Alloy

This add-on runs [Grafana Alloy][alloy] to collect logs from your Home Assistant
system and ship them to [Loki][loki].

It started life as a Promtail add-on. Grafana has deprecated Promtail in favour
of Alloy, so this fork now runs Alloy instead. The add-on's slug is still
`promtail` so existing installs keep upgrading in place.

## Install

Add this repository to the add-on store
(`https://github.com/caraar12345/home-assistant-addons`):

[![Open your Home Assistant instance and show the add add-on repository dialog
with a specific repository URL pre-filled.][add-repo-shield]][add-repo]

Then find **Alloy** in the store and click install:

[![Open your Home Assistant instance and show the dashboard of a Supervisor add-on.][add-addon-shield]][add-addon]

## Default setup

By default the add-on reads the systemd journal. That includes the logs of every
add-on, the Supervisor, Home Assistant Core, Docker and the host system itself.
It ships them to the Loki instance set in `client.url`.

The default `client.url` points at the [Loki add-on][addon-loki] by its add-on
hostname. If you run Loki somewhere else, set `client.url` to that instead.

## Configuration

**Note**: _Remember to restart the add-on when the configuration is changed._

Example add-on configuration:

```yaml
client:
  url: https://loki.example.com/loki/api/v1/push
  username: loki
  password: secret
  cafile: ca.pem
additional_scrape_configs: /share/alloy/scrape-configs.alloy
log_level: info
```

**Note**: _This is just an example, don't copy and paste it! Create your own!_

### Option: `client.url` (required)

The full push URL of the Loki deployment Alloy should ship logs to, including
the `/loki/api/v1/push` path.

If you use the Loki add-on, this is `http://39bd2704-loki:3100/loki/api/v1/push`
(use `https` if you enabled SSL on it). For Grafana Cloud, use your stack's Loki
push URL and put the user ID and API token in `client.username` and
`client.password`.

### Option: `client.username`

The username to use if your Loki deployment requires basic auth.

### Option: `client.password`

The password for `client.username`. **Note**: This field is required if
`client.username` is provided.

### Option: `client.cafile`

The CA certificate that signed Loki's certificate, if Loki uses a certificate
your system doesn't already trust.

**Note**: _Give the file name only. The file MUST be stored in `/ssl/`._

### Option: `client.servername`

The server name on Loki's certificate, if you connect using a different name
than the certificate lists (usually when the certificate has a public name and
you're connecting locally).

### Option: `client.certfile`

The absolute path to a client certificate, if Loki uses mTLS to authenticate
clients.

### Option: `client.keyfile`

The absolute path to the key for `client.certfile`. **Note**: This field is
required if `client.certfile` is provided.

### Option: `timezone`

Currently unused. The add-on follows the time zone set in Home Assistant, which
the Supervisor passes in.

### Option: `additional_pipeline_stages`

The absolute path to a file of Alloy `stage.*` blocks to add to the
[default journal config][addon-default-config]. The primary use of this is to
apply additional processing to logs from particular add-ons you use if they are
noisy or difficult to read.

The file must contain only `stage.*` blocks (Alloy syntax, not YAML). They are
inserted inside the default `loki.process "journal"` block, after the stages
already there. If you don't like the default stages, use
`skip_default_scrape_config` and `additional_scrape_configs` to write your own
instead. Here's an example of the contents of this file:

```alloy
stage.match {
  selector = `{container_name="app_cebe7a76_hassio_google_drive_backup"}`

  stage.multiline {
    firstline = `^\x{001b}`
  }
}
```

This particular example applies to the [Google Drive backup add-on][addon-google-drive-backup].
It uses the same log format as Home Assistant and outputs the escape character
at the start of each log line for color-coding in terminals. Looking for that
in a multiline stage makes it so tracebacks are included in the same log entry
as the error that caused them for easier readability.

See the [`loki.process` documentation][alloy-doc-loki-process] for the stages
available and how to configure them.

**Note**: This add-on has access to `/ssl`, `/share` and `/config/alloy`. Place
the file in one of these locations, others will not work.

### Option: `skip_default_scrape_config`

Alloy reads the systemd journal using a pre-defined config you can find
[here][addon-default-config]. If you only want it to look at specific log files,
or you want to replace the default config with your own, set this to `true`.
Then the only components used are the ones in your `additional_scrape_configs`
file.

### Option: `additional_scrape_configs`

The absolute path to a file of additional Alloy components (Alloy syntax, not
YAML), appended to the generated config. The primary use of this is to point
Alloy at additional log files created by add-ons which don't use `stdout` for
all logging. You can also change how the system journal is read by using this
in conjunction with `skip_default_scrape_config`. **Note**: If
`skip_default_scrape_config` is `true` then this field becomes required
(otherwise there would be nothing to collect).

Send logs to `loki.write.default.receiver`, which the add-on defines from the
`client` options. Here's an example of the contents of this file:

```alloy
local.file_match "zigbee2mqtt" {
  path_targets = [{
    __path__ = "/share/zigbee2mqtt/log/**/*.txt",
    job      = "zigbee2mqtt_messages",
  }]
}

loki.source.file "zigbee2mqtt" {
  targets    = local.file_match.zigbee2mqtt.targets
  forward_to = [loki.write.default.receiver]
}
```

This particular example would cause Alloy to collect the MQTT logs that the
[Zigbee2MQTT add-on][addon-z2m] makes by default.

See the [Alloy components reference][alloy-doc-components] for the options
available.

I would also recommend reading the [Loki label best practices][loki-doc-labels]
before writing custom configs. Pipelines are powerful, but avoid making too many
labels: it does more harm than good. Look at what you can do with
[LogQL][logql] at query time instead.

**Note**: This add-on has access to `/ssl`, `/share` and `/config/alloy`. Place
the file in one of these locations, others will not work.

### Option: `log_level`

Controls how much the add-on and Alloy log, which can help when you're dealing
with an unknown issue. Possible values are:

- `trace` or `debug`: Shows detailed debug information.
- `info`: Normal (usually) interesting events.
- `notice` or `warning`: Exceptional occurrences that are not errors.
- `error` or `fatal`: Runtime errors that do not require immediate action.

Alloy has four levels, so `trace` runs as `debug`, `notice` as `warning` and
`fatal` as `error`. Each level includes messages from the more severe levels;
for example, `debug` also shows `info` messages. The default is `info`, which is
the recommended setting unless you are troubleshooting.

### Port: `9080/tcp`

Alloy's HTTP server listens on this port. It serves Alloy's web UI, its own
metrics and the health endpoint the Supervisor watchdog uses. The watchdog
doesn't need the port exposed on the host, so most users should leave it
disabled.

Expose it if you want to browse the Alloy UI (handy for debugging pipelines) or
scrape Alloy's metrics from another system on your network.

## Labels added by the default journal config

| Label | Source journal field |
|---|---|
| `job` | always `systemd-journal` |
| `unit` | `_SYSTEMD_UNIT` |
| `nodename` | `_HOSTNAME` |
| `syslog_identifier` | `SYSLOG_IDENTIFIER` |
| `container_name` | `CONTAINER_NAME` |
| `boot_id` | `_BOOT_ID` (changes on every host reboot) |

## Alloy, Loki and Grafana

Alloy doesn't stand alone: its job is to find logs, process them and ship them
to Loki. Most likely you want all three pieces:

- **Alloy** to collect, process and ship logs (this add-on)
- **Loki** to store and index them
- **Grafana** to query and visualise them

This used to be called the "PLG stack", with Promtail as the collector. Alloy
has replaced Promtail in that role. Grafana doesn't use a new three-letter name
for it; their docs describe Alloy as the collector for their "LGTM" stack (Loki,
Grafana, Tempo, Mimir).

### Loki

You need a Loki instance to send logs to. Options include:

- The original author's [Loki add-on][addon-loki], which runs Loki on your Home
  Assistant system. Note that it hasn't been updated since 2024.
- Your own Loki deployment elsewhere. See the [Loki documentation][loki-doc] for
  setup options.
- Grafana Cloud, which runs Loki and Grafana for you.

### Grafana

Once Loki is receiving logs, connect [Grafana][grafana] to it to query them. The
[Grafana community add-on][addon-grafana] is the easiest way to run Grafana on
Home Assistant. Add Loki as a data source there (see
[Loki in Grafana][loki-in-grafana]).

### Grafana Cloud

Grafana Cloud takes the place of both Loki and Grafana. Create an account, then
point this add-on at your stack's Loki push URL with `client.url`, using
`client.username` and `client.password` for the credentials.

## Versions

Versions follow `<Alloy version>-aaroncarson.<n>`. For example,
`v1.21.3-aaroncarson.1` is the first release of this add-on on Alloy v1.21.3.
`<n>` goes up for add-on changes that don't change the Alloy version.

Changes are tracked in the [repository's commit history and pull
requests][repo].

## Support

This is a personal fork, so please report problems with it on
[this repository's issue tracker][issue]. Please don't raise issues with this
fork with the original authors: their add-ons are separate projects and they
don't maintain this one.

## Credits

This add-on is built on others' work:

- [Mike Degatano (@mdegat01)][mdegat01] created the original
  [Promtail add-on][addon-promtail-upstream] this is based on. See its
  [contributors][contributors-upstream].
- [@LucidityCrash][luciditycrash] forked it to fix some issues.
- This fork migrated the add-on from Promtail to Grafana Alloy.

Thank you to everyone who worked on it before.

## License

MIT License

Copyright (c) 2021-2022 Mike Degatano

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

[add-addon-shield]: https://my.home-assistant.io/badges/supervisor_addon.svg
[add-addon]: https://my.home-assistant.io/redirect/supervisor_addon/?addon=d630ad5e_promtail
[add-repo-shield]: https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg
[add-repo]: https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fcaraar12345%2Fhome-assistant-addons
[addon-default-config]: https://github.com/caraar12345/home-assistant-addons/blob/main/promtail/rootfs/etc/alloy/default-scrape-config.alloy
[addon-google-drive-backup]: https://github.com/sabeechen/hassio-google-drive-backup
[addon-grafana]: https://github.com/hassio-addons/addon-grafana
[addon-loki]: https://github.com/mdegat01/addon-loki
[addon-promtail-upstream]: https://github.com/mdegat01/addon-promtail
[addon-z2m]: https://github.com/zigbee2mqtt/hassio-zigbee2mqtt
[alloy]: https://grafana.com/docs/alloy/latest/
[alloy-doc-components]: https://grafana.com/docs/alloy/latest/reference/components/
[alloy-doc-loki-process]: https://grafana.com/docs/alloy/latest/reference/components/loki/loki.process/
[contributors-upstream]: https://github.com/mdegat01/addon-promtail/graphs/contributors
[grafana]: https://grafana.com/oss/grafana/
[issue]: https://github.com/caraar12345/home-assistant-addons/issues
[logql]: https://grafana.com/docs/loki/latest/query/
[loki]: https://grafana.com/oss/loki/
[loki-doc]: https://grafana.com/docs/loki/latest/
[loki-doc-labels]: https://grafana.com/docs/loki/latest/get-started/labels/bp-labels/
[loki-in-grafana]: https://grafana.com/docs/grafana/latest/datasources/loki/configure/
[luciditycrash]: https://github.com/LucidityCrash/addon-promtail
[mdegat01]: https://github.com/mdegat01
[repo]: https://github.com/caraar12345/home-assistant-addons/commits/main/promtail
