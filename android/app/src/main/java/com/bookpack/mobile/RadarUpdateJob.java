package com.bookpack.mobile;

import android.app.job.*;
import android.content.*;
import com.chaquo.python.Python;

/** Network-constrained daily checks, persisted across device reboots. */
public class RadarUpdateJob extends JobService {
    private static final int ID = 20261025;
    private volatile boolean stopped;
    static void schedule(Context context, boolean enabled) {
        JobScheduler scheduler=context.getSystemService(JobScheduler.class);
        if(!enabled){scheduler.cancel(ID);return;}
        if(scheduler.getPendingJob(ID)!=null)return;
        JobInfo job=new JobInfo.Builder(ID,new ComponentName(context,RadarUpdateJob.class))
            .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
            .setPeriodic(24*60*60*1000L,6*60*60*1000L).setPersisted(true)
            .setBackoffCriteria(30*60*1000L,JobInfo.BACKOFF_POLICY_LINEAR).build();
        scheduler.schedule(job);
    }
    @Override public boolean onStartJob(JobParameters parameters) {
        stopped=false;
        CoreRuntime.IO.execute(()->{
            boolean retry=true;
            try {
                CoreRuntime.start(this).get();
                if(!stopped)retry=!Python.getInstance().getModule("android_runtime").callAttr("daily_update").toBoolean();
            } catch(Exception error){android.util.Log.w("Bookpack","Daily radar update",error);}
            if(!stopped)jobFinished(parameters,retry);
        });
        return true;
    }
    @Override public boolean onStopJob(JobParameters parameters) {
        stopped=true;
        CoreRuntime.IO.execute(()->{try{if(Python.isStarted())Python.getInstance().getModule("android_runtime").callAttr("stop_daily");}catch(Exception ignored){}});
        return true;
    }
}
