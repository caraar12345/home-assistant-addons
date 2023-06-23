FROM sbs20/scanservjs

RUN export DEBIAN_FRONTEND=noninteractive \
    && apt update \
    && apt install -y jq curl \
    && curl -J -L -o /tmp/bashio.tar.gz \
        "https://github.com/hassio-addons/bashio/archive/v0.14.3.tar.gz" \
    && mkdir /tmp/bashio \
    && tar zxvf \
        /tmp/bashio.tar.gz \
        --strip 1 -C /tmp/bashio \
    && apt remove -y curl \
    && mv /tmp/bashio/lib /usr/lib/bashio \
    && ln -s /usr/lib/bashio/bashio /usr/bin/bashio \
    && apt install hplip hpijs-ppds hp-ppd -y \
	&& apt-get clean -y \
    && rm -rf /var/lib/apt/lists/*

ADD --chmod=777 run.sh /run.sh
ENTRYPOINT [ "/run.sh" ]
