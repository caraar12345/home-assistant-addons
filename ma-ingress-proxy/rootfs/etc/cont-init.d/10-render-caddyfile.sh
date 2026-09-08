#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
# ==============================================================================
# Home Assistant Add-on: Music Assistant Ingress Proxy
# Copies the Caddyfile template into place and, when verify_ssl is disabled,
# inserts a transport block that skips upstream TLS certificate verification.
# ==============================================================================
set -e

readonly CADDY_DIR=/etc/caddy
readonly CADDYFILE="${CADDY_DIR}/Caddyfile"

if ! bashio::config.true 'verify_ssl'; then
    bashio::log.warning "verify_ssl is disabled - the upstream Music Assistant TLS certificate will not be checked"
    sed -i \
        's|# MA_INSECURE_TRANSPORT_PLACEHOLDER|transport http {\n\t\t\t\ttls\n\t\t\t\ttls_insecure_skip_verify\n\t\t\t}|' \
        "${CADDYFILE}"
else
    sed -i '/# MA_INSECURE_TRANSPORT_PLACEHOLDER/d' "${CADDYFILE}"
fi

bashio::log.info "Caddyfile ready"
