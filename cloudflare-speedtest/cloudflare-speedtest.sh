#!/usr/bin/env bashio
set -uo pipefail

MQTT_HOST=$(bashio::services mqtt "host")
MQTT_PORT=$(bashio::services mqtt "port")
MQTT_USER=$(bashio::services mqtt "username")
MQTT_PASS=$(bashio::services mqtt "password")

MQTT_ID="cloudflare-speedtest-hass"
MQTT_TOPIC=$(bashio::config 'mqtt_topic')
DISCOVERY_PREFIX="homeassistant"
# MQTT_OPTIONS is user-supplied, space-separated extra mosquitto_pub flags
# (e.g. "-r"), so it is intentionally split into an array rather than quoted.
read -ra MQTT_OPTIONS <<< "$(bashio::config 'mqtt_options')"

file=~/cloudflare-speedtest.json

echo "$(date -Iseconds) starting speedtest"

if ! cloudflare-speed-cli --json --silent > "${file}"; then
  echo "$(date -Iseconds) cloudflare-speed-cli failed, skipping this run"
  exit 1
fi

if ! jq -e '[.download.mbps, .upload.mbps, .idle_latency.mean_ms, .idle_latency.jitter_ms, .idle_latency.loss] | all(type == "number")' "${file}" > /dev/null 2>&1; then
  echo "$(date -Iseconds) speedtest output missing required fields, skipping this run"
  exit 1
fi

download=$(jq -r '.download.mbps' "${file}")
upload=$(jq -r '.upload.mbps' "${file}")
ping=$(jq -r '.idle_latency.mean_ms' "${file}")
jitter=$(jq -r '.idle_latency.jitter_ms' "${file}")
packetloss=$(jq -r '.idle_latency.loss' "${file}")
colo=$(jq -r '.colo // ""' "${file}")
ip=$(jq -r '.ip // ""' "${file}")
asn=$(jq -r '.asn // ""' "${file}")
asorg=$(jq -r '.as_org // ""' "${file}")
timestamp=$(jq -r '.timestamp_utc // ""' "${file}")

echo "$(date -Iseconds) speedtest results"

echo "$(date -Iseconds) download = ${download} Mbps"
echo "$(date -Iseconds) upload =  ${upload} Mbps"
echo "$(date -Iseconds) ping =  ${ping} ms"
echo "$(date -Iseconds) jitter = ${jitter} ms"

echo "$(date -Iseconds) sending results to ${MQTT_HOST} as clientID ${MQTT_ID} using user ${MQTT_USER}"

mqtt_pub() {
  /usr/bin/mosquitto_pub -h "${MQTT_HOST}" -p "${MQTT_PORT}" -r -i "${MQTT_ID}" \
    "${MQTT_OPTIONS[@]}" -u "${MQTT_USER}" -P "${MQTT_PASS}" "$@"
}

mqtt_pub -t "${DISCOVERY_PREFIX}/sensor/cloudflare-speedtest-download/config" -m "{\"name\":\"Cloudflare Speedtest - Download\", \"state_topic\":\"${MQTT_TOPIC}/test\", \"value_template\":\"{{ value_json.download }}\", \"json_attributes_topic\": \"${MQTT_TOPIC}/test\", \"unit_of_measurement\":\"Mbps\", \"icon\":\"mdi:speedometer\", \"unique_id\":\"cloudflare_speedtest_download\"}"
mqtt_pub -t "${DISCOVERY_PREFIX}/sensor/cloudflare-speedtest-upload/config" -m "{\"name\":\"Cloudflare Speedtest - Upload\", \"state_topic\":\"${MQTT_TOPIC}/test\", \"value_template\":\"{{ value_json.upload }}\", \"json_attributes_topic\": \"${MQTT_TOPIC}/test\", \"unit_of_measurement\":\"Mbps\", \"icon\":\"mdi:speedometer\", \"unique_id\":\"cloudflare_speedtest_upload\"}"
mqtt_pub -t "${DISCOVERY_PREFIX}/sensor/cloudflare-speedtest-ping/config" -m "{\"name\":\"Cloudflare Speedtest - Ping\", \"state_topic\":\"${MQTT_TOPIC}/test\", \"value_template\":\"{{ value_json.ping }}\", \"json_attributes_topic\": \"${MQTT_TOPIC}/test\", \"unit_of_measurement\":\"ms\", \"icon\":\"mdi:access-point\", \"unique_id\":\"cloudflare_speedtest_ping\"}"
mqtt_pub -t "${DISCOVERY_PREFIX}/sensor/cloudflare-speedtest-jitter/config" -m "{\"name\":\"Cloudflare Speedtest - Jitter\", \"state_topic\":\"${MQTT_TOPIC}/test\", \"value_template\":\"{{ value_json.jitter }}\", \"json_attributes_topic\": \"${MQTT_TOPIC}/test\", \"unit_of_measurement\":\"ms\", \"icon\":\"mdi:access-point-remove\", \"unique_id\":\"cloudflare_speedtest_jitter\"}"
mqtt_pub -t "${DISCOVERY_PREFIX}/sensor/cloudflare-speedtest-packet-loss/config" -m "{\"name\":\"Cloudflare Speedtest - Packet loss\", \"state_topic\":\"${MQTT_TOPIC}/test\", \"value_template\":\"{{ value_json.packetloss }}\", \"json_attributes_topic\": \"${MQTT_TOPIC}/test\", \"unit_of_measurement\":\"%\", \"icon\":\"mdi:lan-disconnect\", \"unique_id\":\"cloudflare_speedtest_packet_loss\"}"

mqtt_pub -t "${MQTT_TOPIC}/test" -m "{\"download\":${download}, \"upload\":${upload}, \"ping\":${ping}, \"jitter\":${jitter}, \"packetloss\":${packetloss}, \"colo\":\"${colo}\", \"ip\":\"${ip}\", \"asn\":\"${asn}\", \"as_org\":\"${asorg}\", \"timestamp\":\"${timestamp}\"}"

mqtt_pub -t "${MQTT_TOPIC}/download" -m "${download}"
mqtt_pub -t "${MQTT_TOPIC}/upload" -m "${upload}"
mqtt_pub -t "${MQTT_TOPIC}/ping" -m "${ping}"
mqtt_pub -t "${MQTT_TOPIC}/jitter" -m "${jitter}"
mqtt_pub -t "${MQTT_TOPIC}/packetloss" -m "${packetloss}"

mqtt_pub -t "${MQTT_TOPIC}/colo" -m "${colo}"
mqtt_pub -t "${MQTT_TOPIC}/ip" -m "${ip}"
mqtt_pub -t "${MQTT_TOPIC}/asn" -m "${asn}"
mqtt_pub -t "${MQTT_TOPIC}/as_org" -m "${asorg}"

mqtt_pub -t "${MQTT_TOPIC}/timestamp" -m "${timestamp}"
