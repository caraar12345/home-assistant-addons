#!/usr/bin/env bashio

CUSTOM_ARGS=$(bashio::config 'custom_args')

/usr/bin/cadvisor --docker="unix:///run/docker.sock" --docker_only=true $CUSTOM_ARGS