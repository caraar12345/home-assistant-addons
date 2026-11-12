#!/command/with-contenv bashio
# shellcheck shell=bash
# ==============================================================================
# Prometheus add-on: prepare directories and write the initial config
# ==============================================================================
# Prometheus reads the Supervisor token from a file to scrape Home Assistant.
echo "${SUPERVISOR_TOKEN}" > /run/home-assistant.token
chown prometheus:prometheus /run/home-assistant.token
chmod 0400 /run/home-assistant.token

mkdir -p /data/prometheus /share/prometheus/rules /share/prometheus/targets
# Walking the whole TSDB on every start is slow, so only do it when needed.
if [ "$(stat -c %U:%G /data/prometheus)" != "prometheus:prometheus" ]; then
  chown -R prometheus:prometheus /data/prometheus
fi
chown prometheus:prometheus /share/prometheus/rules /share/prometheus/targets
# The config generator keeps the last valid version of each rule file here.
mkdir -p /data/prometheus-rules
chown prometheus:prometheus /data/prometheus-rules

# Build prometheus.yml before Prometheus starts, so it never runs on a config
# that's missing the user's targets. Falls back to the built-in jobs only if
# the targets don't validate.
s6-setuidgid prometheus python3 /opt/prometheus-configgen/combiner --once \
  || bashio::exit.nok "Could not generate the Prometheus configuration."
