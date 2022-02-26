#!/bin/bash

CONFIG_PATH="/data/options.json"
echo "---"; cat $CONFIG_PATH | yq e -P - > /data/config.yml

/usr/local/bin/smokeping_prober --config.file /data/config.yml 
