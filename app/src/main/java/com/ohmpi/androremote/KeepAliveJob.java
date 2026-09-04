package com.ohmpi.androremote;

import android.app.job.JobInfo;
import android.app.job.JobParameters;
import android.app.job.JobScheduler;
import android.app.job.JobService;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.os.Build;

/**
 * Fourth restart mechanism: JobScheduler with a fast-ish periodic job.
 * Complements START_STICKY, WatchdogReceiver alarm, and the accessibility
 * rebind. Jobs are dispatched by the framework even when the app process
 * is dead, and are not subject to the "proc frequent died" gate that
 * MIUI applies to alarm-triggered restarts of crashed apps.
 */
public class KeepAliveJob extends JobService {
    static final int JOB_ID = 8743;

    @Override
    public boolean onStartJob(JobParameters params) {
        Intent i = new Intent(this, RemoteService.class);
        try {
            if (Build.VERSION.SDK_INT >= 26) startForegroundService(i);
            else startService(i);
        } catch (Exception ignored) {}
        schedule(this); // re-arm
        return false; // no background thread needed
    }

    @Override
    public boolean onStopJob(JobParameters params) {
        return true; // reschedule if killed mid-job
    }

    static void schedule(Context ctx) {
        try {
            JobScheduler js = (JobScheduler) ctx.getSystemService(JOB_SCHEDULER_SERVICE);
            if (js == null) return;
            JobInfo ji = new JobInfo.Builder(JOB_ID,
                    new ComponentName(ctx, KeepAliveJob.class))
                    .setPeriodic(15 * 60 * 1000L)        // framework minimum
                    .setPersisted(true)                   // survives reboot
                    .build();
            js.schedule(ji);
        } catch (Exception ignored) {}
    }
}
