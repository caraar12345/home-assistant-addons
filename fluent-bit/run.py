import json
import socket
import time

import docker
from docker.types import LogConfig, Mount

with open("/data/options.json", "r") as options_file:
    options = json.loads(options_file.read())
    print(f"Using options: {options}")

CONTAINER_IMAGE = "fluent/fluent-bit" # go back to latest; sid no longer exists
FORWARDER_CONTAINER_NAME = "addon_fluent_bit_forwarder"
FLUENT_BIT_COMMAND = [
    "/fluent-bit/bin/fluent-bit",
    "-i", "systemd",
    "-p", "db=/data/fluent-bit.db",
    "-p", "path=/var/log/journal",
    "-o", "http",
    "-p", f"host={options['host']}",
    "-p", f"port={options['port']}",
    "-p", "format=json_lines",
    "-p", "header=Content-Type application/x-ndjson",
]

hostname = socket.gethostname().split(".")[0]
print(f"Hostname: {hostname}")

client = docker.from_env()

containers = client.containers.list(filters={"status":"running"})
print(f"Containers: {containers}")
print(f"Hostnames: {[c.attrs['Config']['Hostname'] for c in containers]}")

this_container = next(
    c for c in containers
    if c.attrs["Config"]["Hostname"] == hostname
)

# Find the data mount from the original add-on container
data_mount = mount = next(
    m for m in this_container.attrs["Mounts"]
    if m.get("Destination", "") == "/data"
)
print(f"Container persisent data: {data_mount}")

try:
    container = client.containers.get(FORWARDER_CONTAINER_NAME)
    print(f"Found existing container {container.id}")
    container.stop()
    container.remove()
except docker.errors.NotFound:
    pass

container = client.containers.run(
    CONTAINER_IMAGE,
    FLUENT_BIT_COMMAND,
    name=FORWARDER_CONTAINER_NAME,
    detach=True,
    #remove=True,
    log_config=LogConfig(type=LogConfig.types.JOURNALD),
    mounts=[
        Mount(source="/var/log/journal", target="/var/log/journal", type="bind", read_only=True),
        Mount(source="/etc/machine-id", target="/etc/machine-id", type="bind", read_only=True),
        Mount(source=data_mount["Source"], target="/data", type="bind", read_only=False),
    ],
    restart_policy={"Name": "always"},
    #user="daemon",
)
print(f"Created new container {container.id}")

while True:
    container = client.containers.get(container.id)
    assert container.status in ["running", "creating"]
    time.sleep(10)
