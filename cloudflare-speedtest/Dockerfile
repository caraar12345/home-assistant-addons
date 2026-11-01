ARG BUILD_FROM=ghcr.io/hassio-addons/base:stable
FROM $BUILD_FROM

ARG TARGETARCH
ARG BUILD_ARCH=$TARGETARCH

ARG CLOUDFLARE_SPEED_CLI_VERSION=1.0.8

COPY entrypoint.sh cloudflare-speedtest.sh /opt/

RUN chmod +x /opt/cloudflare-speedtest.sh /opt/entrypoint.sh && \
    apk --no-cache add mosquitto-clients jq xz && \
    echo "Target Arch: $BUILD_ARCH" && \
    if [ "$BUILD_ARCH" = 'amd64' ]; then CF_TARGET='x86_64-unknown-linux-musl'; fi && \
    if [ "$BUILD_ARCH" = 'arm64' ]; then CF_TARGET='aarch64-unknown-linux-musl'; fi && \
    if [ -z "$CF_TARGET" ]; then echo >&2 "error: unsupported architecture ($BUILD_ARCH)"; exit 1; fi && \
    CF_FILE="cloudflare-speed-cli-${CF_TARGET}.tar.xz" && \
    CF_BASE_URL="https://github.com/kavehtehrani/cloudflare-speed-cli/releases/download/v${CLOUDFLARE_SPEED_CLI_VERSION}" && \
    wget "${CF_BASE_URL}/${CF_FILE}" -O /var/tmp/${CF_FILE} && \
    wget "${CF_BASE_URL}/${CF_FILE}.sha256" -O /var/tmp/${CF_FILE}.sha256 && \
    cd /var/tmp && \
    sed 's/\r$//' ${CF_FILE}.sha256 | grep -E -v '^[[:space:]]*$' | sha256sum -c - && \
    mkdir -p /var/tmp/cloudflare-speed-cli && \
    tar xf ${CF_FILE} -C /var/tmp/cloudflare-speed-cli && \
    find /var/tmp/cloudflare-speed-cli -type f -name cloudflare-speed-cli -exec mv {} /usr/local/bin/cloudflare-speed-cli \; && \
    chmod +x /usr/local/bin/cloudflare-speed-cli && \
    cd / && \
    rm -rf /var/tmp/cloudflare-speed-cli*

ENTRYPOINT /opt/entrypoint.sh

# Label-only build arguments go last: every RUN after an ARG sees it, so a
# per-commit value declared earlier would invalidate the layer cache.
ARG BUILD_DATE
ARG BUILD_REF
ARG BUILD_VERSION

LABEL \
    org.opencontainers.image.created="${BUILD_DATE}" \
    org.opencontainers.image.source="https://github.com/caraar12345/home-assistant-addons" \
    org.opencontainers.image.authors="Aaron Carson <aaron@aaroncarson.co.uk>" \
    org.opencontainers.image.version="${BUILD_VERSION}" \
    org.opencontainers.image.licenses="MIT" \
    org.opencontainers.image.revision="${BUILD_REF}" \
    org.opencontainers.image.description="Cloudflare Speed CLI as a Home Assistant Add-on with the output being published to MQTT."
