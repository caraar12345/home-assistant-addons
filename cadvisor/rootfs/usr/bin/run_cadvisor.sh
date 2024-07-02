#!/usr/bin/env bashio

CUSTOM_ARGS=$(bashio::config 'custom_args')

/usr/bin/cadvisor $CUSTOM_ARGS