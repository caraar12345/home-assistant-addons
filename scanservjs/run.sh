#!/usr/bin/env bashio
set -xve

# Test using the following form:
# export SANED_NET_HOSTS="a|b" AIRSCAN_DEVICES="c|d" DELIMITER="|"; ./run.sh

# turn off globbing
set -f

# split at newlines only (airscan devices can have spaces in)
IFS='
'

# Get a custom delimiter but default to ;
DELIMITER=${DELIMITER:-;}

export SANED_NET_HOSTS=$(bashio::config 'saned_net_hosts')

# Insert a list of net hosts
if [ ! -z "$SANED_NET_HOSTS" ]; then
  hosts=$(echo $SANED_NET_HOSTS | sed "s/$DELIMITER/\n/")
  for host in $hosts; do
    echo $host >> /etc/sane.d/net.conf
  done
fi

export AIRSCAN_DEVICES=$(bashio::config 'airscan_devices')

# Insert airscan devices
if [ ! -z "$AIRSCAN_DEVICES" ]; then
  devices=$(echo $AIRSCAN_DEVICES | sed "s/$DELIMITER/\n/")
  for device in $devices; do
    sed -i "/^\[devices\]/a $device" /etc/sane.d/airscan.conf
  done
fi

export SCANIMAGE_LIST_IGNORE=$(bashio::config 'scanimage_list_ignore')
export DEVICES=$(bashio::config 'devices')
export OCR_LANG=$(bashio::config 'ocr_lang')

unset IFS
set +f

node ./server/server.js
