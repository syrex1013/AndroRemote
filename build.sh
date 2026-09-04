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
"$BT/apksigner" sign --ks "$ROOT/keystore/release.keystore" --ks-pass pass:androremote \
    --out "$OUT/androremote.apk" "$OUT/z2.apk"
echo "OK: $OUT/androremote.apk (c2_url=${C2URL:-<none>} enc=${C2KEY:+AES-256-GCM} pin=${C2PIN:+set})"
