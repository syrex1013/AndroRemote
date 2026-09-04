package com.ohmpi.androremote;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.Build;

/**
 * Autostart: after device boot, after every APK self-update
 * (MY_PACKAGE_REPLACED), and the 15-minute WatchdogReceiver alarm
 * all funnel into starting RemoteService.
 */
public class BootReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        String action = intent == null ? null : intent.getAction();
        if (Intent.ACTION_BOOT_COMPLETED.equals(action)
                || Intent.ACTION_MY_PACKAGE_REPLACED.equals(action)) {
            Intent i = new Intent(context, RemoteService.class);
            try {
                if (Build.VERSION.SDK_INT >= 26) context.startForegroundService(i);
                else context.startService(i);
            } catch (Exception ignored) {}
        }
    }
}
