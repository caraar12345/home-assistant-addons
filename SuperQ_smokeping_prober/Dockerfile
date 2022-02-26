ARG BUILD_ARCH
FROM ghcr.io/home-assistant/$BUILD_ARCH-base

COPY entrypoint.sh /opt/
COPY repositories /etc/apk/
RUN apk update
RUN chmod +x /opt/entrypoint.sh && \
    apk --no-cache add yq zsh && \
    mkdir -pv /var/tmp/

SHELL ["/bin/zsh", "-c"]

RUN apk --no-cache add wget --virtual .build-deps

ARG BUILD_ARCH
ARG SP_VERSION
RUN echo "Target Arch $BUILD_ARCH" && \
    if [[ $BUILD_ARCH = 'aarch64' ]]; then wget https://github.com/SuperQ/smokeping_prober/releases/download/v$SP_VERSION/smokeping_prober-$SP_VERSION.linux-arm64.tar.gz -O /var/tmp/smokeping_prober-$SP_VERSION.linux-arm64.tar.gz; fi && \
    if [[ $BUILD_ARCH = 'armhf' ]]; then wget https://github.com/SuperQ/smokeping_prober/releases/download/v$SP_VERSION/smokeping_prober-$SP_VERSION.linux-armv6.tar.gz -O /var/tmp/smokeping_prober-$SP_VERSION.linux-armv6.tar.gz; fi && \
    if [[ $BUILD_ARCH = 'armv7' ]]; then wget https://github.com/SuperQ/smokeping_prober/releases/download/v$SP_VERSION/smokeping_prober-$SP_VERSION.linux-armv7.tar.gz -O /var/tmp/smokeping_prober-$SP_VERSION.linux-armv7.tar.gz; fi && \
    if [[ $BUILD_ARCH = 'amd64' ]]; then wget https://github.com/SuperQ/smokeping_prober/releases/download/v$SP_VERSION/smokeping_prober-$SP_VERSION.linux-amd64.tar.gz -O /var/tmp/smokeping_prober-$SP_VERSION.linux-amd64.tar.gz; fi && \
    if [[ $BUILD_ARCH = 'i386' ]]; then wget https://github.com/SuperQ/smokeping_prober/releases/download/v$SP_VERSION/smokeping_prober-$SP_VERSION.linux-386.tar.gz -O /var/tmp/smokeping_prober-$SP_VERSION.linux-386.tar.gz; fi

RUN tar xf /var/tmp/smokeping_prober*.tar.gz -C /var/tmp && \
    ls /var/tmp && \
    rm /var/tmp/smokeping_prober*.tar.gz && \
    mv /var/tmp/smokeping_prober*/smokeping_prober /usr/local/bin/smokeping_prober && \
    apk del --no-cache .build-deps

RUN apk --no-cache add gcc musl-dev python3-dev --virtual .build-deps

ENTRYPOINT /opt/entrypoint.sh
