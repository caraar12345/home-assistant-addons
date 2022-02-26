#!/bin/bash

CONFIG_PATH="/data/options.json"
echo "---"; cat tmp.json | yq e -P - > /data/config.yml


