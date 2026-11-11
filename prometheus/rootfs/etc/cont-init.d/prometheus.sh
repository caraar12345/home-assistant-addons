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
chown -R prometheus:prometheus /data/prometheus
chown prometheus:prometheus /share/prometheus/rules /share/prometheus/targets

# Build prometheus.yml before Prometheus starts, so it never runs on a config
# that's missing the user's targets. Falls back to the built-in jobs only if
# the targets don't validate.
s6-setuidgid prometheus python3 /opt/prometheus-configgen/combiner --once \
  || bashio::exit.nok "Could not generate the Prometheus configuration."
