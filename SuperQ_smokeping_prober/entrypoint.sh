#!/bin/bash

CONFIG_PATH="/data/options.json"
cat $CONFIG_PATH | yq e -P - > /data/config.yml

echo '---' | cat - /data/config.yml > temp && mv temp config.yml

echo ----------
echo options.json
cat $CONFIG_PATH

echo
echo ----------
echo config.yml
cat /data/config.yml 

echo ----------
/usr/local/bin/smokeping_prober --config.file /data/config.yml
