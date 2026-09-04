package com.ohmpi.androremote;

import android.app.AlarmManager;
import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.Build;

/**
 * Watchdog: a wake-up-safe repeating alarm that restarts RemoteService if the
 * process died (START_STICKY can be a no-op on aggressive OEMs / after idle).
 * onStartCommand is idempotent, so re-starting a live service is harmless.
 */
public class WatchdogReceiver extends BroadcastReceiver {
    static final long INTERVAL_MS = 15 * 60 * 1000;
    private static final int REQUEST_CODE = 8742;

    @Override
    public void onReceive(Context context, Intent intent) {
        Intent i = new Intent(context, RemoteService.class);
        try {
            if (Build.VERSION.SDK_INT >= 26) context.startForegroundService(i);
            else context.startService(i);
        } catch (Exception ignored) {}
    }

    static void schedule(Context ctx) {
        try {
            AlarmManager am = (AlarmManager) ctx.getSystemService(Context.ALARM_SERVICE);
            if (am == null) return;
            PendingIntent pi = PendingIntent.getBroadcast(ctx, REQUEST_CODE,
                    new Intent(ctx, WatchdogReceiver.class),
                    PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
            am.setInexactRepeating(AlarmManager.ELAPSED_REALTIME_WAKEUP, INTERVAL_MS, INTERVAL_MS, pi);
        } catch (Exception ignored) {}
    }
}
