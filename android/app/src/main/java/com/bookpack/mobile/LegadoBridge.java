package com.bookpack.mobile;

import android.app.Activity;
import android.content.ClipData;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.database.Cursor;
import android.net.Uri;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.File;
import java.io.FileInputStream;
import java.security.MessageDigest;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

/** File handoff to a separately installed Legado reader. */
final class LegadoBridge {
    private static final String ASSOCIATION = "io.legado.app.ui.association.FileAssociationActivity";
    private static final String READER = "io.legado.app.ui.book.read.ReadBookActivity";

    static final class Handoff {
        final Intent intent;
        final JSONObject result;
        Handoff(Intent intent, JSONObject result) { this.intent = intent; this.result = result; }
    }

    static JSONObject status(Context context) {
        JSONObject result = new JSONObject();
        JSONArray readers = new JSONArray();
        PackageManager manager = context.getPackageManager();
        Map<String, ResolveInfo> found = new LinkedHashMap<>();
        for (String mime : new String[]{"text/plain", "application/epub+zip"}) {
            Intent intent = new Intent(Intent.ACTION_VIEW)
                    .setDataAndType(Uri.parse("content://bookpack.local/sample.txt"), mime);
            for (ResolveInfo info : manager.queryIntentActivities(intent, PackageManager.MATCH_DEFAULT_ONLY)) {
                if (ASSOCIATION.equals(info.activityInfo.name)) found.put(info.activityInfo.packageName, info);
            }
        }
        try {
            for (String packageName : found.keySet()) {
                JSONObject reader = new JSONObject();
                reader.put("packageName", packageName);
                reader.put("version", manager.getPackageInfo(packageName, 0).versionName);
                readers.put(reader);
            }
            result.put("available", readers.length() > 0);
            result.put("readers", readers);
            if (readers.length() > 0) {
                JSONObject preferred = readers.getJSONObject(0);
                for (int index = 0; index < readers.length(); index++) {
                    String name = readers.getJSONObject(index).getString("packageName");
                    if ("com.legado.app.release".equals(name) || "io.legado.app.release".equals(name)) {
                        preferred = readers.getJSONObject(index);
                        break;
                    }
                }
                result.put("packageName", preferred.getString("packageName"));
                result.put("version", preferred.getString("version"));
            }
        } catch (Exception ignored) { }
        return result;
    }

    /** Runs on CoreRuntime.IO: file hashing and Legado's provider must not block the UI. */
    static Handoff prepare(Context context, JSONObject book) throws Exception {
        JSONObject reader = status(context);
        reader.put("id", book.getString("id"));
        if (!reader.optBoolean("available")) {
            return failure(reader, "not_installed", "这台手机还没有安装 Legado。");
        }
        String packageName = reader.getString("packageName");
        JSONArray books = bookshelf(context, packageName);
        if (books == null) {
            return failure(reader, "library_unavailable", "暂时无法读取 Legado 书架，请先打开 Legado 完成初始化后重试；如仍失败，请更新 Legado。");
        }
        String fingerprint = fingerprint(new File(book.getString("path")));
        long installed = context.getPackageManager().getPackageInfo(packageName, 0).firstInstallTime;
        SharedPreferences links = context.getSharedPreferences("legado_books", Context.MODE_PRIVATE);
        String key = packageName + ":" + book.getString("id");
        JSONObject link;
        try { link = new JSONObject(links.getString(key, "{}")); }
        catch (Exception ignored) { link = new JSONObject(); }
        Set<String> current = urls(books);
        String bookUrl = "";
        if (fingerprint.equals(link.optString("fingerprint")) && installed == link.optLong("installed")) {
            String saved = link.optString("bookUrl");
            if (!saved.isEmpty() && current.contains(saved)) bookUrl = saved;
            // The first import is completed by Legado, outside our process. Resolve its
            // new row on the next open; persist the snapshot before leaving the app so
            // this also works when Android kills Bookpack during the first reading.
            JSONArray before = link.optJSONArray("before");
            if (bookUrl.isEmpty() && before != null) {
                Set<String> previous = new HashSet<>();
                for (int index = 0; index < before.length(); index++) previous.add(before.getString(index));
                int matches = 0;
                for (int index = 0; index < books.length(); index++) {
                    JSONObject item = books.getJSONObject(index);
                    String url = item.optString("bookUrl");
                    if (!previous.contains(url) && "loc_book".equals(item.optString("origin"))
                            && sameFilename(book.getString("filename"), item.optString("originName"))) {
                        bookUrl = url;
                        matches++;
                    }
                }
                if (matches > 1) return failure(reader, "ambiguous_import", "Legado 中出现了多个同名新副本，请先从 Legado 书架打开这本书。");
                if (matches == 0 && System.currentTimeMillis() - link.optLong("started") < 10000) {
                    return failure(reader, "import_pending", "Legado 正在接收这本书，请稍后再试。");
                }
            }
        }
        JSONObject saved = new JSONObject().put("fingerprint", fingerprint).put("installed", installed);
        Intent intent;
        if (!bookUrl.isEmpty()) {
            // Legado ReadBookViewModel.initData accepts bookUrl and restores its own
            // saved chapter/page. Do not pass index/chapterPos or import the file again.
            saved.put("bookUrl", bookUrl);
            intent = new Intent(Intent.ACTION_VIEW).setComponent(new ComponentName(packageName, READER))
                    .putExtra("bookUrl", bookUrl);
        } else {
            saved.put("before", new JSONArray(current)).put("started", System.currentTimeMillis());
            Uri uri = BookFileProvider.uri(context, book);
            intent = new Intent(Intent.ACTION_VIEW).setComponent(new ComponentName(packageName, ASSOCIATION))
                    .setDataAndType(uri, book.getString("mime"))
                    .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
            intent.setClipData(ClipData.newRawUri(book.getString("title"), uri));
        }
        if (!links.edit().putString(key, saved.toString()).commit()) {
            return failure(reader, "save_failed", "无法保存送书记录，请检查手机剩余空间后重试。");
        }
        return new Handoff(intent, reader.put("ok", true).put("resumed", !bookUrl.isEmpty())
                .put("title", book.getString("title")));
    }

    static JSONObject launch(Activity activity, Handoff handoff) {
        if (handoff.intent != null) activity.startActivity(handoff.intent);
        return handoff.result;
    }

    private static Handoff failure(JSONObject result, String reason, String message) throws Exception {
        return new Handoff(null, result.put("ok", false).put("reason", reason).put("message", message));
    }

    private static JSONArray bookshelf(Context context, String packageName) {
        Uri uri = Uri.parse("content://" + packageName + ".readerProvider/books/query");
        try (Cursor cursor = context.getContentResolver().query(uri, null, null, null, null)) {
            if (cursor == null || !cursor.moveToFirst()) return null;
            JSONObject response = new JSONObject(cursor.getString(cursor.getColumnIndexOrThrow("result")));
            return response.optBoolean("isSuccess") ? response.optJSONArray("data") : null;
        } catch (Exception error) {
            android.util.Log.w("Bookpack", "Legado bookshelf query", error);
            return null;
        }
    }

    private static Set<String> urls(JSONArray books) throws Exception {
        Set<String> result = new HashSet<>();
        for (int index = 0; index < books.length(); index++) result.add(books.getJSONObject(index).getString("bookUrl"));
        return result;
    }

    private static boolean sameFilename(String original, String imported) {
        if (original.equals(imported)) return true;
        int dot = original.lastIndexOf('.');
        return dot > 0 && imported.matches(Pattern.quote(original.substring(0, dot))
                + " \\(\\d+\\)" + Pattern.quote(original.substring(dot)));
    }

    private static String fingerprint(File file) throws Exception {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        byte[] buffer = new byte[65536];
        try (FileInputStream input = new FileInputStream(file)) {
            int count;
            while ((count = input.read(buffer)) != -1) digest.update(buffer, 0, count);
        }
        StringBuilder value = new StringBuilder(64);
        for (byte item : digest.digest()) value.append(String.format(java.util.Locale.ROOT, "%02x", item & 0xff));
        return value.toString();
    }
}
