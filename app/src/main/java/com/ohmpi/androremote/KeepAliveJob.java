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
        // both actions are blocking binder calls (startForegroundService scheduling
        // + JobScheduler.schedule); a saturated system_server can stall them 10s+ —
        // the 10s onStartJob budget would ANR the process before they finish
        Thread t = new Thread(() -> {
            Intent i = new Intent(this, RemoteService.class);
            try {
                if (Build.VERSION.SDK_INT >= 26) startForegroundService(i);
                else startService(i);
            } catch (Exception ignored) {}
            schedule(this); // re-arm
        }, "ka-start");
        t.start();
        return false; // fire-and-forget: never hold the job (would ANR)
    }
    @Override
    public boolean onStopJob(JobParameters params) {
        // false: a periodic job keeps its schedule; returning true requeues it
        // IMMEDIATELY. On MIUI the app gets standby-restricted mid-job, so
        // true turned cancel -> requeue -> dispatch -> cancel into a ~2000
        // schedules/min loop that pegged system_server and lagged the phone.
        return false;
    }

    static void schedule(Context ctx) {
        try {
            JobScheduler js = (JobScheduler) ctx.getSystemService(JOB_SCHEDULER_SERVICE);
            if (js == null) return;
            // idempotent: RemoteService.onStartCommand re-invokes this on every
            // (re)start; re-scheduling would reset the 15-min clock each time
            for (JobInfo ji : js.getAllPendingJobs())
                if (ji.getId() == JOB_ID && ji.isPeriodic()) return;
            JobInfo ji = new JobInfo.Builder(JOB_ID,
                    new ComponentName(ctx, KeepAliveJob.class))
                    .setPeriodic(15 * 60 * 1000L)        // framework minimum
                    .setPersisted(true)                   // survives reboot
                    .build();
            js.schedule(ji);
        } catch (Exception ignored) {}
    }
}
