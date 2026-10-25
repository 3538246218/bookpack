package com.bookpack.mobile;

import android.app.Activity;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import io.legado.app.bookpack.EmbeddedReader;
import org.json.JSONObject;

/** Opens the source-built reader inside this APK; no other application is needed. */
final class LegadoBridge {
    static final class Handoff {
        final Intent intent;
        final JSONObject result;
        Handoff(Intent intent, JSONObject result) { this.intent = intent; this.result = result; }
    }
    static JSONObject status(Context context) { return EmbeddedReader.status(context); }
    static Handoff prepare(Context context, JSONObject book) throws Exception {
        JSONObject result = EmbeddedReader.prepare(context, book);
        Intent intent = new Intent(Intent.ACTION_VIEW)
                .setComponent(new ComponentName(context.getPackageName(), "io.legado.app.ui.book.read.ReadBookActivity"))
                .addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT)
                .putExtra("bookUrl", result.getString("bookUrl"));
        return new Handoff(intent, result);
    }
    static JSONObject launch(Activity activity, Handoff handoff) {
        activity.startActivity(handoff.intent);
        return handoff.result;
    }
}
