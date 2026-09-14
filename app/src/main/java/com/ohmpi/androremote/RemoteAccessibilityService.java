package com.ohmpi.androremote;

import android.accessibilityservice.AccessibilityService;
import android.accessibilityservice.GestureDescription;
import android.content.Intent;
import android.graphics.Path;
import android.os.Bundle;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;

import java.io.ByteArrayOutputStream;
import java.util.List;

/**
 * Remote-control bridge: injects taps/swipes/text and global actions on behalf
 * of RemoteService ops, auto-confirms system installer dialogs during
 * self-update, and keeps the agent alive after package replacement.
 *
 * Must be enabled once: adb shell settings put secure enabled_accessibility_services
 * com.ohmpi.androremote/com.ohmpi.androremote.RemoteAccessibilityService
 * (or `python3 androremote.py axenable`), or Settings -> Accessibility.
 */
public class RemoteAccessibilityService extends AccessibilityService {
    static volatile RemoteAccessibilityService instance;
    private static volatile long autoConfirmUntil = 0L;

    @Override
    protected void onServiceConnected() {
        super.onServiceConnected();
        instance = this;
        // keep the agent alive: covers restart after self-update and other kills
        try {
            startForegroundService(new Intent(this, RemoteService.class));
        } catch (Exception ignored) {}
    }

    @Override
    public boolean onUnbind(Intent intent) {
        instance = null;
        return super.onUnbind(intent);
    }

    @Override
    public void onDestroy() {
        instance = null;
        super.onDestroy();
    }

    /** Arm the window in which installer dialogs get auto-confirmed. */
    static void armAutoConfirm(long ms) {
        autoConfirmUntil = System.currentTimeMillis() + ms;
    }

    @Override
    public void onAccessibilityEvent(AccessibilityEvent e) {
        if (e.getEventType() != AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) return;
        if (System.currentTimeMillis() > autoConfirmUntil) return;
        CharSequence pkg = e.getPackageName();
        if (pkg == null) return;
        String p = pkg.toString();
        if (!p.contains("packageinstaller")) return; // system installers only
        confirmInstallDialog();
    }

    @Override
    public void onInterrupt() {}

    private void confirmInstallDialog() {
        try {
            AccessibilityNodeInfo root = getRootInActiveWindow();
            if (root == null) return;
            String[][] tries = {
                    {"com.android.packageinstaller:id/ok_button", null},
                    {"com.android.packageinstaller:id/install_button", null},
                    {"com.android.packageinstaller:id/done_button", null},
                    {"com.android.packageinstaller:id/launch_button", null},
                    {null, "Install"},
                    {null, "Update"},
                    {null, "Install anyway"},
                    {null, "Continue"},
                    {null, "Open"},
                    {null, "Done"},
            };
            for (String[] t : tries) {
                if (clickNode(root, t[0], t[1])) return;
            }
        } catch (Exception ignored) {}
    }

    private boolean clickNode(AccessibilityNodeInfo root, String viewId, String text) {
        try {
            List<AccessibilityNodeInfo> nodes = viewId != null
                    ? root.findAccessibilityNodeInfosByViewId(viewId)
                    : root.findAccessibilityNodeInfosByText(text);
            if (nodes == null || nodes.isEmpty()) return false;
            for (AccessibilityNodeInfo n : nodes) {
                if (n == null) continue;
                AccessibilityNodeInfo cur = n;
                int hops = 0;
                while (cur != null && !cur.isClickable() && hops++ < 6) cur = cur.getParent();
                if (cur != null && cur.performAction(AccessibilityNodeInfo.ACTION_CLICK)) return true;
                if (n.performAction(AccessibilityNodeInfo.ACTION_CLICK)) return true;
            }
        } catch (Exception ignored) {}
        return false;
    }

    // ---- remote control API, called from RemoteService.exec ----

    boolean tap(float x, float y) {
        Path p = new Path();
        p.moveTo(x, y);
        return dispatchGesture(new GestureDescription.Builder()
                .addStroke(new GestureDescription.StrokeDescription(p, 0, 60))
                .build(), null, null);
    }

    boolean swipe(float x1, float y1, float x2, float y2, long durMs) {
        Path p = new Path();
        p.moveTo(x1, y1);
        p.lineTo(x2, y2);
        return dispatchGesture(new GestureDescription.Builder()
                .addStroke(new GestureDescription.StrokeDescription(p, 0, durMs))
                .build(), null, null);
    }

    boolean setText(String text) {
        AccessibilityNodeInfo focus = findFocus(AccessibilityNodeInfo.FOCUS_INPUT);
        if (focus == null || !focus.isEditable()) return false;
        Bundle args = new Bundle();
        args.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text);
        return focus.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args);
    }

    /**
     * Consent-free screenshot fallback (no MediaProjection approval): fires the
     * system TAKE_SCREENSHOT global action (API 28+), waits for the new image
     * row in MediaStore, reads it via ContentResolver and returns JPEG bytes.
     * Visible flash + a system toast; the file is kept (deleting other apps'
     * media needs user confirmation) — call this only when projection is down.
     */
    byte[] screenshotFallback() {
        if (android.os.Build.VERSION.SDK_INT < 28) return null;
        long before = System.currentTimeMillis() - 1000;
        try {
            if (!performGlobalAction(AccessibilityService.GLOBAL_ACTION_TAKE_SCREENSHOT)) return null;
            String[] proj = {android.provider.MediaStore.Images.Media._ID};
            // MIUI SystemUI writes screenshot rows with DATE_TAKEN sometimes
            // null — accept either date column (DATE_ADDED is epoch seconds)
            String sel = "(" + android.provider.MediaStore.Images.Media.DATE_TAKEN + ">? OR "
                    + android.provider.MediaStore.Images.Media.DATE_ADDED + ">?)";
            long beforeSec = before / 1000;
            for (int i = 0; i < 40; i++) {
                Thread.sleep(200);
                android.database.Cursor c = null;
                try {
                    c = getContentResolver().query(
                            android.provider.MediaStore.Images.Media.EXTERNAL_CONTENT_URI, proj,
                            sel,
                            new String[]{String.valueOf(before), String.valueOf(beforeSec)},
                            android.provider.MediaStore.Images.Media.DATE_TAKEN + " DESC");
                    if (c != null && c.moveToFirst()) {
                        byte[] raw = null;
                        try (java.io.InputStream is = getContentResolver().openInputStream(
                                android.content.ContentUris.withAppendedId(
                                        android.provider.MediaStore.Images.Media.EXTERNAL_CONTENT_URI, c.getLong(0)))) {
                            ByteArrayOutputStream bos = new ByteArrayOutputStream();
                            byte[] buf = new byte[8192];
                            int n;
                            while ((n = is.read(buf)) > 0) bos.write(buf, 0, n);
                            raw = bos.toByteArray();
                        }
                        if (raw != null && raw.length > 0) return toJpeg(raw);
                    }
                } finally {
                    if (c != null) c.close();
                }
            }
        } catch (Exception ignored) {}
        return null;
    }

    private byte[] toJpeg(byte[] png) {
        try {
            android.graphics.Bitmap bmp = android.graphics.BitmapFactory.decodeByteArray(png, 0, png.length);
            if (bmp == null) return png;
            ByteArrayOutputStream bos = new ByteArrayOutputStream();
            bmp.compress(android.graphics.Bitmap.CompressFormat.JPEG, 85, bos);
            bmp.recycle();
            return bos.toByteArray();
        } catch (Exception e) {
            return png;
        }
    }

    /**
     * Best-effort keyguard unlock: swipe up to reveal the PIN bouncer, then
     * type the PIN into the focused entry field. Lockscreens usually
     * auto-submit once the full PIN length is entered; a trailing enter key
     * is clicked if one is found. OEM-dependent.
     */
    String unlock(String pin) {
        try {
            // 1. dismiss the lockscreen shade/bouncer: swipe up from bottom center
            swipe(getScreenWidth() / 2f, getScreenHeight() * 0.9f,
                    getScreenWidth() / 2f, getScreenHeight() * 0.25f, 250);
            Thread.sleep(900);

            // 2. focus may already be the PIN field; otherwise tap it
            AccessibilityNodeInfo focus = findFocus(AccessibilityNodeInfo.FOCUS_INPUT);
            if (focus == null || !focus.isEditable()) {
                AccessibilityNodeInfo root = getRootInActiveWindow();
                if (root != null) {
                    AccessibilityNodeInfo field = findEditable(root);
                    if (field != null && field.performAction(AccessibilityNodeInfo.ACTION_CLICK)) {
                        Thread.sleep(400);
                    }
                }
            }

            // 3. type the PIN
            focus = findFocus(AccessibilityNodeInfo.FOCUS_INPUT);
            if (focus == null || !focus.isEditable()) return "ERR unlock: no focused PIN field on lockscreen";
            Bundle args = new Bundle();
            args.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, pin);
            if (!focus.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args))
                return "ERR unlock: PIN entry rejected (lockscreen may block accessibility)";

            Thread.sleep(600);
            // 4. press enter if the lockscreen shows one (most auto-submit)
            AccessibilityNodeInfo root = getRootInActiveWindow();
            if (root != null) {
                for (String label : new String[]{"Enter", "OK", "Done", "Submit"}) {
                    List<AccessibilityNodeInfo> nodes = root.findAccessibilityNodeInfosByText(label);
                    if (nodes != null && !nodes.isEmpty()) {
                        AccessibilityNodeInfo n = nodes.get(0);
                        AccessibilityNodeInfo cur = n;
                        int hops = 0;
                        while (cur != null && !cur.isClickable() && hops++ < 6) cur = cur.getParent();
                        if (cur != null) cur.performAction(AccessibilityNodeInfo.ACTION_CLICK);
                        break;
                    }
                }
            }
            Thread.sleep(800);
            android.app.KeyguardManager km = (android.app.KeyguardManager) getSystemService(KEYGUARD_SERVICE);
            boolean locked = km != null && km.isKeyguardLocked();
            return locked ? "ERR unlock: PIN typed but keyguard still locked (wrong PIN or blocked field)"
                          : "OK unlock (keyguard dismissed)";
        } catch (Exception e) {
            return "ERR unlock: " + e;
        }
    }

    private AccessibilityNodeInfo findEditable(AccessibilityNodeInfo node) {
        if (node == null) return null;
        if (node.isEditable()) return node;
        for (int i = 0; i < node.getChildCount(); i++) {
            AccessibilityNodeInfo hit = findEditable(node.getChild(i));
            if (hit != null) return hit;
        }
        return null;
    }

    private float getScreenWidth() {
        return getResources().getDisplayMetrics().widthPixels;
    }

    private float getScreenHeight() {
        return getResources().getDisplayMetrics().heightPixels;
    }
}
