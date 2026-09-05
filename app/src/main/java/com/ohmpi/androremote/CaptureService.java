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
 * on-demand full-screen PNG captures to RemoteService (SCREEN / SCREENB64).
 */
public class CaptureService extends Service {
    static final String CHANNEL = "androremote-cap";
    private static volatile MediaProjection projection;
    private static volatile VirtualDisplay display;
    private static volatile ImageReader reader;

    @Override
    public void onCreate() {
        super.onCreate();
        NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        NotificationChannel channel = new NotificationChannel(CHANNEL, "Sync", NotificationManager.IMPORTANCE_MIN);
        channel.setShowBadge(false);
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
                    setup();
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

    private void setup() {
        Rect b = ((WindowManager) getSystemService(WINDOW_SERVICE)).getMaximumWindowMetrics().getBounds();
        int dpi = getResources().getDisplayMetrics().densityDpi;
        reader = ImageReader.newInstance(b.width(), b.height(), PixelFormat.RGBA_8888, 2);
        display = projection.createVirtualDisplay("androremote",
                b.width(), b.height(), dpi,
                DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
                reader.getSurface(), null, null);
    }

    static void release() {
        try { if (display != null) display.release(); } catch (Exception ignored) {}
        try { if (reader != null) reader.close(); } catch (Exception ignored) {}
        display = null;
        reader = null;
        try { if (projection != null) projection.stop(); } catch (Exception ignored) {}
        projection = null;
    }

    /** Capture one full-screen PNG, or null if projection is not active. */
    static boolean isActive() { return projection != null && reader != null; }

    static byte[] capture() {
        MediaProjection p = projection;
        ImageReader r = reader;
        if (p == null || r == null) return null;
        synchronized (CaptureService.class) {
            Image img = null;
            try {
                long deadline = System.currentTimeMillis() + 3000;
                while (img == null && System.currentTimeMillis() < deadline) {
                    img = r.acquireLatestImage();
                    if (img == null) Thread.sleep(50);
                }
                if (img == null) return null;
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
                ByteArrayOutputStream bos = new ByteArrayOutputStream();
                crop.compress(Bitmap.CompressFormat.PNG, 100, bos);
                return bos.toByteArray();
            } catch (Exception e) {
                Log.e("AndroRemote", "capture failed", e);
                return null;
            } finally {
                if (img != null) img.close();
            }
        }
    }
}
