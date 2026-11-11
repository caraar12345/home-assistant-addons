# https://github.com/hassio-addons/addon-base/releases
ARG BUILD_FROM=ghcr.io/hassio-addons/base:21.0.11
# hadolint ignore=DL3006
FROM ${BUILD_FROM}

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# The exporter only uses the Python standard library.
# hadolint ignore=DL3018
RUN apk add --no-cache python3

COPY rootfs /
# s6 needs these executable; don't rely on git file modes
RUN chmod a+x /etc/services.d/modem-exporter/run /etc/services.d/modem-exporter/finish \
    && python3 -m py_compile /opt/modem-exporter/modem_exporter.py

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
    org.opencontainers.image.url="https://github.com/caraar12345/home-assistant-addons/tree/main/modem-exporter" \
    org.opencontainers.image.source="https://github.com/${BUILD_REPOSITORY}" \
    org.opencontainers.image.documentation="https://github.com/${BUILD_REPOSITORY}/blob/main/modem-exporter/DOCS.md" \
    org.opencontainers.image.created=${BUILD_DATE} \
    org.opencontainers.image.revision=${BUILD_REF} \
    org.opencontainers.image.version=${BUILD_VERSION}
