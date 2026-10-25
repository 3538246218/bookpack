/* Bookpack integration, 2026-10-25. GPL-3.0; see android/COPYING. */
package io.legado.app.bookpack

import android.content.Context
import android.net.Uri
import io.legado.app.data.appDb
import io.legado.app.model.localBook.LocalBook
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.security.MessageDigest

/** Called only on Bookpack's I/O executor. Both screens share one Android UID. */
object EmbeddedReader {
    const val VERSION = "3.26101819"

    @JvmStatic fun status(context: Context): JSONObject = JSONObject()
        .put("available", true).put("embedded", true)
        .put("packageName", context.packageName).put("version", VERSION)

    @JvmStatic fun prepare(context: Context, source: JSONObject): JSONObject {
        val identity = source.getString("id")
        require(identity.matches(Regex("[a-f0-9]{20}"))) { "请选择书架中的书籍。" }
        val original = File(source.getString("path"))
        require(original.isFile && original.length() > 0) { "书籍文件暂不可用。" }
        val directory = File(context.filesDir, "reader/books/$identity")
        check(directory.isDirectory || directory.mkdirs()) { "无法创建阅读书库。" }
        val target = File(directory, original.name).canonicalFile
        val uri = Uri.fromFile(target)
        val url = target.path
        val existing = appDb.bookDao.getBook(url)
        val hash = digest(original)
        val preferences = context.getSharedPreferences("embedded_reader_books", Context.MODE_PRIVATE)
        val resumed = existing != null && target.isFile &&
            (preferences.getString(identity, null) == hash || digest(target) == hash)
        val book = if (resumed) existing!! else {
            val temporary = File(directory, original.name + ".incoming")
            try {
                original.inputStream().use { input ->
                    FileOutputStream(temporary).use { output -> input.copyTo(output); output.fd.sync() }
                }
                check(temporary.renameTo(target)) { "无法保存阅读副本，请检查剩余空间。" }
            } finally { temporary.delete() }
            val preview = LocalBook.previewImportFile(uri)
            source.optString("title").takeIf { it.isNotBlank() }?.let { preview.name = it }
            source.optString("author").takeIf { it.isNotBlank() }?.let { preview.author = it }
            LocalBook.importFile(uri, preview)
        }
        check(preferences.edit().putString(identity, hash).commit()) { "无法保存阅读记录。" }
        return status(context).put("ok", true).put("id", identity)
            .put("title", book.name).put("bookUrl", book.bookUrl).put("resumed", resumed)
    }

    private fun digest(file: File): String {
        val hash = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buffer = ByteArray(65536)
            while (true) { val count = input.read(buffer); if (count < 0) break; hash.update(buffer, 0, count) }
        }
        return hash.digest().joinToString("") { "%02x".format(it.toInt() and 255) }
    }
}
