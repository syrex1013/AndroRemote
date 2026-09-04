package com.ohmpi.androremote;

import android.Manifest;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Bundle;
import android.telephony.SmsMessage;

import java.io.File;
import java.io.FileOutputStream;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

public class SmsReceiver extends BroadcastReceiver {
    private static final String ACTION = "android.provider.Telephony.SMS_RECEIVED";

    @Override
    public void onReceive(Context context, Intent intent) {
        if (!ACTION.equals(intent.getAction())) return;
        if (!hasPerm(context, Manifest.permission.RECEIVE_SMS)) return;
        Bundle b = intent.getExtras();
        if (b == null) return;
        StringBuilder body = new StringBuilder();
        String sender = null;
        Object[] pdus = (Object[]) b.get("pdus");
        String fmt = b.getString("format");
        if (pdus != null) {
            for (Object pdu : pdus) {
                SmsMessage msg = fmt != null
                        ? SmsMessage.createFromPdu((byte[]) pdu, fmt)
                        : SmsMessage.createFromPdu((byte[]) pdu);
                if (sender == null) sender = msg.getOriginatingAddress();
                body.append(msg.getDisplayMessageBody());
            }
        }
        save(context, sender, body.toString());
    }

    void save(Context context, String sender, String body) {
        try {
            File dir = new File(context.getExternalFilesDir(null), "logs");
            dir.mkdirs();
            String ts = new SimpleDateFormat("yyyyMMdd_HHmmss_SSS", Locale.US).format(new Date());
            File f = new File(dir, "sms_" + ts + ".txt");
            try (FileOutputStream fos = new FileOutputStream(f)) {
                fos.write(("from=" + sender + "\n" + body + "\n").getBytes(java.nio.charset.StandardCharsets.UTF_8));
            }
        } catch (Exception ignored) {}
    }

    boolean hasPerm(Context c, String p) {
        return c.checkSelfPermission(p) == PackageManager.PERMISSION_GRANTED;
    }
}
