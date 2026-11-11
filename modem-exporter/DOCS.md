# Modem Exporter (VM Hub 5)

Polls a Virgin Media Hub 5's REST API and exposes what it finds as Prometheus
metrics on port 9781 (`/metrics`). It can also push new modem event log
entries to Loki.

**It only supports the Virgin Media (GB) Hub 5.** Other modems, including
earlier Virgin Media Hubs, have different APIs and won't work. The Hub 5
serves the API at `https://<hub>/rest/v1`, and in modem mode it's usually at
`192.168.100.1`.

## Options

| Option | Default | Description |
| --- | --- | --- |
| `modem_url` | `https://192.168.100.1/rest/v1` | Base URL of the Hub 5's REST API. |
| `verify_ssl` | `false` | Check the Hub's TLS certificate. The Hub 5 uses a self-signed one, so leave this off unless yours is valid. |
| `scrape_interval` | `30` | Seconds between polls of the modem. |
| `request_timeout` | `10` | Seconds to wait for each request to the modem or Loki. |
| `loki_url` | | Base URL of a Loki server, e.g. `http://loki.example:3100`. Leave empty to not push the event log. Use `https://` if you set credentials, or they're sent unencrypted. |
| `loki_username` / `loki_password` | | Basic auth for Loki, if needed (e.g. Grafana Cloud). |
| `loki_job` | `modem_eventlog` | `job` label on pushed log lines. |
| `loki_host` | `modem` | `host` label on pushed log lines. |
| `log_level` | `info` | `debug`, `info`, `warning` or `error`. |

The metrics port is set in the add-on's **Network** section (default 9781).

## Scraping it

```yaml
scrape_configs:
  - job_name: modem
    scrape_interval: 30s
    static_configs:
      - targets: ["homeassistant.local:9781"]
        labels:
          instance: modem
```

The exporter polls the modem on its own schedule and serves the latest
result, so a scrape never waits on the modem. Match `scrape_interval` to the
option of the same name.

## Metrics

All metrics start with `modem_`:

- State: `uptime_seconds`, `operational`, `access_allowed`,
  `baseline_privacy_enabled`, `max_cpes`, `info` (identity as labels)
- Registration: `registration_complete`, `downstream_all_locked`,
  `mode_enabled`
- Downstream, per channel: `downstream_power_dBmV`, `downstream_snr_dB`,
  `downstream_rx_mer_dB`, `downstream_corrected_errors_total`,
  `downstream_uncorrected_errors_total`, `downstream_channel_locked`,
  `downstream_primary`
- Upstream, per channel: `upstream_power_dBmV`, `upstream_channel_locked`,
  `upstream_t1_timeouts` to `upstream_t4_timeouts`,
  `upstream_symbol_rate_ksps`, `upstream_primary`
- Service flows: `service_flow_max_traffic_rate_bps`,
  `service_flow_max_traffic_burst_bytes`,
  `service_flow_min_reserved_rate_bps`,
  `service_flow_max_concatenated_burst_bytes`
- Exporter: `scrape_success`, `scrape_duration_seconds`

If any request to the modem fails, only `modem_scrape_success 0` (and the
duration) are exposed until the next successful poll. The exception is the
primary-channel lookup: if that fails, only the `*_primary` metrics are
left out.

`/healthz` returns 503 if polling has stalled, so the Supervisor watchdog
restarts the add-on.

## Event log

With `loki_url` set, each poll fetches the modem's event log and pushes
entries it hasn't sent before, one stream per `priority`. This happens even
when the metrics requests fail. Entries Loki doesn't accept are retried on
the next poll. The add-on only remembers what it has sent while it's
running, so after a restart it sends the whole log again. Loki drops lines
that are exact duplicates.
