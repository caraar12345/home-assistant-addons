"""Case 9 mechanism check: run from Supervisor's address (172.30.32.2).

Verifies the bootstrap redirect's token actually authenticates a real WebSocket
session as the right user - the same mechanism the Music Assistant frontend uses
after reading its `?code=` query parameter (see ../README.md).
"""

import asyncio
import json
import sys
import urllib.error
import urllib.request

import websockets

BASE = sys.argv[1]
HEADERS = {"X-Remote-User-Id": "ha-user-carol", "X-Remote-User-Name": "carol"}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


async def main() -> None:
    opener = urllib.request.build_opener(NoRedirect)
    try:
        opener.open(urllib.request.Request(f"{BASE}/", headers=HEADERS))
        sys.exit("expected a redirect")
    except urllib.error.HTTPError as err:
        if err.code != 302:
            sys.exit(f"expected 302, got {err.code}")
        token = err.headers["Location"].split("code=", 1)[1]

    ws_url = BASE.replace("http://", "ws://") + "/ws"
    async with websockets.connect(ws_url, additional_headers=HEADERS) as ws:
        await ws.recv()  # ServerInfo
        await ws.send(json.dumps({"message_id": "1", "command": "auth", "args": {"token": token}}))
        result = json.loads(await ws.recv())["result"]
        if not result.get("authenticated") or result["user"]["username"] != "carol":
            sys.exit(f"unexpected auth result: {result}")


asyncio.run(main())
