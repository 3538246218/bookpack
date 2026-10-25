package com.bookpack.mobile;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;
import com.chaquo.python.Python;
import org.json.JSONObject;
import java.io.File;
import java.io.FileNotFoundException;
import java.util.concurrent.TimeUnit;

/** Exposes a bookshelf file through an Android read-only URI grant. */
public class BookFileProvider extends ContentProvider {
    static JSONObject source(Context context, String identity) throws Exception {
        if (identity == null || !identity.matches("[a-f0-9]{20}")) {
            throw new FileNotFoundException("请选择书架中的书籍。");
        }
        CoreRuntime.start(context).get(45, TimeUnit.SECONDS);
        return new JSONObject(Python.getInstance().getModule("android_runtime")
                .callAttr("reader_source", identity).toString());
    }

    static Uri uri(Context context, JSONObject book) throws Exception {
        return new Uri.Builder().scheme("content")
                .authority(context.getPackageName() + ".books")
                .appendPath(book.getString("id"))
                .appendPath(book.getString("filename")).build();
    }

    private JSONObject book(Uri uri) throws FileNotFoundException {
        try {
            if (uri.getPathSegments().size() != 2) throw new FileNotFoundException();
            return source(getContext(), uri.getPathSegments().get(0));
        } catch (Exception error) {
            throw new FileNotFoundException("书籍文件暂不可用，请回到书包重新打开。");
        }
    }

    @Override public boolean onCreate() { return true; }

    @Override public String getType(Uri uri) {
        String filename = uri.getLastPathSegment();
        return filename != null && filename.toLowerCase(java.util.Locale.ROOT).endsWith(".epub")
                ? "application/epub+zip" : "text/plain";
    }

    @Override public ParcelFileDescriptor openFile(Uri uri, String mode)
            throws FileNotFoundException {
        if (!"r".equals(mode)) throw new FileNotFoundException("书籍只供阅读。");
        try {
            return ParcelFileDescriptor.open(new File(book(uri).getString("path")),
                    ParcelFileDescriptor.MODE_READ_ONLY);
        } catch (org.json.JSONException error) {
            throw new FileNotFoundException("书籍文件信息暂不可用。");
        }
    }

    @Override public Cursor query(Uri uri, String[] projection, String selection,
                                  String[] arguments, String sortOrder) {
        try {
            JSONObject book = book(uri);
            String[] columns = projection != null ? projection
                    : new String[]{OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE};
            MatrixCursor result = new MatrixCursor(columns, 1);
            Object[] row = new Object[columns.length];
            for (int index = 0; index < columns.length; index++) {
                if (OpenableColumns.DISPLAY_NAME.equals(columns[index])) row[index] = book.getString("filename");
                if (OpenableColumns.SIZE.equals(columns[index])) row[index] = book.getLong("bytes");
            }
            result.addRow(row);
            return result;
        } catch (Exception error) { return null; }
    }

    @Override public Uri insert(Uri uri, ContentValues values) { throw new UnsupportedOperationException(); }
    @Override public int update(Uri uri, ContentValues values, String selection, String[] arguments) { return 0; }
    @Override public int delete(Uri uri, String selection, String[] arguments) { return 0; }
}
