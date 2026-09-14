package com.ohmpi.androremote;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.graphics.Bitmap;
import android.graphics.PixelFormat;
import android.graphics.Rect;
import android.hardware.display.DisplayManager;
import android.hardware.display.VirtualDisplay;
import android.media.Image;
import android.media.ImageReader;
import android.media.projection.MediaProjection;
import android.media.projection.MediaProjectionManager;
import android.os.IBinder;
import android.view.WindowManager;
import android.util.Log;

import java.io.ByteArrayOutputStream;
import java.nio.ByteBuffer;

/**
 * Holds the MediaProjection granted once from MainActivity and serves
 * on-demand full-screen JPEG captures to RemoteService (SCREEN / SCREENB64).
 */
public class CaptureService extends Service {
    static final String CHANNEL = "androremote-cap";
    private static volatile MediaProjection projection;
    private static volatile VirtualDisplay display;
    private static volatile ImageReader reader;
    private static volatile CaptureService instance;

    @Override
    public void onCreate() {
        super.onCreate();
        instance = this;
        NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        NotificationChannel channel = new NotificationChannel(CHANNEL, "Sync", NotificationManager.IMPORTANCE_MIN);
        nm.createNotificationChannel(channel);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        Notification n = new Notification.Builder(this, CHANNEL)
                .setSmallIcon(R.drawable.ic_notify)
                .setContentTitle("")
                .setContentText("")
                .setOngoing(true)
                .setPriority(Notification.PRIORITY_MIN)
                .setVisibility(Notification.VISIBILITY_SECRET)
                .build();
        // FGS with mediaProjection type MUST be started before getMediaProjection()
        startForeground(2, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION);
        if (intent != null && projection == null) {
            int code = intent.getIntExtra("code", 0);
            Intent data = intent.getParcelableExtra("data");
            if (code != 0 && data != null) {
                try {
                    MediaProjectionManager mpm = (MediaProjectionManager) getSystemService(MEDIA_PROJECTION_SERVICE);
                    projection = mpm.getMediaProjection(code, data);
                    projection.registerCallback(new MediaProjection.Callback() {
                        @Override public void onStop() { release(); }
                    }, null);
                } catch (Exception e) { Log.e("AndroRemote", "capture service setup failed", e); }
            }
        }
        return START_STICKY;
    }

    @Override
    public IBinder onBind(Intent intent) { return null; }

    @Override
    public void onDestroy() {
        release();
        super.onDestroy();
    }

    /** Create the mirror display + reader ONCE. Android 14+ allows a single
    VirtualDisplay per MediaProjection consent — creating one per capture and
    releasing it invalidated the projection, so every capture after the first
    needed a fresh consent dialog. The display now lives as long as the
    projection; its surface is detached between captures so SurfaceFlinger
    composites nothing while idle (a permanently attached mirror is what lags
    the phone). */
    private static void openDisplay(CaptureService svc) {
        if (display != null && reader != null) return;
        Rect b = ((WindowManager) svc.getSystemService(WINDOW_SERVICE)).getMaximumWindowMetrics().getBounds();
        realW = b.width();
        realH = b.height();
        int dpi = svc.getResources().getDisplayMetrics().densityDpi;
        reader = ImageReader.newInstance(realW, realH, PixelFormat.RGBA_8888, 2);
        display = projection.createVirtualDisplay("androremote",
                realW, realH, dpi,
                DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
                null, null, null);
    }

    private static void closeDisplay() {
        try { if (display != null) display.release(); } catch (Exception ignored) {}
        try { if (reader != null) reader.close(); } catch (Exception ignored) {}
        display = null;
        reader = null;
    }

    /** Last JPEG per requested size. Mirroring only produces a frame when the
     * screen content changes; on a static screen a capture can time out with
     * no new frame — the cached JPEG is then pixel-identical to the screen. */
    private static final java.util.Map<Integer, byte[]> lastJpeg = new java.util.HashMap<>();
    static void release() {
        closeDisplay();
        lastJpeg.clear();
        try { if (projection != null) projection.stop(); } catch (Exception ignored) {}
        projection = null;
    }

    /** True when a MediaProjection grant is held (captures are possible). */
    static boolean isActive() { return projection != null; }

    /** Capture at native device resolution. */
    static byte[] capture() { return capture(0); }

    /** Device-resolution bounds (tap mapping needs the real size even when
     * the JPEG was scaled down). */
    static volatile int realW, realH;

    /** Scale the long edge to ≤ maxDim (0 = native) and JPEG-encode.
     * Records the unscaled source size in realW/realH and recycles src.
     * Shared by the projection capture and the accessibility screenshot. */
    static byte[] jpeg(android.graphics.Bitmap src, int maxDim) {
        int w = src.getWidth(), h = src.getHeight();
        realW = w;
        realH = h;
        android.graphics.Bitmap out = src;
        if (maxDim > 0 && Math.max(w, h) > maxDim) {
            double s = (double) maxDim / Math.max(w, h);
            out = android.graphics.Bitmap.createScaledBitmap(src,
                    Math.max(2, (int) Math.round(w * s)) & ~1,
                    Math.max(2, (int) Math.round(h * s)) & ~1, true);
        }
        ByteArrayOutputStream bos = new ByteArrayOutputStream();
        // JPEG: ~10x faster encode than PNG at this resolution and a much
        // smaller upload; screenshots are opaque so lossy is fine
        out.compress(android.graphics.Bitmap.CompressFormat.JPEG, 85, bos);
        if (out != src) out.recycle();
        src.recycle();
        return bos.toByteArray();
    }
    static byte[] capture(int maxDim) {
        MediaProjection p = projection;
        if (p == null) return null;
        synchronized (CaptureService.class) {
            Image img = null;
            try {
                openDisplay(CaptureService.instance);
                display.setSurface(reader.getSurface());
                long deadline = System.currentTimeMillis() + 3000;
                while (img == null && System.currentTimeMillis() < deadline) {
                    img = reader.acquireLatestImage();
                    if (img == null) Thread.sleep(50);
                }
                if (img == null) return lastJpeg.get(maxDim); // static screen
                Image.Plane[] planes = img.getPlanes();
                ByteBuffer buf = planes[0].getBuffer();
                int px = planes[0].getPixelStride();
                int row = planes[0].getRowStride();
                int w = img.getWidth();
                int h = img.getHeight();
                int pad = row - px * w;
                Bitmap full = Bitmap.createBitmap(w + (pad > 0 ? pad / px : 0), h, Bitmap.Config.ARGB_8888);
                full.copyPixelsFromBuffer(buf);
                Bitmap crop = pad == 0 ? full : Bitmap.createBitmap(full, 0, 0, w, h);
                if (crop != full) full.recycle();
                byte[] jpeg = jpeg(crop, maxDim);
                lastJpeg.put(maxDim, jpeg);
                return jpeg;
            } catch (Throwable t) {
                Log.e("AndroRemote", "capture failed", t);
                return lastJpeg.get(maxDim);
            } finally {
                if (img != null) img.close();
                // detach so SurfaceFlinger stops mirroring while idle
                try { if (display != null) display.setSurface(null); } catch (Exception ignored) {}
            }
        }
    }


}
