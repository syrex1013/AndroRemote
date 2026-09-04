#!/usr/bin/env python3
"""AndroRemote adb-direct CLI module."""

import argparse
import base64 as _b64
import os
import re
import socket
import subprocess
import sys

ADB = os.environ.get("ADB", os.path.expanduser("~/Library/Android/sdk/platform-tools/adb"))
SERIAL = os.environ.get("ANDROID_SERIAL")
LOCAL_PORT = 8741
AGENT_PORT = 8740


def adb(*args, check=True, timeout=30):
    cmd = [ADB]
    if SERIAL and "-s" not in args:
        cmd += ["-s", SERIAL]
    cmd += list(args)
    return subprocess.run(cmd, capture_output=True, check=check, timeout=timeout)


def devices():
    try:
        out = adb("devices").stdout.decode()
        return [l.split("\t")[0] for l in out.splitlines()[1:] if "\tdevice" in l]
    except Exception:
        return []


def pick_serial(a=None):
    if SERIAL:
        return SERIAL
    devs = devices()
    if not devs:
        sys.exit("ERR: no adb devices connected")
    return devs[0]


def adb_device(serial, *args, **kwargs):
    if serial:
        return adb("-s", serial, *args, **kwargs)
    return adb(*args, **kwargs)


def forward(serial):
    adb("-s", serial, "forward", f"tcp:{LOCAL_PORT}", f"tcp:{AGENT_PORT}")


def send_line(payload: bytes, expect_raw=0):
    s = socket.create_connection(("127.0.0.1", LOCAL_PORT), timeout=10)
    s.settimeout(120)
    try:
        s.sendall(payload)
        f = s.makefile("rb")
        first = f.readline().decode("utf-8", "replace").rstrip("\n")
        rest = b""
        if expect_raw:
            m = re.match(r"OK (\d+) ", first)
            if not m:
                raise RuntimeError(first or "connection closed")
            need = int(m.group(1))
            while len(rest) < need:
                chunk = f.read(min(65536, need - len(rest)))
                if not chunk:
                    raise RuntimeError(f"short read {len(rest)}/{need}")
                rest += chunk
        else:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                rest += chunk
        return first, rest
    finally:
        s.close()


def one_shot(cmd_line: str, expect_raw=0):
    first, raw = send_line(cmd_line.encode() + b"\n", expect_raw)
    return first, raw


def cmd_ping(a): print(one_shot("PING")[0])
def cmd_id(a): print(one_shot("ID")[0])
def cmd_perms(a): first, rest = one_shot("PERMS"); print(first); print(rest.decode("utf-8", "replace"), end="")
def cmd_log(a): first, rest = one_shot("LOG"); print(first); print(rest.decode("utf-8", "replace"), end="")
def cmd_shell(a): first, rest = one_shot("SHELL " + " ".join(a.args)); print(first); print(rest.decode("utf-8", "replace"), end="")
def cmd_ls(a): first, rest = one_shot("LS " + (a.path or "")); print(first); print(rest.decode("utf-8", "replace"), end="")
def cmd_smslog(a): first, rest = one_shot("SMSLOG " + (a.name or "")); print(first); print(rest.decode("utf-8", "replace"), end="")


def cmd_get(a):
    first, raw = one_shot("GET " + a.remote, 1)
    if first.startswith("OK"):
        with open(a.local, "wb") as f:
            f.write(raw)
        print(f"{len(raw)} bytes -> {a.local}")
    else:
        print(first)
        sys.exit(1)


def cmd_put(a):
    size = os.path.getsize(a.local)
    if size <= 0 or size > 50 * 1024 * 1024:
        sys.exit("ERR put: local file empty or too large")
    with open(a.local, "rb") as f:
        data = f.read()
    first, _ = send_line(f"PUT {size} {a.remote}\n".encode() + data)
    print(first)


def cmd_screen(a):
    try:
        first, raw = one_shot("SCREEN", 1)
    except RuntimeError as e:
        first, raw = str(e), b""
    if first.startswith("OK"):
        with open(a.local, "wb") as f:
            f.write(raw)
        print(f"{len(raw)} bytes -> {a.local}")
        return
    print(f"{first}; falling back to adb exec-out screencap")
    cmd = [ADB] + (["-s", SERIAL] if SERIAL else []) + ["exec-out", "screencap", "-p"]
    import subprocess as sp
    with open(a.local, "wb") as f:
        r = sp.run(cmd, stdout=f)
    if r.returncode != 0 or os.path.getsize(a.local) == 0:
        sys.exit("ERR screen: both agent and adb screencap failed")
    print(f"{os.path.getsize(a.local)} bytes -> {a.local} (via adb)")


def cmd_sms(a): print(one_shot("SMS " + a.number + " " + a.text)[0])
def cmd_calllog(a): first, rest = one_shot("CALLLOG " + (a.n or "25")); print(first); print(rest.decode("utf-8", "replace"), end="")
def cmd_call(a): print(one_shot("CALL " + a.number)[0])
def cmd_loc(a): print(one_shot("LOC")[0])
def cmd_photos(a): first, rest = one_shot("PHOTOS " + (a.n or "30")); print(first); print(rest.decode("utf-8", "replace"), end="")


def cmd_rec(a):
    first, _ = one_shot("RECORD " + str(a.secs))
    print(first)
    if not first.startswith("OK"):
        sys.exit(1)
    path = first.split()[2]
    dest = a.out or os.path.basename(path)
    ffirst, raw = one_shot("GET " + path, 1)
    if ffirst.startswith("OK"):
        with open(dest, "wb") as f:
            f.write(raw)
        print(f"{len(raw)} bytes -> {dest}")
    else:
        print(ffirst)
        sys.exit(1)


def cmd_launch(a):
    serial = pick_serial(a)
    adb_device(serial, "shell", "am", "start", "-n", "com.ohmpi.androremote/.MainActivity")
    adb_device(serial, "forward", f"tcp:{LOCAL_PORT}", f"tcp:{AGENT_PORT}")
    print("launched (permission requests); agent on 127.0.0.1:%d" % LOCAL_PORT)


def cmd_consent(a):
    serial = pick_serial(a)
    adb_device(serial, "shell", "am", "start", "-n", "com.ohmpi.androremote/.ConsentActivity")
    print("screen-capture consent dialog shown - tap 'Start now' on the phone")


def cmd_update(a):
    size = os.path.getsize(a.apk)
    if size <= 0 or size > 32 * 1024 * 1024:
        sys.exit("ERR update: apk empty or >32MB")
    remote = "/storage/emulated/0/Android/data/com.ohmpi.androremote/files/update.apk"
    with open(a.apk, "rb") as f:
        data = f.read()
    first, _ = send_line(("PUTB64 %s %s\n" % (_b64.b64encode(remote.encode()).decode(), _b64.b64encode(data).decode())).encode())
    print(first)
    if not first.startswith("OK"):
        sys.exit(1)
    print(one_shot("INSTALL " + remote)[0])


def cmd_axenable(a):
    serial = pick_serial(a)
    pkg = "com.ohmpi.androremote"
    svc = pkg + "/" + pkg + ".RemoteAccessibilityService"
    adb_device(serial, "shell", "settings", "put", "secure", "enabled_accessibility_services", svc)
    adb_device(serial, "shell", "settings", "put", "secure", "accessibility_enabled", "1")
    adb_device(serial, "shell", "appops", "set", pkg, "REQUEST_INSTALL_PACKAGES", "allow")
    adb_device(serial, "shell", "dumpsys", "deviceidle", "whitelist", "+" + pkg)
    for op in ("RUN_ANY_IN_BACKGROUND", "RUN_IN_BACKGROUND", "START_FOREGROUND"):
        adb_device(serial, "shell", "cmd", "appops", "set", pkg, op, "allow")
    print("accessibility + install-unknown + battery exemption + MIUI background-run granted (verify: perms)")


def cmd_axdisable(a):
    serial = pick_serial(a)
    adb_device(serial, "shell", "settings", "put", "secure", "enabled_accessibility_services", "''")
    adb_device(serial, "shell", "settings", "put", "secure", "accessibility_enabled", "0")
    print("accessibility service disabled")


def cmd_tap(a): print(one_shot("TAP %s %s" % (a.x, a.y))[0])
def cmd_swipe(a): print(one_shot("SWIPE %s %s %s %s %s" % (a.x1, a.y1, a.x2, a.y2, a.ms))[0])
def cmd_settext(a): print(one_shot("SETTEXT " + a.text)[0])
def cmd_gaction(a): print(one_shot("GACTION " + a.name)[0])
def cmd_sleep(a): print(one_shot("SLEEP")[0])
def cmd_unlock(a): print(one_shot("UNLOCK " + a.pin)[0])

def cmd_installstatus(a): print(one_shot("INSTALLSTATUS")[0])


def cmd_wake(a): print(one_shot("WAKE")[0])
def cmd_vol(a): first, rest = one_shot("VOL " + (a.v or "")); print(first); print(rest.decode("utf-8", "replace"), end="")
def cmd_clipset(a): print(one_shot("CLIPSET " + a.text)[0])
def cmd_clipget(a): print(one_shot("CLIPGET")[0])
def cmd_torch(a): print(one_shot("TORCH " + (a.s or "on"))[0])
def cmd_vibrate(a): print(one_shot("VIBRATE " + (a.ms or ""))[0])
def cmd_apps(a): first, rest = one_shot("APPS"); print(first); print(rest.decode("utf-8", "replace"), end="")
def cmd_startapp(a): print(one_shot("STARTAPP " + a.pkg)[0])
def cmd_notifs(a): first, rest = one_shot("NOTIFS " + (a.n or "")); print(first); print(rest.decode("utf-8", "replace"), end="")


def cmd_notifsenable(a):
    serial = pick_serial(a)
    adb_device(serial, "shell", "cmd", "notification", "allow_listener", "com.ohmpi.androremote/.NotifsListener")
    print("notification listener enabled (verify: notifs)")


def cmd_notifsdisable(a):
    serial = pick_serial(a)
    adb_device(serial, "shell", "cmd", "notification", "disallow_listener", "com.ohmpi.androremote/.NotifsListener")
    print("notification listener disabled")


def cmd_serve(_):
    print(f"bridge 127.0.0.1:{LOCAL_PORT} -> device:{AGENT_PORT} (adb forward)")
    if SERIAL:
        forward(SERIAL)
        print(f"forwarded serial={SERIAL}")
    else:
        for d in devices():
            forward(d)
    print("accepting connections; Ctrl-C to stop")
    try:
        while True:
            import time
            time.sleep(60)
            for d in devices():
                adb("-s", d, "forward", f"tcp:{LOCAL_PORT}", f"tcp:{AGENT_PORT}", check=False)
    except KeyboardInterrupt:
        print("\nbye")


def build_parser(prog="androremote adb"):
    p = argparse.ArgumentParser(prog=prog, description="AndroRemote adb-direct controller")
    sub = p.add_subparsers(dest="subcmd")

    sub.add_parser("serve", help="Run adb port forward bridge").set_defaults(fn=cmd_serve)
    sub.add_parser("launch", help="Launch MainActivity to request initial perms").set_defaults(fn=cmd_launch)
    sub.add_parser("consent", help="Show screen capture consent dialog").set_defaults(fn=cmd_consent)
    s = sub.add_parser("update", help="Push and trigger APK update"); s.add_argument("apk"); s.set_defaults(fn=cmd_update)
    sub.add_parser("axenable", help="Enable accessibility & background perms").set_defaults(fn=cmd_axenable)
    sub.add_parser("axdisable", help="Disable accessibility service").set_defaults(fn=cmd_axdisable)
    s = sub.add_parser("tap", help="Inject tap at x y"); s.add_argument("x"); s.add_argument("y"); s.set_defaults(fn=cmd_tap)
    s = sub.add_parser("swipe", help="Inject swipe gesture"); s.add_argument("x1"); s.add_argument("y1"); s.add_argument("x2"); s.add_argument("y2"); s.add_argument("ms", nargs="?", default="300"); s.set_defaults(fn=cmd_swipe)
    s = sub.add_parser("settext", help="Set text in focused field"); s.add_argument("text"); s.set_defaults(fn=cmd_settext)
    s = sub.add_parser("gaction", help="Trigger global action (back, home, recents, etc.)"); s.add_argument("name"); s.set_defaults(fn=cmd_gaction)
    sub.add_parser("installstatus", help="Get status of last package install").set_defaults(fn=cmd_installstatus)
    sub.add_parser("wake", help="Wake device screen").set_defaults(fn=cmd_wake)
    sub.add_parser("sleep", help="Lock screen / turn display off").set_defaults(fn=cmd_sleep)
    s = sub.add_parser("unlock", help="Wake and dismiss PIN keyguard"); s.add_argument("pin"); s.set_defaults(fn=cmd_unlock)
    s = sub.add_parser("vol", help="Query or set volume"); s.add_argument("v", nargs="?"); s.set_defaults(fn=cmd_vol)
    s = sub.add_parser("clipset", help="Set clipboard content"); s.add_argument("text"); s.set_defaults(fn=cmd_clipset)
    sub.add_parser("clipget", help="Get clipboard content").set_defaults(fn=cmd_clipget)
    s = sub.add_parser("torch", help="Toggle camera flashlight"); s.add_argument("s", nargs="?", choices=["on", "off"]); s.set_defaults(fn=cmd_torch)
    s = sub.add_parser("vibrate", help="Trigger device vibration"); s.add_argument("ms", nargs="?"); s.set_defaults(fn=cmd_vibrate)
    sub.add_parser("apps", help="List installed apps").set_defaults(fn=cmd_apps)
    s = sub.add_parser("startapp", help="Launch app package"); s.add_argument("pkg"); s.set_defaults(fn=cmd_startapp)
    s = sub.add_parser("notifs", help="Fetch notification logs"); s.add_argument("n", nargs="?"); s.set_defaults(fn=cmd_notifs)
    sub.add_parser("notifsenable", help="Enable notification listener").set_defaults(fn=cmd_notifsenable)
    sub.add_parser("notifsdisable", help="Disable notification listener").set_defaults(fn=cmd_notifsdisable)
    sub.add_parser("ping", help="Ping agent").set_defaults(fn=cmd_ping)
    sub.add_parser("id", help="Query agent ID and model").set_defaults(fn=cmd_id)
    sub.add_parser("perms", help="Query agent permissions").set_defaults(fn=cmd_perms)
    sub.add_parser("log", help="List SMS logs").set_defaults(fn=cmd_log)
    s = sub.add_parser("smslog", help="Read SMS log file"); s.add_argument("name", nargs="?"); s.set_defaults(fn=cmd_smslog)
    s = sub.add_parser("shell", help="Execute shell command"); s.add_argument("args", nargs=argparse.REMAINDER); s.set_defaults(fn=cmd_shell)
    s = sub.add_parser("ls", help="List files on device"); s.add_argument("path", nargs="?"); s.set_defaults(fn=cmd_ls)
    s = sub.add_parser("get", help="Download file from device"); s.add_argument("remote"); s.add_argument("local"); s.set_defaults(fn=cmd_get)
    s = sub.add_parser("put", help="Upload file to device"); s.add_argument("local"); s.add_argument("remote"); s.set_defaults(fn=cmd_put)
    s = sub.add_parser("screen", help="Capture screenshot"); s.add_argument("local"); s.set_defaults(fn=cmd_screen)
    s = sub.add_parser("sms", help="Send SMS"); s.add_argument("number"); s.add_argument("text"); s.set_defaults(fn=cmd_sms)
    s = sub.add_parser("calllog", help="Fetch call log"); s.add_argument("n", nargs="?"); s.set_defaults(fn=cmd_calllog)
    s = sub.add_parser("call", help="Place phone call"); s.add_argument("number"); s.set_defaults(fn=cmd_call)
    s = sub.add_parser("loc", help="Get GPS/network location").set_defaults(fn=cmd_loc)
    s = sub.add_parser("photos", help="List media photos"); s.add_argument("n", nargs="?"); s.set_defaults(fn=cmd_photos)
    s = sub.add_parser("rec", help="Record microphone audio"); s.add_argument("secs", type=int); s.add_argument("out", nargs="?"); s.set_defaults(fn=cmd_rec)

    return p


def main(argv=None):
    p = build_parser()
    a = p.parse_args(argv if argv is not None else sys.argv[1:])
    if not a.subcmd:
        a = p.parse_args(["serve"])
    a.fn(a)


if __name__ == "__main__":
    main()
