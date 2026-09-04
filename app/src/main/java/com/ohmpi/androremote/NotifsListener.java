package com.ohmpi.androremote;

import android.app.Notification;
import android.service.notification.NotificationListenerService;
import android.service.notification.StatusBarNotification;

import java.io.File;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

/**
 * Logs every posted notification to <external>/notifs.txt for the NOTIFS op.
 * Enable once: adb shell cmd notification allow_listener
 *   com.ohmpi.androremote/.NotifsListener   (or `androremote.py notifsenable`)
 */
public class NotifsListener extends NotificationListenerService {
    private static final SimpleDateFormat TS = new SimpleDateFormat("MM-dd HH:mm:ss", Locale.US);

    @Override
    public void onNotificationPosted(StatusBarNotification sbn) {
        try {
            Notification n = sbn.getNotification();
            if (n == null || sbn.getPackageName().equals("com.ohmpi.androremote")) return;
            CharSequence t = n.tickerText;
            if (t == null && n.extras != null) {
                CharSequence big = n.extras.getCharSequence(Notification.EXTRA_BIG_TEXT);
                CharSequence line = n.extras.getCharSequence(Notification.EXTRA_TEXT);
                t = big != null ? big : line;
            }
            String text = t == null ? "" : t.toString().replace('\n', ' ');
            File f = new File(getExternalFilesDir(null), "notifs.txt");
            if (f.length() > 1_000_000) f.delete();
            try (FileOutputStream fos = new FileOutputStream(f, true)) {
                fos.write((TS.format(new Date()) + " " + sbn.getPackageName() + " | " + text + "\n")
                        .getBytes(StandardCharsets.UTF_8));
            }
        } catch (Exception ignored) {}
    }
}
