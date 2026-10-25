package com.bookpack.mobile;

import android.content.Context;
import android.content.res.AssetManager;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicInteger;

final class CoreRuntime {
    static final ExecutorService IO = Executors.newFixedThreadPool(3);
    static final AtomicInteger exports = new AtomicInteger();
    private static volatile String baseUrl;
    private static Future<String> startup;
    static synchronized Future<String> start(Context context) {
        if (startup != null) return startup;
        Context app = context.getApplicationContext();
        startup = IO.submit(() -> {
            File runtime = new File(app.getFilesDir(), "runtime");
            File marker = new File(runtime, "build-id.txt");
            String version;
            try (InputStream input=app.getAssets().open("runtime/build-id.txt");ByteArrayOutputStream bytes=new ByteArrayOutputStream()){transfer(input,bytes);version=bytes.toString("UTF-8");}
            String previous = "";
            if (marker.isFile()) try (InputStream input = new FileInputStream(marker); ByteArrayOutputStream bytes = new ByteArrayOutputStream()) { transfer(input,bytes); previous=bytes.toString("UTF-8"); }
            if (!version.equals(previous)) {
                copyAsset(app.getAssets(), "runtime", runtime);
                try (FileOutputStream out = new FileOutputStream(marker)) { out.write(version.getBytes(StandardCharsets.UTF_8)); }
            }
            if (!Python.isStarted()) Python.start(new AndroidPlatform(app));
            baseUrl = Python.getInstance().getModule("android_runtime").callAttr("start", runtime.getAbsolutePath(), home(app).getAbsolutePath()).toString();
            return baseUrl;
        });
        return startup;
    }
    static File home(Context context) { return new File(context.getFilesDir(), "bookpack"); }
    static String baseUrl() { return baseUrl; }
    private static void copyAsset(AssetManager assets, String source, File target) throws IOException {
        String[] children = assets.list(source);
        if (children != null && children.length > 0) {
            if (!target.isDirectory() && !target.mkdirs()) throw new IOException("Cannot create " + target);
            for (String child : children) copyAsset(assets, source + "/" + child, new File(target, child));
        } else {
            File parent = target.getParentFile(); if (!parent.isDirectory() && !parent.mkdirs()) throw new IOException("Cannot create " + parent);
            try (InputStream in = assets.open(source); OutputStream out = new FileOutputStream(target)) { transfer(in, out); }
        }
    }
    static void transfer(InputStream in, OutputStream out) throws IOException { byte[] buffer = new byte[128 * 1024]; int count; while ((count = in.read(buffer)) != -1) out.write(buffer, 0, count); }
}
