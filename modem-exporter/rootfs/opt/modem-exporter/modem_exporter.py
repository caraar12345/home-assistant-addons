#!/usr/bin/env python3
"""Prometheus exporter for the Virgin Media (GB) Hub 5 (its REST API only).

Ported from caraar12345/monitoring-config (roles/modem_exporter). Metric
names and labels are unchanged, so existing dashboards and alerts still work.

Exposes:
  - Modem state (uptime, operational status, DOCSIS version, identity info)
  - Registration state (registration complete, downstream locked)
  - Modem mode (bridge/router toggle)
  - Downstream channel metrics (power, SNR, RxMER, corrected/uncorrected
    errors, lock status, primary)
  - Upstream channel metrics (power, symbol rate, T1-T4 timeouts, lock
    status, primary)
  - Service flow max/min rates and burst sizes
  - Optionally pushes new modem event log entries to Loki

Configuration comes from the add-on options (/data/options.json, or the file
named by MODEM_EXPORTER_OPTIONS).
"""

import base64
import json
import logging
import os
import ssl
import time
import urllib.error
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock, Thread

OPTIONS_FILE = os.environ.get("MODEM_EXPORTER_OPTIONS", "/data/options.json")
PORT = int(os.environ.get("MODEM_EXPORTER_PORT", "9781"))

log = logging.getLogger("modem_exporter")


class Config:
    def __init__(self, opts: dict):
        self.modem_url = opts["modem_url"].rstrip("/")
        self.verify_ssl = bool(opts.get("verify_ssl", False))
        self.scrape_interval = int(opts.get("scrape_interval", 30))
        self.request_timeout = int(opts.get("request_timeout", 10))
        self.loki_url = (opts.get("loki_url") or "").rstrip("/")
        self.loki_username = opts.get("loki_username") or ""
        self.loki_password = opts.get("loki_password") or ""
        self.loki_labels = {
            "job": opts.get("loki_job") or "modem_eventlog",
            "host": opts.get("loki_host") or "modem",
        }
        self.log_level = (opts.get("log_level") or "info").upper()

        if self.verify_ssl:
            self.ssl_ctx = ssl.create_default_context()
        else:
            # The Hub 5 serves a self-signed certificate.
            self.ssl_ctx = ssl.create_default_context()
            self.ssl_ctx.check_hostname = False
            self.ssl_ctx.verify_mode = ssl.CERT_NONE


def load_config() -> Config:
    with open(OPTIONS_FILE, encoding="utf-8") as f:
        return Config(json.load(f))


CFG: Config

# The Hub 5's largest response (the event log) is tens of KB.
MAX_RESPONSE_BYTES = 4 * 1024 * 1024

_lock = Lock()
_metrics_body = ""
_last_scrape_end = time.monotonic()
_seen_log_entries: set[str] = set()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Loki's push API never redirects, and following one would forward the
    Authorization header to wherever it points."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, f"refusing redirect to {newurl}", headers, fp)


_loki_opener = urllib.request.build_opener(_NoRedirect)


def fetch(path: str) -> dict:
    url = f"{CFG.modem_url}{path}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, context=CFG.ssl_ctx, timeout=CFG.request_timeout) as resp:
        body = resp.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError(f"{path}: response larger than {MAX_RESPONSE_BYTES} bytes")
    return json.loads(body)


# --- Exposition -------------------------------------------------------------


def _escape(v) -> str:
    return str(v).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


class Metrics:
    """Collects samples grouped by metric name, so each metric gets exactly
    one HELP and TYPE line however many label sets it has."""

    def __init__(self):
        self._families: dict[str, tuple[str, str, list[str]]] = {}

    def add(self, name: str, help_text: str, value, labels: dict | None = None, kind: str = "gauge"):
        if name not in self._families:
            self._families[name] = (help_text, kind, [])
        label_str = ""
        if labels:
            label_str = "{" + ",".join(f'{k}="{_escape(v)}"' for k, v in labels.items()) + "}"
        self._families[name][2].append(f"{name}{label_str} {value}")

    def render(self) -> str:
        lines: list[str] = []
        for name, (help_text, kind, samples) in self._families.items():
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} {kind}")
            lines.extend(samples)
        return "\n".join(lines) + "\n"


def _bool(v) -> int:
    return 1 if v else 0


# --- Collectors -------------------------------------------------------------


def collect_state(m: Metrics) -> None:
    cm = fetch("/cablemodem/state_").get("cablemodem", {})
    m.add("modem_uptime_seconds", "Modem uptime in seconds since last boot", cm.get("upTime", 0))
    m.add(
        "modem_operational",
        "Whether the modem reports status=operational (1=yes, 0=no)",
        _bool(cm.get("status") == "operational"),
    )
    m.add(
        "modem_access_allowed",
        "Whether network access is allowed by the CMTS (1=yes, 0=no)",
        _bool(cm.get("accessAllowed", False)),
    )
    m.add(
        "modem_baseline_privacy_enabled",
        "Whether BPI+ encryption is enabled (1=yes, 0=no)",
        _bool(cm.get("baselinePrivacyEnabled", False)),
    )
    m.add(
        "modem_max_cpes",
        "Maximum number of CPEs (customer premises equipment) the modem will serve",
        cm.get("maxCPEs", 0),
    )
    m.add(
        "modem_info",
        "Modem identity and configuration as labels (value is always 1)",
        1,
        {
            "docsis_version": cm.get("docsisVersion", "unknown"),
            "mac_address": cm.get("macAddress", "unknown"),
            "serial_number": cm.get("serialNumber", "unknown"),
            "boot_filename": cm.get("bootFilename", "unknown"),
            "status": cm.get("status", "unknown"),
        },
    )


def collect_registration(m: Metrics) -> None:
    reg = fetch("/cablemodem/registration").get("registration", {})
    m.add(
        "modem_registration_complete",
        "Whether modem registration with CMTS is complete (1=yes, 0=no)",
        _bool(reg.get("registrationComplete", False)),
    )
    m.add(
        "modem_downstream_all_locked",
        "Whether all downstream channels are locked (1=yes, 0=no)",
        _bool(reg.get("downstreamLocked", False)),
    )


def collect_modemmode(m: Metrics) -> None:
    data = fetch("/system/modemmode")
    m.add(
        "modem_mode_enabled",
        "Whether the modem is enabled (1=yes, 0=no - e.g. bridge mode off)",
        _bool(data.get("modemmode", {}).get("enable", False)),
    )


def _primary_channel(direction: str) -> dict | None:
    """Identifying fields of the primary channel, matched by channelId if
    present, otherwise frequency (upstream primary_ only returns frequency).
    None if the request failed, so the primary metric is left out rather
    than reporting every channel as non-primary."""
    try:
        return fetch(f"/cablemodem/{direction}/primary_").get("channel", {}) or {}
    except Exception as e:
        log.warning("Failed to fetch primary %s channel, omitting modem_%s_primary: %s", direction, direction, e)
        return None


def _is_primary(ch: dict, primary: dict) -> bool:
    if not primary:
        return False
    if "channelId" in primary:
        return ch.get("channelId") == primary["channelId"]
    if "frequency" in primary:
        return ch.get("frequency") == primary["frequency"]
    return False


def _channel_labels(ch: dict) -> dict:
    return {
        "channel_id": str(ch["channelId"]),
        "channel_type": ch.get("channelType", "unknown"),
        "frequency_hz": str(ch.get("frequency", 0)),
        "modulation": ch.get("modulation", "unknown"),
    }


def collect_downstream(m: Metrics) -> None:
    data = fetch("/cablemodem/downstream")
    primary = _primary_channel("downstream")
    for ch in data.get("downstream", {}).get("channels", []):
        labels = _channel_labels(ch)
        m.add(
            "modem_downstream_power_dBmV", "Downstream channel receive power level in dBmV", ch.get("power", 0), labels
        )
        m.add("modem_downstream_snr_dB", "Downstream channel signal-to-noise ratio in dB", ch.get("snr", 0), labels)
        m.add(
            "modem_downstream_rx_mer_dB",
            "Downstream channel receive modulation error ratio in dB",
            ch.get("rxMer", 0),
            labels,
        )
        m.add(
            "modem_downstream_corrected_errors_total",
            "Downstream channel correctable codeword errors (resets on reboot)",
            ch.get("correctedErrors", 0),
            labels,
            "counter",
        )
        m.add(
            "modem_downstream_uncorrected_errors_total",
            "Downstream channel uncorrectable codeword errors (resets on reboot)",
            ch.get("uncorrectedErrors", 0),
            labels,
            "counter",
        )
        m.add(
            "modem_downstream_channel_locked",
            "Whether the downstream channel is locked (1=yes, 0=no)",
            _bool(ch.get("lockStatus", False)),
            labels,
        )
        if primary is not None:
            m.add(
                "modem_downstream_primary",
                "Whether this is the primary downstream channel (1=yes, 0=no)",
                _bool(_is_primary(ch, primary)),
                labels,
            )


def collect_upstream(m: Metrics) -> None:
    data = fetch("/cablemodem/upstream")
    primary = _primary_channel("upstream")
    for ch in data.get("upstream", {}).get("channels", []):
        labels = _channel_labels(ch)
        m.add("modem_upstream_power_dBmV", "Upstream channel transmit power level in dBmV", ch.get("power", 0), labels)
        m.add(
            "modem_upstream_channel_locked",
            "Whether the upstream channel is locked (1=yes, 0=no)",
            _bool(ch.get("lockStatus", False)),
            labels,
        )
        m.add(
            "modem_upstream_t1_timeouts",
            "Upstream T1 (ranging request) timeouts (resets on reboot)",
            ch.get("t1Timeout", 0),
            labels,
        )
        m.add(
            "modem_upstream_t2_timeouts",
            "Upstream T2 (ranging response) timeouts (resets on reboot)",
            ch.get("t2Timeout", 0),
            labels,
        )
        m.add(
            "modem_upstream_t3_timeouts",
            "Upstream T3 (no ranging response) timeouts - key stability indicator (resets on reboot)",
            ch.get("t3Timeout", 0),
            labels,
        )
        m.add(
            "modem_upstream_t4_timeouts",
            "Upstream T4 (no maintenance opportunity) timeouts (resets on reboot)",
            ch.get("t4Timeout", 0),
            labels,
        )
        m.add(
            "modem_upstream_symbol_rate_ksps",
            "Upstream channel symbol rate in ksym/s",
            ch.get("symbolRate", 0),
            labels,
        )
        if primary is not None:
            m.add(
                "modem_upstream_primary",
                "Whether this is the primary upstream channel (1=yes, 0=no)",
                _bool(_is_primary(ch, primary)),
                labels,
            )


def collect_serviceflows(m: Metrics) -> None:
    data = fetch("/cablemodem/serviceflows")
    for entry in data.get("serviceFlows", []):
        sf = entry.get("serviceFlow", {})
        labels = {
            "flow_id": str(sf.get("serviceFlowId", 0)),
            "direction": sf.get("direction", "unknown"),
            "schedule_type": sf.get("scheduleType", "unknown"),
        }
        m.add(
            "modem_service_flow_max_traffic_rate_bps",
            "Service flow maximum traffic rate in bits/sec",
            sf.get("maxTrafficRate", 0),
            labels,
        )
        m.add(
            "modem_service_flow_max_traffic_burst_bytes",
            "Service flow maximum traffic burst in bytes",
            sf.get("maxTrafficBurst", 0),
            labels,
        )
        m.add(
            "modem_service_flow_min_reserved_rate_bps",
            "Service flow minimum reserved traffic rate in bits/sec",
            sf.get("minReservedRate", 0),
            labels,
        )
        m.add(
            "modem_service_flow_max_concatenated_burst_bytes",
            "Service flow maximum concatenated burst in bytes",
            sf.get("maxConcatenatedBurst", 0),
            labels,
        )


COLLECTORS = (
    collect_state,
    collect_registration,
    collect_modemmode,
    collect_downstream,
    collect_upstream,
    collect_serviceflows,
)


# --- Loki -------------------------------------------------------------------


def _ts_ns(entry: dict) -> str:
    try:
        # ISO 8601, e.g. "2026-04-24T20:08:58.000Z"
        dt = datetime.fromisoformat(entry["time"].replace("Z", "+00:00"))
        return str(int(dt.timestamp() * 1e9))
    except Exception:
        return str(int(time.time() * 1e9))


def push_to_loki(entries: list[dict]) -> bool:
    """Returns True if Loki accepted the entries."""
    by_priority: dict[str, list] = {}
    for e in entries:
        by_priority.setdefault(e.get("priority", "unknown"), []).append([_ts_ns(e), e.get("message", "")])

    streams = [
        {"stream": {**CFG.loki_labels, "priority": p}, "values": sorted(v, key=lambda x: x[0])}
        for p, v in by_priority.items()
    ]
    headers = {"Content-Type": "application/json"}
    if CFG.loki_username:
        token = base64.b64encode(f"{CFG.loki_username}:{CFG.loki_password}".encode()).decode()
        headers["Authorization"] = f"Basic {token}"
    req = urllib.request.Request(
        f"{CFG.loki_url}/loki/api/v1/push",
        data=json.dumps({"streams": streams}).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with _loki_opener.open(req, timeout=CFG.request_timeout) as resp:
            if resp.status in (200, 204):
                return True
            log.warning("Loki push returned %s", resp.status)
    except urllib.error.HTTPError as e:
        body = e.read(500).decode(errors="replace") if e.fp else ""
        log.warning("Failed to push to Loki: %s %s", e, body)
    except Exception as e:
        log.warning("Failed to push to Loki: %s", e)
    return False


def poll_eventlog() -> None:
    entries = fetch("/cablemodem/eventlog").get("eventlog", [])
    new = {}
    for e in entries:
        key = f"{e.get('time', '')}|{e.get('message', '')}"
        if key not in _seen_log_entries:
            new[key] = e
    # Only remember entries once Loki has them, so a failed push is retried.
    if new and push_to_loki(list(new.values())):
        log.info("Pushed %d new event log entries to Loki", len(new))
        _seen_log_entries.update(new)


# --- Main loop and HTTP server ----------------------------------------------


def scrape_once() -> None:
    global _metrics_body
    m = Metrics()
    start = time.monotonic()
    try:
        for collect in COLLECTORS:
            collect(m)
        success = 1
    except Exception as e:
        log.error("Scrape failed: %s", e)
        m = Metrics()
        success = 0
    m.add("modem_scrape_success", "Whether the last modem scrape succeeded", success)
    m.add(
        "modem_scrape_duration_seconds",
        "How long the last modem scrape took",
        round(time.monotonic() - start, 3),
    )
    with _lock:
        _metrics_body = m.render()

    if CFG.loki_url:
        try:
            poll_eventlog()
        except Exception as e:
            log.error("Event log poll failed: %s", e)


def scrape_loop() -> None:
    global _last_scrape_end
    while True:
        scrape_once()
        _last_scrape_end = time.monotonic()
        time.sleep(CFG.scrape_interval)


def healthy() -> bool:
    """False if the polling loop looks stuck (e.g. a modem that never
    finishes a response), so the Supervisor watchdog restarts the add-on.
    A poll makes up to 9 modem requests plus a Loki push, each bounded by
    request_timeout per blocking read."""
    allowance = 3 * CFG.scrape_interval + 10 * CFG.request_timeout * 3
    return time.monotonic() - _last_scrape_end < allowance


class MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ("/metrics", "/metrics/", "/healthz"):
            self.send_response(404)
            self.end_headers()
            return
        status = 200
        if self.path == "/healthz":
            ok = healthy()
            status = 200 if ok else 503
            body = b"ok\n" if ok else b"polling loop stalled\n"
        else:
            with _lock:
                body = _metrics_body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


def main() -> None:
    global CFG
    CFG = load_config()
    logging.basicConfig(level=CFG.log_level, format="%(asctime)s %(levelname)s %(message)s")
    log.info("Scraping %s every %ds", CFG.modem_url, CFG.scrape_interval)
    if CFG.loki_url:
        log.info("Pushing the modem event log to %s", CFG.loki_url)
        if CFG.loki_username and not CFG.loki_url.startswith("https://"):
            log.warning("loki_username is set but loki_url isn't HTTPS: the credentials are sent unencrypted")
    else:
        log.info("No loki_url set, not pushing the modem event log")
    Thread(target=scrape_loop, daemon=True).start()
    log.info("Listening on :%d", PORT)
    ThreadingHTTPServer(("", PORT), MetricsHandler).serve_forever()


if __name__ == "__main__":
    main()
