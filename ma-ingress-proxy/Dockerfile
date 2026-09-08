ARG BUILD_FROM
# hadolint ignore=DL3006
FROM ${BUILD_FROM}

RUN set -eux; \
    apt-get update; \
    apt-get upgrade -y; \
    apt-get install -qy --no-install-recommends \
        age \
        ca-certificates \
        python3 \
        python3-venv \
        tzdata; \
    update-ca-certificates; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*;

# Add Caddy
COPY --from=caddy:2.9-alpine /usr/bin/caddy /usr/bin/caddy
RUN caddy version

COPY rootfs/opt/sidecar/requirements.txt /opt/sidecar/requirements.txt
RUN set -eux; \
    python3 -m venv /opt/venv; \
    /opt/venv/bin/pip install --no-cache-dir --upgrade pip; \
    /opt/venv/bin/pip install --no-cache-dir -r /opt/sidecar/requirements.txt

ENV PATH="/opt/venv/bin:${PATH}"

COPY rootfs /
RUN chmod -R +x /etc/cont-init.d /etc/services.d

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
    org.opencontainers.image.url="https://github.com/caraar12345/home-assistant-addons/blob/main/ma-ingress-proxy" \
    org.opencontainers.image.source="${BUILD_REPOSITORY}" \
    org.opencontainers.image.documentation="https://github.com/${BUILD_REPOSITORY}/blob/main/ma-ingress-proxy/README.md" \
    org.opencontainers.image.created=${BUILD_DATE} \
    org.opencontainers.image.revision=${BUILD_REF} \
    org.opencontainers.image.version=${BUILD_VERSION}
