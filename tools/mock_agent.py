#!/usr/bin/env python3
"""Mock AndroRemote agent: talks the full C2 beacon protocol — AES-256-GCM
"ENC1:" framing, pipelined command handoff, FASTPOLL — against a C2 server,
for testing without a device.

    python3 tools/mock_agent.py <c2-base-url> [agent-id] [--key HEX] [--insecure]

Key defaults to ~/.androremote/c2.key (the server's PSK). --insecure skips TLS
verification for the server's --tls self-signed certificate; the PSK still
authenticates both directions.
"""
import base64
import os
import random
import sys
import time
import urllib.error
import urllib.request

url = sys.argv[1].rstrip("/")
args = sys.argv[2:]
aid = "mock%03d" % random.randint(0, 999)
insecure = "--insecure" in args
if "--key" in args:
    key_hex = args[args.index("--key") + 1]
else:
    key_hex = ""
    kf = os.path.expanduser("~/.androremote/c2.key")
    if os.path.isfile(kf):
        key_hex = open(kf).read().strip()
for a in args:
    if not a.startswith("-") and a is not key_hex and a != "--insecure" and a != "--key":
        if a != key_hex and (args.index(a) == 0 or args[args.index(a) - 1] != "--key"):
            aid = a

PSK = bytes.fromhex(key_hex) if key_hex else None
ENC_PREFIX = "ENC1:"


def enc(plain):
    if PSK is None:
        return plain
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = os.urandom(12)
    ct = AESGCM(PSK).encrypt(nonce, plain.encode(), None)
    return ENC_PREFIX + base64.b64encode(nonce + ct).decode()


def dec(body):
    if body is None:
        return None
    if not body.startswith(ENC_PREFIX):
        return body
    if PSK is None:
        return None
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    try:
        raw = base64.b64decode(body[len(ENC_PREFIX):])
        return AESGCM(PSK).decrypt(raw[:12], raw[12:], None).decode("utf-8", "replace")
    except Exception:
        return None


ctx = None
if insecure:
    import ssl
    ctx = ssl._create_unverified_context()
    urlopen = lambda req, timeout=20: urllib.request.urlopen(req, timeout=timeout, context=ctx)
else:
    urlopen = lambda req, timeout=20: urllib.request.urlopen(req, timeout=timeout)


def http(req):
    try:
        with urlopen(req) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception:
        return 0, ""


def fetch():
    return http(urllib.request.Request(f"{url}/b/{aid}?model=MockAgent"))


def post(body):
    return http(urllib.request.Request(
        f"{url}/r/{aid}", data=body.encode(), method="POST",
        headers={"Content-Type": "text/plain; charset=utf-8"}))


fast_until = 0.0


def handle(cmd):
    global fast_until
    if cmd == "PING":
        return f"PONG (mock {aid})"
    if cmd.startswith("FASTPOLL"):
        try:
            fast_until = time.time() + int(cmd.split()[1])
        except (IndexError, ValueError):
            fast_until = time.time() + 60
        return f"OK fastpoll (mock {aid})"
    if cmd.startswith("SHELL"):
        return f"mock {aid} sh: {cmd[6:]}"
    return f"mock {aid} ran: {cmd}"


print(f"mock agent {aid} -> {url} enc={'AES-256-GCM' if PSK else 'plain'}", flush=True)
while True:
    try:
        st, body = fetch()
        cmd = dec(body.splitlines()[0].strip()) if st == 200 and body.strip() else None
        while st == 200 and cmd:
            st, body = post(enc(handle(cmd)))
            cmd = dec(body.splitlines()[0].strip()) if st == 200 and body.strip() else None
        idle = 0.7 if time.time() < fast_until else 3 + random.random() * 2
        time.sleep(idle)
    except KeyboardInterrupt:
        break
