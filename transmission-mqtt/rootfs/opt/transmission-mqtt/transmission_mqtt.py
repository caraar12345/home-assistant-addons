#!/usr/bin/env python3
"""Publish a Transmission server to Home Assistant over MQTT discovery.

Home Assistant's core Transmission integration only accepts Transmission
4.0 and later. This polls any Transmission 2.80+ RPC server (written for a
3.00 seedbox) and publishes the same entities the core integration has:

  - Sensors: download/upload speed, status, available disk space, active,
    paused, total, completed and started torrents (with the core
    integration's torrent_info attribute), session and total download and
    upload, session and total ratio
  - Switches: Switch (start/stop all torrents) and Turtle mode
  - An event entity, Torrent: started, downloaded and removed

Units are the ones the core integration shows by default (MB/s and GiB), so
history carries on if these take over the old entity IDs.

Configuration comes from the add-on options (/data/options.json, or the file
named by TRANSMISSION_MQTT_OPTIONS). The MQTT broker comes from the options
if set, otherwise from MQTT_HOST, MQTT_PORT, MQTT_USERNAME and MQTT_PASSWORD,
which the add-on's run script fills in from Home Assistant's MQTT service.
"""

import base64
import http.client
import json
import logging
import os
import queue
from collections import deque
import re
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import paho.mqtt.client as mqtt

OPTIONS_FILE = os.environ.get("TRANSMISSION_MQTT_OPTIONS", "/data/options.json")
VERSION = os.environ.get("BUILD_VERSION", "dev")
SOURCE_URL = "https://github.com/caraar12345/home-assistant-addons/tree/main/transmission-mqtt"

# How long to wait after a switch command before polling, as the core
# integration does (Transmission applies some changes asynchronously).
AFTER_WRITE_SLEEP = 2

# Torrent events kept for publishing while the MQTT broker is unreachable.
EVENT_BUFFER = 100

# Transmission's numeric torrent status, named as transmission-rpc names them
# (and so as the core integration reports them).
TORRENT_STATUS = {
    0: "stopped",
    1: "check pending",
    2: "checking",
    3: "download pending",
    4: "downloading",
    5: "seed pending",
    6: "seeding",
}

# Which statuses each sensor's torrent list holds (core's FILTER_MODES).
FILTER_MODES: dict[str, set[str] | None] = {
    "started": {"downloading"},
    "completed": {"seeding"},
    "paused": {"stopped"},
    "active": {"seeding", "downloading"},
    "total": None,
}

ORDER_MODES = {
    "newest_first": (lambda t: t["addedDate"], True),
    "oldest_first": (lambda t: t["addedDate"], False),
    "worst_ratio_first": (lambda t: t["uploadRatio"], False),
    "best_ratio_first": (lambda t: t["uploadRatio"], True),
}

TORRENT_FIELDS = [
    "id",
    "name",
    "status",
    "percentDone",
    "uploadRatio",
    "eta",
    "addedDate",
    "downloadDir",
    "labels",
]

MB = 1000**2
GIB = 1024**3

log = logging.getLogger("transmission_mqtt")


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "transmission"


@dataclass
class Config:
    name: str
    url: str
    username: str
    password: str
    verify_ssl: bool
    scan_interval: int
    request_timeout: int
    limit: int
    order: str
    mqtt_host: str
    mqtt_port: int
    mqtt_username: str
    mqtt_password: str
    discovery_prefix: str
    log_level: str
    node_id: str = field(init=False)
    base_topic: str = field(init=False)

    def __post_init__(self) -> None:
        self.node_id = slugify(self.name)
        self.base_topic = f"transmission_mqtt/{self.node_id}"


def load_config() -> Config:
    with open(OPTIONS_FILE, encoding="utf-8") as f:
        opts = json.load(f)

    scheme = "https" if opts.get("ssl", False) else "http"
    path = "/" + (opts.get("path") or "/transmission/rpc").lstrip("/")
    url = f"{scheme}://{opts['host']}:{int(opts.get('port', 9091))}{path}"

    mqtt_host = opts.get("mqtt_host") or os.environ.get("MQTT_HOST", "")
    if not mqtt_host:
        raise SystemExit(
            "No MQTT broker: install the Mosquitto broker app (or another MQTT "
            "integration), or set mqtt_host in this app's options."
        )
    return Config(
        name=opts.get("name") or "Transmission",
        url=url,
        username=opts.get("username") or "",
        password=opts.get("password") or "",
        verify_ssl=bool(opts.get("verify_ssl", True)),
        scan_interval=int(opts.get("scan_interval", 120)),
        request_timeout=int(opts.get("request_timeout", 10)),
        limit=int(opts.get("limit", 10)),
        order=opts.get("order") or "oldest_first",
        mqtt_host=mqtt_host,
        mqtt_port=int(opts.get("mqtt_port") or os.environ.get("MQTT_PORT") or 1883),
        mqtt_username=opts.get("mqtt_username") or os.environ.get("MQTT_USERNAME", ""),
        mqtt_password=opts.get("mqtt_password") or os.environ.get("MQTT_PASSWORD", ""),
        discovery_prefix=(opts.get("discovery_prefix") or "homeassistant").strip("/"),
        log_level=(opts.get("log_level") or "info").upper(),
    )


# --------------------------------------------------------------------------
# Transmission RPC
# --------------------------------------------------------------------------


class TransmissionError(Exception):
    pass


class Transmission:
    """A minimal Transmission RPC client (standard library only)."""

    def __init__(self, cfg: Config):
        self.url = cfg.url
        self.timeout = cfg.request_timeout
        self.session_id = ""
        self.auth = ""
        if cfg.username or cfg.password:
            token = base64.b64encode(f"{cfg.username}:{cfg.password}".encode()).decode()
            self.auth = f"Basic {token}"
        self.ssl_ctx = ssl.create_default_context()
        if not cfg.verify_ssl:
            self.ssl_ctx.check_hostname = False
            self.ssl_ctx.verify_mode = ssl.CERT_NONE

    def call(self, method: str, arguments: dict | None = None) -> dict:
        body = json.dumps({"method": method, "arguments": arguments or {}}).encode()
        # A 409 hands out the CSRF session id to retry with; it changes when
        # Transmission restarts, so allow one retry per call.
        for _ in range(2):
            headers = {"Content-Type": "application/json"}
            if self.session_id:
                headers["X-Transmission-Session-Id"] = self.session_id
            if self.auth:
                headers["Authorization"] = self.auth
            req = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout, context=self.ssl_ctx) as resp:
                    reply = json.load(resp)
            except urllib.error.HTTPError as err:
                if err.code == 409 and err.headers.get("X-Transmission-Session-Id"):
                    self.session_id = err.headers["X-Transmission-Session-Id"]
                    continue
                if err.code == 401:
                    raise TransmissionError("Transmission refused the username or password") from err
                raise TransmissionError(f"{method}: HTTP {err.code}") from err
            except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as err:
                raise TransmissionError(f"{method}: {err}") from err
            if reply.get("result") != "success":
                raise TransmissionError(f"{method}: {reply.get('result')}")
            return reply.get("arguments", {})
        raise TransmissionError(f"{method}: no session id after a 409")


# --------------------------------------------------------------------------
# Entities
# --------------------------------------------------------------------------


@dataclass
class Snapshot:
    stats: dict
    session: dict
    torrents: list[dict]
    free_space: int | None


def _ratio(up: int | None, down: int | None) -> float | None:
    if up is None or down is None or down == 0:
        return None
    return round(up / down, 6)


def _status(up: int, down: int) -> str:
    if up > 0 and down > 0:
        return "up_down"
    if up > 0:
        return "seeding"
    if down > 0:
        return "downloading"
    return "idle"


def _torrent_status(t: dict) -> str:
    return TORRENT_STATUS.get(t.get("status"), "unknown")


def _filter(torrents: list[dict], mode: str) -> list[dict]:
    wanted = FILTER_MODES[mode]
    return [t for t in torrents if wanted is None or _torrent_status(t) in wanted]


def _speed(n: int | None) -> float | None:
    return None if n is None else round(n / MB, 4)


def _size(n: int | None) -> float | None:
    return None if n is None else round(n / GIB, 6)


# key: (name, value function, discovery extras, attribute mode)
SPEED = {"device_class": "data_rate", "unit_of_measurement": "MB/s",
         "suggested_display_precision": 2}
TOTAL = {"device_class": "data_size", "unit_of_measurement": "GiB",
         "suggested_display_precision": 3, "state_class": "total_increasing"}
COUNT = {"unit_of_measurement": "torrents"}

SENSORS: list[tuple[str, str, callable, dict, str | None]] = [
    ("download_speed", "Download speed", lambda s: _speed(s.stats.get("downloadSpeed")), SPEED, None),
    ("upload_speed", "Upload speed", lambda s: _speed(s.stats.get("uploadSpeed")), SPEED, None),
    ("status", "Status",
     lambda s: _status(s.stats.get("uploadSpeed", 0), s.stats.get("downloadSpeed", 0)),
     {"device_class": "enum", "options": ["idle", "up_down", "seeding", "downloading"]}, None),
    ("download_dir_free_space", "Available disk space", lambda s: _size(s.free_space),
     {"device_class": "data_size", "unit_of_measurement": "GiB",
      "suggested_display_precision": 3, "state_class": "measurement"}, None),
    ("active_torrents", "Active torrents", lambda s: s.stats.get("activeTorrentCount"), COUNT, "active"),
    ("paused_torrents", "Paused torrents", lambda s: s.stats.get("pausedTorrentCount"), COUNT, "paused"),
    ("total_torrents", "Total torrents", lambda s: s.stats.get("torrentCount"), COUNT, "total"),
    ("completed_torrents", "Completed torrents", lambda s: len(_filter(s.torrents, "completed")), COUNT, "completed"),
    ("started_torrents", "Started torrents", lambda s: len(_filter(s.torrents, "started")), COUNT, "started"),
    ("session_download", "Session download",
     lambda s: _size(s.stats.get("current-stats", {}).get("downloadedBytes")), TOTAL, None),
    ("session_upload", "Session upload",
     lambda s: _size(s.stats.get("current-stats", {}).get("uploadedBytes")), TOTAL, None),
    ("total_download", "Total download",
     lambda s: _size(s.stats.get("cumulative-stats", {}).get("downloadedBytes")), TOTAL, None),
    ("total_upload", "Total upload",
     lambda s: _size(s.stats.get("cumulative-stats", {}).get("uploadedBytes")), TOTAL, None),
    ("session_ratio", "Session ratio",
     lambda s: _ratio(s.stats.get("current-stats", {}).get("uploadedBytes"),
                      s.stats.get("current-stats", {}).get("downloadedBytes")),
     {"state_class": "measurement", "suggested_display_precision": 3}, None),
    ("total_ratio", "Total ratio",
     lambda s: _ratio(s.stats.get("cumulative-stats", {}).get("uploadedBytes"),
                      s.stats.get("cumulative-stats", {}).get("downloadedBytes")),
     {"state_class": "measurement", "suggested_display_precision": 3}, None),
]

SWITCHES = [
    ("on_off", "Switch", lambda s: s.stats.get("activeTorrentCount", 0) > 0),
    ("turtle_mode", "Turtle mode", lambda s: bool(s.session.get("alt-speed-enabled"))),
]


def torrent_info(cfg: Config, snap: Snapshot, mode: str) -> dict:
    """The core integration's torrent_info attribute for one sensor."""
    key, reverse = ORDER_MODES.get(cfg.order, ORDER_MODES["oldest_first"])
    torrents = sorted(_filter(snap.torrents, mode), key=key, reverse=reverse)
    infos = {}
    for t in torrents[: cfg.limit]:
        info = {
            "added_date": datetime.fromtimestamp(t.get("addedDate", 0), timezone.utc).isoformat(),
            "percent_done": f"{t.get('percentDone', 0) * 100:.2f}",
            "status": _torrent_status(t),
            "id": t.get("id"),
            "ratio": t.get("uploadRatio"),
        }
        eta = t.get("eta", -1)
        if isinstance(eta, int) and eta >= 0:
            info["eta"] = str(timedelta(seconds=eta))
        infos[t.get("name", "")] = info
    return {"torrent_info": infos}


# --------------------------------------------------------------------------
# The bridge
# --------------------------------------------------------------------------


class Bridge:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.rpc = Transmission(cfg)
        self.commands: queue.Queue[tuple[str, str]] = queue.Queue()
        self.publish_discovery = True
        self.sw_version: str | None = None
        self.available: bool | None = None
        self.reachable: bool | None = None  # for logging transitions only
        # Torrent event bookkeeping, as the core coordinator keeps it.
        self.seen_first = False
        self.all_ids: dict[int, dict] = {}
        self.completed_ids: set[int] = set()
        self.started_ids: set[int] = set()
        # Events seen while MQTT is down wait here (oldest dropped past the
        # limit) and go out once it's back; states are simply republished.
        self.pending_events: deque[dict] = deque(maxlen=EVENT_BUFFER)

        base = cfg.base_topic
        self.t_bridge = f"{base}/bridge"
        self.t_available = f"{base}/availability"
        self.t_event = f"{base}/event"

        client_id = f"transmission-mqtt-{cfg.node_id}"
        # Alpine packages paho-mqtt 1.6; 2.x needs the callback API named.
        if hasattr(mqtt, "CallbackAPIVersion"):
            self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
            self.client.on_connect = self._on_connect
            self.client.on_disconnect = self._on_disconnect
        else:
            self.client = mqtt.Client(client_id=client_id)
            self.client.on_connect = lambda c, u, flags, rc: self._on_connect(c, u, flags, rc, None)
            self.client.on_disconnect = lambda c, u, rc: self._on_disconnect(c, u, None, rc, None)
        if cfg.mqtt_username:
            self.client.username_pw_set(cfg.mqtt_username, cfg.mqtt_password)
        self.client.will_set(self.t_bridge, "offline", qos=1, retain=True)
        self.client.on_message = self._on_message
        self.client.reconnect_delay_set(1, 60)

    # MQTT callbacks run on paho's thread: hand work to the main loop.
    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code != 0:  # a ReasonCode (2.x) or an int (1.x)
            log.error("MQTT connection refused: %s", reason_code)
            return
        log.info("Connected to MQTT at %s:%s", self.cfg.mqtt_host, self.cfg.mqtt_port)
        client.subscribe(f"{self.cfg.base_topic}/+/set", qos=1)
        client.subscribe(f"{self.cfg.discovery_prefix}/status", qos=1)
        client.publish(self.t_bridge, "online", qos=1, retain=True)
        self.commands.put(("discovery", ""))

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        if reason_code != 0:
            log.warning("Lost the MQTT connection (%s), reconnecting", reason_code)

    def _on_message(self, client, userdata, msg):
        payload = msg.payload.decode(errors="replace").strip()
        if msg.topic == f"{self.cfg.discovery_prefix}/status":
            if payload == "online":
                log.info("Home Assistant restarted, republishing")
                self.commands.put(("discovery", ""))
            return
        key = msg.topic[len(self.cfg.base_topic) + 1 : -len("/set")]
        self.commands.put((key, payload))

    # Discovery
    def discovery(self) -> dict:
        cfg, base = self.cfg, self.cfg.base_topic
        cmps: dict[str, dict] = {}
        for key, name, _, extra, attrs in SENSORS:
            c = {"p": "sensor", "name": name, "unique_id": f"{cfg.node_id}_{key}",
                 "state_topic": f"{base}/{key}", **extra}
            if attrs:
                c["json_attributes_topic"] = f"{base}/{key}/attributes"
            cmps[key] = c
        for key, name, _ in SWITCHES:
            cmps[key] = {"p": "switch", "name": name, "unique_id": f"{cfg.node_id}_{key}",
                         "state_topic": f"{base}/{key}", "command_topic": f"{base}/{key}/set"}
        cmps["torrent"] = {"p": "event", "name": "Torrent", "unique_id": f"{cfg.node_id}_torrent",
                           "state_topic": self.t_event,
                           "event_types": ["started", "downloaded", "removed"]}
        device = {"identifiers": [f"transmission_mqtt_{cfg.node_id}"], "name": cfg.name,
                  "manufacturer": "Transmission", "model": "Transmission"}
        if self.sw_version:
            device["sw_version"] = self.sw_version
        return {
            "device": device,
            "origin": {"name": "transmission-mqtt", "sw_version": VERSION, "support_url": SOURCE_URL},
            "availability": [{"topic": self.t_bridge}, {"topic": self.t_available}],
            "availability_mode": "all",
            "qos": 1,
            "components": cmps,
        }

    def send_discovery(self) -> None:
        topic = f"{self.cfg.discovery_prefix}/device/{self.cfg.node_id}/config"
        if not self.client.is_connected():
            return  # on_connect asks for discovery again
        self.client.publish(topic, json.dumps(self.discovery()), qos=1, retain=True)
        self.publish_discovery = False
        log.debug("Published discovery to %s", topic)

    def pub(self, topic: str, value, retain: bool = True) -> None:
        if value is None:
            payload = "None"  # MQTT sensors read this as unknown
        elif isinstance(value, (dict, list)):
            payload = json.dumps(value)
        else:
            payload = str(value)
        # Don't let paho queue states while the broker is away: on_connect
        # republishes discovery, availability and every state anyway.
        if self.client.is_connected():
            self.client.publish(topic, payload, qos=1, retain=retain)

    def set_available(self, ok: bool) -> None:
        if ok != self.available:
            self.pub(self.t_available, "online" if ok else "offline")
            self.available = ok

    # Polling
    def fetch(self) -> Snapshot:
        stats = self.rpc.call("session-stats")
        session = self.rpc.call("session-get")
        torrents = self.rpc.call("torrent-get", {"fields": TORRENT_FIELDS}).get("torrents", [])
        free = None
        try:
            free = self.rpc.call("free-space", {"path": session.get("download-dir", "")}).get("size-bytes")
        except TransmissionError as err:
            log.debug("Unable to fetch download directory free space: %s", err)
        return Snapshot(stats, session, torrents, free)

    def poll(self) -> None:
        try:
            snap = self.fetch()
        except TransmissionError as err:
            if self.reachable is not False:
                log.warning("Unable to reach Transmission: %s", err)
            self.reachable = False
            self.set_available(False)
            return
        if self.reachable is not True:
            log.info("Transmission %s reachable at %s", snap.session.get("version"), self.cfg.url)
            self.reachable = True

        version = snap.session.get("version")
        if version and version != self.sw_version:
            self.sw_version = version
            self.publish_discovery = True
        if self.publish_discovery:
            self.send_discovery()

        base = self.cfg.base_topic
        for key, _, value, _, attrs in SENSORS:
            self.pub(f"{base}/{key}", value(snap))
            if attrs:
                self.pub(f"{base}/{key}/attributes", torrent_info(self.cfg, snap, attrs))
        for key, _, is_on in SWITCHES:
            self.pub(f"{base}/{key}", "ON" if is_on(snap) else "OFF")
        self.set_available(True)
        self.torrent_events(snap.torrents)

    def torrent_events(self, torrents: list[dict]) -> None:
        def fire(kind: str, t: dict) -> None:
            log.info("Torrent %s: %s", kind, t.get("name"))
            self.pending_events.append({"event_type": kind, "name": t.get("name"), "id": t.get("id"),
                                    "download_path": t.get("downloadDir") or "",
                                    "labels": t.get("labels") or []})

        completed = {t["id"]: t for t in torrents if _torrent_status(t) == "seeding"}
        started = {t["id"]: t for t in torrents if _torrent_status(t) == "downloading"}
        current = {t["id"]: t for t in torrents}
        if self.seen_first:
            for tid, t in completed.items():
                if tid not in self.completed_ids:
                    fire("downloaded", t)
            for tid, t in started.items():
                if tid not in self.started_ids:
                    fire("started", t)
            for tid, t in self.all_ids.items():
                if tid not in current:
                    fire("removed", t)
        self.seen_first = True
        self.completed_ids, self.started_ids, self.all_ids = set(completed), set(started), current
        self.flush_events()

    def flush_events(self) -> None:
        while self.pending_events and self.client.is_connected():
            self.pub(self.t_event, self.pending_events.popleft(), retain=False)

    # Commands
    def command(self, key: str, payload: str) -> None:
        on = payload.upper() == "ON"
        try:
            if key == "on_off":
                if on:
                    self.rpc.call("torrent-start")
                else:
                    ids = [t["id"] for t in self.rpc.call("torrent-get", {"fields": ["id"]}).get("torrents", [])]
                    if ids:
                        self.rpc.call("torrent-stop", {"ids": ids})
            elif key == "turtle_mode":
                self.rpc.call("session-set", {"alt-speed-enabled": on})
            else:
                log.warning("Ignoring a command for unknown entity %r", key)
                return
        except TransmissionError as err:
            log.error("Couldn't switch %s %s: %s", key, payload, err)
            return
        log.info("Switched %s %s", key, "on" if on else "off")
        time.sleep(AFTER_WRITE_SLEEP)

    def run(self) -> None:
        self.client.connect_async(self.cfg.mqtt_host, self.cfg.mqtt_port, keepalive=60)
        self.client.loop_start()
        next_poll = time.monotonic()
        while True:
            timeout = max(0.0, next_poll - time.monotonic())
            try:
                key, payload = self.commands.get(timeout=timeout)
            except queue.Empty:
                self.poll()
                next_poll = time.monotonic() + self.cfg.scan_interval
                continue
            if key == "discovery":
                self.publish_discovery = True
                # States are retained, but republish so a broker that lost
                # them (or a Home Assistant that cleared them) gets them back.
                next_poll = time.monotonic()
                self.available = None
                continue
            self.command(key, payload)
            next_poll = time.monotonic()


def main() -> None:
    cfg = load_config()
    logging.basicConfig(level=cfg.log_level, format="%(asctime)s %(levelname)s %(message)s")
    log.info("transmission-mqtt %s: %s as device %r (topics under %s)",
             VERSION, cfg.url, cfg.name, cfg.base_topic)
    if (cfg.username or cfg.password) and cfg.url.startswith("http://"):
        log.warning("Sending the Transmission username and password unencrypted: turn on ssl if the server supports HTTPS")
    Bridge(cfg).run()


if __name__ == "__main__":
    main()
