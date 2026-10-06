package app.tokenpace.widget;

import android.app.job.JobInfo;
import android.app.job.JobParameters;
import android.app.job.JobScheduler;
import android.app.job.JobService;
import android.content.ComponentName;
import android.content.Context;
import android.os.Build;

/**
 * Runs the widget's fetch under JobScheduler, which keeps the process and the
 * network up for the whole request (a bare thread from a broadcast can be killed
 * first and leave the widget on "refreshing…"). JOB_NOW serves taps; JOB_PERIODIC
 * refreshes every 15 minutes, Android's minimum, and survives reboots.
 */
public class RefreshJob extends JobService {
    static final int JOB_NOW = 1;
    static final int JOB_PERIODIC = 2;
    static final long PERIOD_MS = 15 * 60 * 1000L;

    static JobScheduler scheduler(Context context) {
        return (JobScheduler) context.getSystemService(Context.JOB_SCHEDULER_SERVICE);
    }

    static ComponentName component(Context context) {
        return new ComponentName(context, RefreshJob.class);
    }

    static void scheduleNow(Context context) {
        JobScheduler js = scheduler(context);
        if (Build.VERSION.SDK_INT >= 31) {
            try {
                js.schedule(new JobInfo.Builder(JOB_NOW, component(context))
                    .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
                    .setExpedited(true)
                    .build());
                return;
            } catch (RuntimeException e) {
                // Expedited quota or limits reached: fall through to a plain job.
            }
        }
        js.schedule(new JobInfo.Builder(JOB_NOW, component(context))
            .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
            .setOverrideDeadline(5000)
            .build());
    }

    static void schedulePeriodic(Context context) {
        JobScheduler js = scheduler(context);
        if (js.getPendingJob(JOB_PERIODIC) != null) {
            return;
        }
        js.schedule(new JobInfo.Builder(JOB_PERIODIC, component(context))
            .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
            .setPeriodic(PERIOD_MS)
            .setPersisted(true)
            .build());
    }

    static void cancelAll(Context context) {
        JobScheduler js = scheduler(context);
        js.cancel(JOB_NOW);
        js.cancel(JOB_PERIODIC);
    }

    @Override
    public boolean onStartJob(final JobParameters params) {
        final Context app = getApplicationContext();
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    UsageWidget.refreshBlocking(app);
                } finally {
                    jobFinished(params, false);
                }
            }
        }, "tokenpace-job").start();
        return true;
    }

    @Override
    public boolean onStopJob(JobParameters params) {
        return true;
    }
}
