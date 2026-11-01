ARG BUILD_FROM
# https://github.com/grafana/alloy/releases
ARG ALLOY_VERSION=v1.21.3

FROM grafana/alloy:${ALLOY_VERSION} AS alloy

# hadolint ignore=DL3006
FROM ${BUILD_FROM}

RUN set -eux; \
    apt-get update \
    && apt-get upgrade -y \
    && apt-get install -qy --no-install-recommends \
        ca-certificates \
        libsystemd0 \
        tzdata \
        tar; \
    update-ca-certificates; \
    \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*;

# Add Alloy (pinned via ALLOY_VERSION above)
COPY --from=alloy /usr/bin/alloy /usr/bin/alloy
RUN alloy --version

COPY rootfs /
# The base's timezone init runs dpkg-reconfigure (/usr/sbin), which AppArmor
# blocks (exit 126). Replace it via the unversioned s6-overlay symlink, and
# fail the build if the base renames it so the override can't go stale again.
ARG TZ_SCRIPT=/package/admin/s6-overlay/etc/s6-rc/scripts/base-app-timezone
RUN test -f "${TZ_SCRIPT}"
COPY base-app-timezone ${TZ_SCRIPT}
# s6 needs these executable; don't rely on git file modes
RUN chmod a+x /etc/cont-init.d/*.sh /etc/services.d/alloy/run "${TZ_SCRIPT}"
WORKDIR /data/promtail

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
    org.opencontainers.image.url="https://github.com/caraar12345/home-assistant-addons/blob/main/promtail" \
    org.opencontainers.image.source="https://github.com/${BUILD_REPOSITORY}" \
    org.opencontainers.image.documentation="https://github.com/${BUILD_REPOSITORY}/blob/main/promtail/README.md" \
    org.opencontainers.image.created=${BUILD_DATE} \
    org.opencontainers.image.revision=${BUILD_REF} \
    org.opencontainers.image.version=${BUILD_VERSION}
