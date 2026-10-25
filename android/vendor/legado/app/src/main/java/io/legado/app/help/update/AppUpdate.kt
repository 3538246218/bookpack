package io.legado.app.help.update

import io.legado.app.help.coroutine.Coroutine
import kotlinx.coroutines.CoroutineScope

object AppUpdate {

    val gitHubUpdate: AppUpdateInterface by lazy {
        object : AppUpdateInterface {
            override fun check(scope: CoroutineScope): Coroutine<UpdateInfo> = embeddedUpdate(scope)
        }
    }

    fun checkBeta(scope: CoroutineScope): Coroutine<UpdateInfo> =
        embeddedUpdate(scope)

    // Bookpack integration, 2026-10-25: never install a different application's APK.
    private fun embeddedUpdate(scope: CoroutineScope): Coroutine<UpdateInfo> =
        Coroutine.async(scope) {
            throw io.legado.app.exception.NoStackTraceException("当前为书包融合版，请从书包项目页面获取更新。")
        }

    data class UpdateInfo(
        val tagName: String,
        val updateLog: String,
        val downloadUrl: String,
        val fileName: String,
        val backupDownloadUrl: String? = null,
        val mirrorDownloadUrl: String? = null,
        val alternateMirrorDownloadUrl: String? = null,
        val size: Long = 0L,
        val createdAt: Long = 0L,
        val isBeta: Boolean = false
    )

    interface AppUpdateInterface {

        fun check(scope: CoroutineScope): Coroutine<UpdateInfo>

    }

}
