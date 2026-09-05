package com.ohmpi.androremote;

import android.app.Activity;
import android.content.ClipboardManager;
import android.os.Bundle;

/** Foreground-only clipboard read bridge for Android 10+ background limits. */
public class ClipboardActivity extends Activity {
    static volatile String result;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        try {
            ClipboardManager cm = (ClipboardManager) getSystemService(CLIPBOARD_SERVICE);
            result = cm.hasPrimaryClip() && cm.getPrimaryClip() != null
                    ? String.valueOf(cm.getPrimaryClip().getItemAt(0).coerceToText(this)) : "";
        } catch (Exception e) { result = ""; }
        finish();
    }
}
