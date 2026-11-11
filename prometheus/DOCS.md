# Prometheus

[Prometheus](https://prometheus.io) time-series database and monitoring
system, with a pre-configured scrape job for Home Assistant.

This is a fork of the unmaintained
[Home Assistant Community Add-on](https://github.com/hassio-addons/addon-prometheus)
(last updated for Prometheus 2.40), updated to Prometheus 3.

## Installation

1. Add this repository to Home Assistant's add-on store.
1. Install the "Prometheus" add-on and start it.
1. Open the web UI from the sidebar (it works through Ingress).

## Scraping Home Assistant

The `home-assistant` job scrapes `/api/prometheus` through the Supervisor
using the add-on's own token, so no long-lived access token is needed. Turn
on Home Assistant's
[Prometheus integration](https://www.home-assistant.io/integrations/prometheus/)
by adding this to `configuration.yaml`:

```yaml
prometheus:
```

## Adding scrape targets

Put one file per job in `/share/prometheus/targets/` (`.yaml` or `.yml`). A
file can hold a single scrape job, or a list of them:

```yaml
---
job_name: octoprint
scrape_interval: 5s
metrics_path: /plugin/prometheus_exporter/metrics
params:
  apikey: ["VERYSECRETAPIKEY"]
static_configs:
  - targets: ["octoprint.example.org:5000"]
```

The add-on watches that directory. When a file changes, it rebuilds the
config, checks it with `promtool` and reloads Prometheus. If the result is
invalid (bad YAML, a duplicate `job_name`, an unknown field), it keeps the
current config and logs the error. At start-up, invalid targets are skipped
and only the built-in jobs run.

The `prometheus` and `home-assistant` job names are already in use.

## Rules

Recording and alerting rules go in `/share/prometheus/rules/` (`*.yaml` or
`*.yml`). The add-on reloads Prometheus when a rule file changes. If a rule
file is invalid, Prometheus keeps the rules it already has and logs the
error.

## Options

| Option | Default | Description |
| --- | --- | --- |
| `log_level` | `info` | `debug`, `info`, `warn` or `error`. |
| `retention_time` | `15d` | How long to keep data (e.g. `30d`, `8w`, `1y`). |
| `retention_size` | | Optional size cap for stored data (e.g. `10GB`); the oldest blocks are removed first. |
| `env_vars` | `[]` | Extra environment variables for Prometheus, as `name`/`value` pairs. |

## Using Prometheus from Grafana

Add a Prometheus data source pointing at `http://<hostname>:9090`, where
`<hostname>` is shown on the add-on's info page (it looks like
`xxxxxxxx-prometheus`).

## Upgrading from Prometheus 2

The Prometheus 3 data format is compatible with 2.x, but this add-on has a
different slug from the community add-on, so it starts with an empty
database. Prometheus 3's
[migration guide](https://prometheus.io/docs/prometheus/latest/migration/)
covers config changes that might affect your own target files.

## Licence

MIT. See [LICENSE.md](LICENSE.md) for the original add-on's copyright.
