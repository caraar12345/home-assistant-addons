ARG BUILD_ARCH
ARG SP_VERSION

FROM $BUILD_ARCH-base

COPY entrypoint.sh /opt/

RUN cat > /etc/apk/repositories << EOF; $(echo) \
https://dl-cdn.alpinelinux.org/alpine/v$(cat /etc/alpine-release | cut -d'.' -f1,2)/main/ \
https://dl-cdn.alpinelinux.org/alpine/v$(cat /etc/alpine-release | cut -d'.' -f1,2)/community/ \
https://dl-cdn.alpinelinux.org/alpine/edge/testing/ \
EOF
RUN apk update
RUN chmod +x /opt/entrypoint.sh && \
    apk --no-cache add yq zsh

SHELL ["/bin/zsh", "-c"]

RUN apk --no-cache add wget --virtual .build-deps

RUN echo "Target Arch $BUILD_ARCH" && \
    if [[ $BUILD_ARCH = 'aarch64' ]]; then wget https://github.com/SuperQ/smokeping_prober/releases/download/v{$SP_VERSION}/smokeping_prober-{$SP_VERSION}.linux-arm64.tar.gz -O /var/tmp/smokeping_prober.tar.gz; fi && \
    if [[ $BUILD_ARCH = 'armv6' ]]; then wget https://github.com/SuperQ/smokeping_prober/releases/download/v{$SP_VERSION}/smokeping_prober-{$SP_VERSION}.linux-armv6.tar.gz -O /var/tmp/smokeping_prober.tar.gz; fi && \
    if [[ $BUILD_ARCH = 'armv7' ]]; then wget https://github.com/SuperQ/smokeping_prober/releases/download/v{$SP_VERSION}/smokeping_prober-{$SP_VERSION}.linux-armv7.tar.gz -O /var/tmp/smokeping_prober.tar.gz; fi && \
    if [[ $BUILD_ARCH = 'amd64' ]]; then wget https://github.com/SuperQ/smokeping_prober/releases/download/v{$SP_VERSION}/smokeping_prober-{$SP_VERSION}.linux-amd64.tar.gz -O /var/tmp/smokeping_prober.tar.gz; fi && \
    if [[ $BUILD_ARCH = 'i386' ]]; then wget https://github.com/SuperQ/smokeping_prober/releases/download/v{$SP_VERSION}/smokeping_prober-{$SP_VERSION}.linux-386.tar.gz -O /var/tmp/smokeping_prober.tar.gz; fi && \
    tar xf /var/tmp/smokeping_prober.tar.gz -C /var/tmp && \
    mv /var/tmp/smokeping_prober /usr/local/bin && \
    rm /var/tmp/smokeping_prober.tar.gz && \
    apk del --no-cache .build-deps

RUN apk --no-cache add gcc musl-dev python3-dev --virtual .build-deps

ENTRYPOINT /opt/entrypoint.sh
