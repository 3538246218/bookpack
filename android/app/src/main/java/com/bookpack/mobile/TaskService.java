package com.bookpack.mobile;

import android.app.*;
import android.content.*;
import android.os.*;
import com.chaquo.python.Python;
import org.json.JSONObject;
import java.util.concurrent.*;

public class TaskService extends Service {
    private static final String CHANNEL = "bookpack_tasks";
    private ScheduledExecutorService timer;
    private long lastBusy;
    static void keep(Context context, String title) {
        Intent intent = new Intent(context, TaskService.class).putExtra("title", title);
        if (Build.VERSION.SDK_INT >= 26) context.startForegroundService(intent); else context.startService(intent);
    }
    @Override public void onCreate() {
        super.onCreate(); lastBusy = System.currentTimeMillis();
        if (Build.VERSION.SDK_INT >= 26) getSystemService(NotificationManager.class).createNotificationChannel(new NotificationChannel(CHANNEL, "书包获取任务", NotificationManager.IMPORTANCE_LOW));
        startForeground(7, notification("正在准备任务…"));
        timer = Executors.newSingleThreadScheduledExecutor();
        timer.scheduleWithFixedDelay(() -> {
            try {
                boolean busy = CoreRuntime.exports.get() > 0;
                String title = busy ? "正在保存文件…" : "正在获取书籍…";
                if (CoreRuntime.baseUrl() != null) {
                    JSONObject status = new JSONObject(Python.getInstance().getModule("android_runtime").callAttr("task_status").toString());
                    busy |= status.optBoolean("active");
                    if (!status.optString("title").isEmpty()) title = status.optString("title");
                }
                if (busy) { lastBusy = System.currentTimeMillis(); getSystemService(NotificationManager.class).notify(7, notification(title)); }
                else if (System.currentTimeMillis() - lastBusy > 5000) stopSelf();
            } catch (Exception error) { android.util.Log.w("Bookpack", "Task notification", error); }
        }, 1, 1, TimeUnit.SECONDS);
    }
    private Notification notification(String text) {
        Intent intent = new Intent(this, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        PendingIntent pending = PendingIntent.getActivity(this, 0, intent, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        Notification.Builder builder = Build.VERSION.SDK_INT >= 26 ? new Notification.Builder(this, CHANNEL) : new Notification.Builder(this);
        return builder.setSmallIcon(R.drawable.ic_bookpack).setContentTitle("书包").setContentText(text).setContentIntent(pending).setOngoing(true).build();
    }
    @Override public int onStartCommand(Intent intent, int flags, int startId) { lastBusy = System.currentTimeMillis(); return START_NOT_STICKY; }
    @Override public IBinder onBind(Intent intent) { return null; }
    @Override public void onDestroy() { if (timer != null) timer.shutdownNow(); stopForeground(true); super.onDestroy(); }
    @Override public void onTimeout(int startId, int foregroundServiceType) {
        CoreRuntime.IO.execute(() -> { try { Python.getInstance().getModule("android_runtime").callAttr("interrupt"); } catch (Exception ignored) {} });
        stopSelf();
    }
}
