# Cloudflare Speed CLI

Home Assistant add-on that runs [cloudflare-speed-cli](https://github.com/kavehtehrani/cloudflare-speed-cli)
against Cloudflare's `speed.cloudflare.com` service on a cron schedule and publishes the
results (plus MQTT discovery config) to your MQTT broker.

This is an alternative to the Ookla-based `speedtest2mqtt` add-on for anyone who would rather
not use the Ookla Speedtest CLI (or its EULA/GDPR requirements), and prefer Cloudflare's speed
test service instead.

## Options

    mqtt_topic (Default 'homeassistant/cloudflare-speedtest')
    mqtt_options (Default '')
    cron (Default '0 * * * *' -> run the speed test once an hour)

## Published topics

Under `<mqtt_topic>`:

    /test         JSON blob with download, upload, ping, jitter, packetloss, colo, ip, asn, as_org, timestamp
    /download     Download speed (Mbps)
    /upload       Upload speed (Mbps)
    /ping         Idle latency (ms)
    /jitter       Idle latency jitter (ms)
    /packetloss   Idle latency packet loss (%)
    /colo         Cloudflare data centre (colo) code used for the test
    /ip           Public IP address seen by Cloudflare
    /asn          ASN of the network under test
    /as_org       ASN organisation name
    /timestamp    UTC timestamp of the test

MQTT discovery configs are also published (under the standard `homeassistant/sensor/...`
discovery prefix, not `<mqtt_topic>`) for the download, upload, ping, jitter and packet loss
sensors so Home Assistant will pick them up automatically.

## Supported architectures

`cloudflare-speed-cli` only ships prebuilt static `musl` binaries for `x86_64` and `aarch64`
Linux, so this add-on only supports the `amd64` and `aarch64` architectures.

## Licensing note

This add-on downloads and bundles the upstream `cloudflare-speed-cli` binary, which is
licensed under GPLv3 by its author. This add-on's own scripts/config are MIT licensed.
