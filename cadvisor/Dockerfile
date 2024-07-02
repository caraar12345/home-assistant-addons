ARG BUILD_FROM=ghcr.io/hassio-addons/base:stable
FROM ${BUILD_FROM}

COPY --from=gcr.io/cadvisor/cadvisor:v0.49.1 /usr/bin/cadvisor /usr/bin/cadvisor

# Copy root filesystem
COPY rootfs /
RUN chmod +x /usr/bin/run_cadvisor.sh && \
    chmod +x /usr/bin/cadvisor

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
    io.hass.name="cAdvisor" \
    io.hass.description="Add-on for cAdvisor" \
    io.hass.arch="${BUILD_ARCH}" \
    io.hass.type="addon" \
    io.hass.version=${BUILD_VERSION} \
    maintainer="https://github.com/caraar12345" \
    org.label-schema.description="Add-on for cAdvisor" \
    org.label-schema.build-date=${BUILD_DATE} \
    org.label-schema.name="TVHeadend" \
    org.label-schema.schema-version="v0.49.1" \
    org.label-schema.url="https://github.com/caraar12345/home-assistant-addons" \
    org.label-schema.usage="https://github.com/caraar12345/home-assistant-addons" \
    org.label-schema.vcs-ref=${BUILD_REF} \
    org.label-schema.vcs-url="https://github.com/caraar12345/home-assistant-addons" \
    org.label-schema.vendor="https://github.com/caraar12345"
