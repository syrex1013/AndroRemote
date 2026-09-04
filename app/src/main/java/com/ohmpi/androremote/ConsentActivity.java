package com.ohmpi.androremote;

import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.media.projection.MediaProjectionManager;
import android.os.Build;
import android.os.Bundle;

/**
 * Consent activity (visible, transparent theme). Launched from MainActivity
 * or via adb:
 *   adb shell am start -n com.ohmpi.androremote/.ConsentActivity
 * Shows the system MediaProjection dialog, hands the result to
 * CaptureService, and finishes. Not Theme.NoDisplay — that theme requires
 * finish() before onResume completes, which startActivityForResult forbids.
 */
public class ConsentActivity extends Activity {
    static final int REQ_CAP = 4243;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        try {
            MediaProjectionManager mpm = (MediaProjectionManager) getSystemService(MEDIA_PROJECTION_SERVICE);
            startActivityForResult(mpm.createScreenCaptureIntent(), REQ_CAP);
        } catch (Exception e) {
            finish();
        }
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode != REQ_CAP) {
            finish();
            return;
        }
        if (resultCode == RESULT_OK && data != null) {
            getSharedPreferences("cfg", MODE_PRIVATE).edit().putBoolean("cap_granted", true).apply();
            Intent i = new Intent(this, CaptureService.class);
            i.putExtra("code", resultCode);
            i.putExtra("data", data);
            if (Build.VERSION.SDK_INT >= 26) startForegroundService(i);
            else startService(i);
        }
        finish();
    }
}
