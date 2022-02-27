#!/bin/bash

CONFIG_PATH="/data/options.json"
echo "---" | cat - $CONFIG_PATH | yq e -P - > /data/config.yml

echo ----------
echo options.json
cat $CONFIG_PATH

echo
echo ----------
echo config.yml
cat /data/config.yml 

echo ----------
/usr/local/bin/smokeping_prober --config.file /data/config.json
