# transmission-mqtt

Home Assistant's Transmission integration needs Transmission 4.0 or later,
and refuses older servers with "Minimum required version is 4.0.0". This app
polls any Transmission server from 2.80 on (it was written for a 3.00
seedbox) and publishes the integration's entities through MQTT discovery, so
they show up as one device, with the same names, under the MQTT integration.

It needs an MQTT broker that Home Assistant's MQTT integration is connected
to. With the Mosquitto broker app installed, it finds it on its own.

## Entities

For a device named `Transmission`:

| Entity | Notes |
| --- | --- |
| `sensor.transmission_download_speed`, `sensor.transmission_upload_speed` | MB/s |
| `sensor.transmission_status` | `idle`, `up_down`, `seeding` or `downloading` |
| `sensor.transmission_available_disk_space` | Free space in the download directory, GiB |
| `sensor.transmission_active_torrents`, `_paused_torrents`, `_total_torrents`, `_completed_torrents`, `_started_torrents` | Counts, each with a `torrent_info` attribute listing up to `limit` torrents (added date, percent done, status, id, ratio, ETA) |
| `sensor.transmission_session_download`, `_session_upload`, `_total_download`, `_total_upload` | GiB |
| `sensor.transmission_session_ratio`, `sensor.transmission_total_ratio` | Unknown until something has been downloaded |
| `switch.transmission_switch` | On while any torrent is active. Off stops every torrent, on starts them all |
| `switch.transmission_turtle_mode` | Transmission's alternative speed limits |
| `event.transmission_torrent` | `started`, `downloaded` or `removed`, with `name`, `id`, `download_path` and `labels` |

These match the integration's entities, including their units (the ones it
shows by default) and the order and limit options for `torrent_info`. The
integration's `transmission_*_torrent` bus events are replaced by the event
entity, which is what the integration is moving to. Its services (add,
remove, start and stop a torrent) aren't included.

Every entity goes unavailable when Transmission can't be reached or the app
stops.

### Taking over the old entity IDs

If the Transmission integration is still set up for this server, its
entities hold the IDs these would get, so Home Assistant adds `_2` to the
new ones. To keep the old IDs (and their history, since the units match):

1. Delete the Transmission integration's entry for this server
   (**Settings > Devices & services > Transmission**).
2. Set this app's `name` to the integration entry's name (e.g.
   `RapidSeedbox Transmission`) and start it.

If the app was already running, delete its device under **MQTT** and restart
the app to have it rediscovered.

## Options

| Option | Default | Description |
| --- | --- | --- |
| `name` | `Transmission` | Device name. Entity IDs and MQTT topics come from it, so renaming creates a new device. |
| `host` | | Transmission server's hostname or IP. Required. |
| `port` | `9091` | RPC port. |
| `path` | `/transmission/rpc` | RPC path. |
| `ssl` | `false` | Use HTTPS. |
| `verify_ssl` | `true` | Check the server's certificate over HTTPS. |
| `username` / `password` | | RPC credentials, if authentication is on. |
| `scan_interval` | `120` | Seconds between polls (the integration's default). A switch change polls straight away. |
| `request_timeout` | `10` | Seconds to wait for each request. |
| `limit` | `10` | Torrents listed in each `torrent_info` attribute. |
| `order` | `oldest_first` | `oldest_first`, `newest_first`, `best_ratio_first` or `worst_ratio_first`. |
| `mqtt_host`, `mqtt_port`, `mqtt_username`, `mqtt_password` | | A broker to use instead of the one Home Assistant provides. |
| `discovery_prefix` | `homeassistant` | MQTT discovery prefix. |
| `log_level` | `info` | `debug`, `info`, `warning` or `error`. |

## MQTT topics

Discovery is one device message at
`<discovery_prefix>/device/<name as a slug>/config`. States are retained under
`transmission_mqtt/<name as a slug>/`: one topic per entity (`.../<key>`,
attributes at `.../<key>/attributes`), events at `.../event`, switch commands
at `.../<key>/set` (`ON`/`OFF`), and two availability topics: `.../bridge`
(the app, with a last will) and `.../availability` (Transmission).
