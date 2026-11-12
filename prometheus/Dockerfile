# https://github.com/hassio-addons/addon-base/releases
ARG BUILD_FROM=ghcr.io/hassio-addons/base:21.0.11
# https://github.com/prometheus/prometheus/releases
ARG PROMETHEUS_VERSION=v3.16.0

# The official image ships static prometheus and promtool binaries for every
# architecture, so there's nothing to download or unpack per arch here.
FROM quay.io/prometheus/prometheus:${PROMETHEUS_VERSION} AS prometheus

# hadolint ignore=DL3006
FROM ${BUILD_FROM}

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# python3 and PyYAML for the scrape-config generator (it needs nothing else)
# hadolint ignore=DL3018
RUN apk add --no-cache \
        python3 \
        py3-yaml \
    && addgroup -S prometheus \
    && adduser -S -D -H -h /nonexistent -s /sbin/nologin -G prometheus prometheus \
    && mkdir -p /etc/prometheus \
    && chown prometheus:prometheus /etc/prometheus

COPY --from=prometheus /bin/prometheus /bin/promtool /usr/local/bin/
RUN prometheus --version && promtool --version

COPY rootfs /
# s6 needs these executable; don't rely on git file modes
RUN chmod a+x \
        /etc/cont-init.d/*.sh \
        /etc/services.d/*/run \
        /etc/services.d/*/finish \
        /opt/prometheus-configgen/combiner

# Build arguments
ARG BUILD_ARCH
ARG BUILD_DATE
ARG BUILD_DESCRIPTION
ARG BUILD_NAME
ARG BUILD_REF
ARG BUILD_REPOSITORY
ARG BUILD_VERSION

# Labels
LABEL \
    io.hass.name="${BUILD_NAME}" \
    io.hass.description="${BUILD_DESCRIPTION}" \
    io.hass.arch="${BUILD_ARCH}" \
    io.hass.type="addon" \
    io.hass.version=${BUILD_VERSION} \
    maintainer="caraar12345" \
    org.opencontainers.image.title="${BUILD_NAME}" \
    org.opencontainers.image.description="${BUILD_DESCRIPTION}" \
    org.opencontainers.image.vendor="aaroncarson's Home Assistant Add-ons" \
    org.opencontainers.image.authors="caraar12345" \
    org.opencontainers.image.licenses="MIT" \
    org.opencontainers.image.url="https://github.com/caraar12345/home-assistant-addons/tree/main/prometheus" \
    org.opencontainers.image.source="https://github.com/${BUILD_REPOSITORY}" \
    org.opencontainers.image.documentation="https://github.com/${BUILD_REPOSITORY}/blob/main/prometheus/DOCS.md" \
    org.opencontainers.image.created=${BUILD_DATE} \
    org.opencontainers.image.revision=${BUILD_REF} \
    org.opencontainers.image.version=${BUILD_VERSION}
