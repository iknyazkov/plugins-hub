#!/usr/bin/env python3
"""yag notify hook: raw proxy.

Reads a hook event from stdin and POSTs it verbatim (base64-wrapped)
to the gateway. Any failure is swallowed: the hook must never interfere
with the session.
"""
import base64
import json
import sys
import urllib.request

GATEWAY = "http://localhost:63991/send"
CHANNEL = "channel"
REQUEST_TIMEOUT = 2


def main():
    try:
        raw = sys.stdin.buffer.read()
        payload = json.dumps({
            "name": CHANNEL,
            "payload": {"message": base64.b64encode(raw).decode()},
        }).encode()

        request = urllib.request.Request(
            GATEWAY,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT)
    except Exception:
        pass

    sys.exit(0)


if __name__ == "__main__":
    main()
