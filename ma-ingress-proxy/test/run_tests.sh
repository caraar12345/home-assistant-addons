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

# Case: sidecar restart wipes every per-user token it previously minted (carol and dave
# from case 1/10 above, plus a fresh bootstrap token below), while leaving the admin
# token itself alone (no re-bootstrap).
admin_token_before=$(docker exec test-sidecar-1 cat /data/admin_token.json | python3 -c 'import json,sys;print(json.load(sys.stdin)["token"])')

# Mint a bootstrap token for a new user so the wipe check below also covers
# ha-ingress-bootstrap: tokens specifically, not just the ha-ingress: ones above.
$SUPERVISOR -D - -o /dev/null "${BASE}/" \
	-H 'X-Remote-User-Id: ha-user-frank' -H 'X-Remote-User-Name: frank' >/dev/null
mapping=$(docker exec test-sidecar-1 cat /data/mapping.json)
frank_id=$(echo "$mapping" | python3 -c 'import json,sys;print(json.load(sys.stdin)["ha-user-frank"])')
frank_tokens_before=$(curl -s -X POST http://127.0.0.1:18095/api -H "Authorization: Bearer ${admin_token_before}" \
	-d '{"message_id":"1","command":"auth/tokens","args":{"user_id":"'"${frank_id}"'"}}' \
	| python3 -c 'import json,sys;print([t["name"] for t in json.load(sys.stdin) if t["name"].startswith("ha-ingress-bootstrap:")])')
check "case wipe-on-boot: bootstrap token exists before restart" "['ha-ingress-bootstrap:ha-user-frank']" "$frank_tokens_before"

docker compose restart sidecar >/dev/null
for _ in $(seq 1 30); do
	docker run --rm --network test_ma-test curlimages/curl -sf -o /dev/null "http://sidecar:9000/healthz" && break
	sleep 1
done
admin_token_after=$(docker exec test-sidecar-1 cat /data/admin_token.json | python3 -c 'import json,sys;print(json.load(sys.stdin)["token"])')
check "case wipe-on-boot: admin token reused, not re-minted" "$admin_token_before" "$admin_token_after"

carol_tokens=$(curl -s -X POST http://127.0.0.1:18095/api -H "Authorization: Bearer ${admin_token_after}" \
	-d '{"message_id":"1","command":"auth/tokens","args":{"user_id":"'"${carol_id}"'"}}')
dave_tokens=$(curl -s -X POST http://127.0.0.1:18095/api -H "Authorization: Bearer ${admin_token_after}" \
	-d '{"message_id":"1","command":"auth/tokens","args":{"user_id":"'"${dave_id}"'"}}')
frank_tokens_after=$(curl -s -X POST http://127.0.0.1:18095/api -H "Authorization: Bearer ${admin_token_after}" \
	-d '{"message_id":"1","command":"auth/tokens","args":{"user_id":"'"${frank_id}"'"}}')
check "case wipe-on-boot: carol's old token revoked" "[]" "$carol_tokens"
check "case wipe-on-boot: dave's old token revoked" "[]" "$dave_tokens"
check "case wipe-on-boot: frank's bootstrap token revoked" "[]" "$frank_tokens_after"

# Case: Music Assistant already has a user whose username collides with the one a new
# HA user's username would derive to (e.g. someone set an account up by hand before this
# add-on ever ran). Regression test for the UNIQUE constraint failure this add-on used to
# let bubble straight up to the caller as a 502 instead of handling it.
admin_token=$(docker exec test-sidecar-1 cat /data/admin_token.json | python3 -c 'import json,sys;print(json.load(sys.stdin)["token"])')
grace_preexisting_id=$(curl -s -X POST http://127.0.0.1:18095/api -H "Authorization: Bearer ${admin_token}" \
	-d '{"message_id":"1","command":"auth/user/create","args":{"username":"grace","password":"PreexistingPassw0rd!","role":"user"}}' \
	| python3 -c 'import json,sys;print(json.load(sys.stdin)["user_id"])')

# Default on_username_conflict=create_new: provision a disambiguated account, not adopt
# the pre-existing one.
status=$($SUPERVISOR -o /dev/null -w '%{http_code}' "${BASE}/auth/me" \
	-H 'X-Remote-User-Id: ha-user-grace' -H 'X-Remote-User-Name: grace')
check "case username-conflict (create_new): colliding username still provisions -> 200" "200" "$status"
mapping=$(docker exec test-sidecar-1 cat /data/mapping.json)
grace_id=$(echo "$mapping" | python3 -c 'import json,sys;print(json.load(sys.stdin)["ha-user-grace"])')
if [[ -n "$grace_id" && "$grace_id" != "$grace_preexisting_id" ]]; then
	echo "PASS: case username-conflict (create_new): disambiguated, did not adopt the pre-existing account"
	PASS=$((PASS + 1))
else
	echo "FAIL: case username-conflict (create_new): expected a new user id, got the pre-existing account $grace_preexisting_id"
	FAIL=$((FAIL + 1))
fi

# on_username_conflict=adopt: link the HA user to the pre-existing account instead.
henry_preexisting_id=$(curl -s -X POST http://127.0.0.1:18095/api -H "Authorization: Bearer ${admin_token}" \
	-d '{"message_id":"1","command":"auth/user/create","args":{"username":"henry","password":"PreexistingPassw0rd!","role":"user"}}' \
	| python3 -c 'import json,sys;print(json.load(sys.stdin)["user_id"])')
MA_ON_USERNAME_CONFLICT=adopt docker compose up -d --force-recreate sidecar >/dev/null
for _ in $(seq 1 30); do
	docker run --rm --network test_ma-test curlimages/curl -sf -o /dev/null "http://sidecar:9000/healthz" && break
	sleep 1
done
status=$($SUPERVISOR -o /dev/null -w '%{http_code}' "${BASE}/auth/me" \
	-H 'X-Remote-User-Id: ha-user-henry' -H 'X-Remote-User-Name: henry')
check "case username-conflict (adopt): colliding username still provisions -> 200" "200" "$status"
mapping=$(docker exec test-sidecar-1 cat /data/mapping.json)
henry_id=$(echo "$mapping" | python3 -c 'import json,sys;print(json.load(sys.stdin)["ha-user-henry"])')
check "case username-conflict (adopt): adopted the pre-existing account" "$henry_preexisting_id" "$henry_id"

# Case 9 (mechanism check): the bootstrap redirect's token authenticates a real
# WebSocket session as the right user, the same mechanism the Music Assistant
# frontend uses after reading its ?code= query parameter. Must run from Supervisor's
# trusted address, which the long-lived stand-in container is already holding. Runs
# last: it permanently stops the stand-in container to free up its trusted IP address.
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
