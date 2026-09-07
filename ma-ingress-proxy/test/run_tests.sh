#!/usr/bin/env bash
# ==============================================================================
# Smoke tests for the Music Assistant ingress proxy add-on, run against a real
# Music Assistant server. Stands in for Supervisor's ingress network: two fixed
# addresses on the compose network play the parts of Supervisor (172.30.32.2,
# the only address the shipped Caddyfile trusts) and an outside attacker.
#
# Usage:
#   docker build --build-arg BUILD_FROM=ghcr.io/hassio-addons/debian-base:8.1.1 \
#       -t ma-ingress-proxy:test ..
#   ./run_tests.sh
#
# Requires: docker, docker compose, curl, python3.
# ==============================================================================
set -euo pipefail
cd "$(dirname "$0")"

PASS=0
FAIL=0

check() {
	local desc="$1" expected="$2" actual="$3"
	if [[ "$actual" == "$expected" ]]; then
		echo "PASS: ${desc}"
		PASS=$((PASS + 1))
	else
		echo "FAIL: ${desc} (expected ${expected}, got ${actual})"
		FAIL=$((FAIL + 1))
	fi
}

cleanup() {
	docker compose down -v >/dev/null 2>&1 || true
}
trap cleanup EXIT

cleanup
docker compose up -d
echo "Waiting for Music Assistant to accept setup..."
for _ in $(seq 1 30); do
	curl -sf -o /dev/null http://127.0.0.1:18095/info && break
	sleep 1
done

curl -s -X POST http://127.0.0.1:18095/setup -H 'Content-Type: application/json' \
	-d '{"username":"sidecar-admin","password":"SidecarAdminPassw0rd!"}' >/dev/null

SUPERVISOR="docker exec test-supervisor-stand-in-1 curl -s"
ATTACKER="docker exec test-attacker-stand-in-1 curl -s"
BASE="http://172.30.33.12:8099"

# Case 4: no X-Remote-User-Id -> 403, nothing forwarded upstream
# `depends_on` only orders container start, it does not wait for Caddy or the sidecar to
# actually accept requests, so poll /api (never /, which redirects via the bootstrap
# route) until the stack is up rather than relying on a fixed sleep.
echo "Waiting for the Caddy + sidecar proxy stack to accept requests..."
status="000"
for _ in $(seq 1 30); do
	status=$($SUPERVISOR -o /dev/null -w '%{http_code}' "${BASE}/api" 2>/dev/null || echo 000)
	[[ "$status" == "403" ]] && break
	sleep 1
done
check "case 4: missing identity header -> 403" "403" "$status"

# Case 6: non-Supervisor source address -> 403 regardless of headers
status=$($ATTACKER -o /dev/null -w '%{http_code}' "${BASE}/" \
	-H 'X-Remote-User-Id: ha-user-evil' -H 'X-Remote-User-Name: evil')
check "case 6: non-Supervisor address -> 403" "403" "$status"

# Case 1: first request for an unknown user -> 200, MA reachable, provisioned
status=$($SUPERVISOR -o /dev/null -w '%{http_code}' "${BASE}/auth/me" \
	-H 'X-Remote-User-Id: ha-user-carol' -H 'X-Remote-User-Name: carol')
check "case 1: first request for unknown user -> 200" "200" "$status"

# Case 5: a client-supplied Authorization header is replaced, not honoured
username=$($SUPERVISOR "${BASE}/auth/me" \
	-H 'X-Remote-User-Id: ha-user-carol' -H 'X-Remote-User-Name: carol' \
	-H 'Authorization: Bearer garbage-should-be-ignored' | python3 -c 'import json,sys;print(json.load(sys.stdin)["username"])')
check "case 5: client Authorization header overridden" "carol" "$username"

# Case 10: a second, distinct HA user gets a distinct MA identity
username2=$($SUPERVISOR "${BASE}/auth/me" \
	-H 'X-Remote-User-Id: ha-user-dave' -H 'X-Remote-User-Name: dave' | python3 -c 'import json,sys;print(json.load(sys.stdin)["username"])')
check "case 10: second HA user provisioned distinctly" "dave" "$username2"
mapping=$(docker exec test-sidecar-1 cat /data/mapping.json)
carol_id=$(echo "$mapping" | python3 -c 'import json,sys;print(json.load(sys.stdin)["ha-user-carol"])')
dave_id=$(echo "$mapping" | python3 -c 'import json,sys;print(json.load(sys.stdin)["ha-user-dave"])')
if [[ "$carol_id" != "$dave_id" ]]; then
	echo "PASS: case 10: distinct MA user ids ($carol_id != $dave_id)"
	PASS=$((PASS + 1))
else
	echo "FAIL: case 10: carol and dave share an MA user id"
	FAIL=$((FAIL + 1))
fi

# Case 8: Music Assistant down -> 502, sidecar survives, recovers without restart
docker compose stop music-assistant >/dev/null
status=$($SUPERVISOR -o /dev/null -w '%{http_code}' "${BASE}/auth/me" \
	-H 'X-Remote-User-Id: ha-user-eve' -H 'X-Remote-User-Name: eve')
check "case 8: Music Assistant down -> 502" "502" "$status"
docker compose start music-assistant >/dev/null
for _ in $(seq 1 30); do
	curl -sf -o /dev/null http://127.0.0.1:18095/info && break
	sleep 1
done
status=$($SUPERVISOR -o /dev/null -w '%{http_code}' "${BASE}/auth/me" \
	-H 'X-Remote-User-Id: ha-user-eve' -H 'X-Remote-User-Name: eve')
check "case 8: recovers without sidecar restart" "200" "$status"

# Case 9 (mechanism check): the bootstrap redirect's token authenticates a real
# WebSocket session as the right user, the same mechanism the Music Assistant
# frontend uses after reading its ?code= query parameter. Must run from Supervisor's
# trusted address, which the long-lived stand-in container is already holding.
docker stop test-supervisor-stand-in-1 >/dev/null
if docker run --rm --network test_ma-test --ip 172.30.32.2 \
	-v "$(pwd)/case9_check.py:/check.py:ro" python:3.13-slim \
	sh -c "pip install --quiet --root-user-action=ignore 'websockets>=14.0' && python3 /check.py ${BASE}"; then
	check "case 9: bootstrap token authenticates real WS session" "0" "0"
else
	check "case 9: bootstrap token authenticates real WS session" "0" "1"
fi

echo
echo "${PASS} passed, ${FAIL} failed"
[[ "$FAIL" -eq 0 ]]
