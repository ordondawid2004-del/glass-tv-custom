package dev.glasslauncher.downloads

import dev.glasslauncher.system.RuntimeSafety

import android.content.Context
import android.content.Intent
import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import java.io.File

/** Process-wide entry point: the queue the UI observes and the service keeps alive. */
object Downloads {
    @Volatile private var queue: DownloadQueue? = null

    fun queue(context: Context): DownloadQueue = queue ?: synchronized(this) {
        queue ?: create(context.applicationContext).also { queue = it }
    }

    /** `Android/data/<pkg>/files/Downloads`: no permission needed, removed with Glass. */
    fun folder(context: Context): File =
        (if (RuntimeSafety.isolated) null else context.getExternalFilesDir("Downloads")) ?: File(context.filesDir, "Downloads").also { it.mkdirs() }

    /** Starts a download the way the manager does: the user's segment and concurrency settings, and the notification ask. */
    fun start(context: Context, d: dev.glasslauncher.data.DownloadsConfig, url: String, name: String?, sha256: String? = null, guardExec: Boolean = false, execOk: String = ""): String {
        val q = queue(context)
        if (q.items.value.none { it.state != DownloadState.Failed }) askNotifications(context)
        q.setSegments(d.segments)
        q.setConcurrency(d.concurrent)
        return q.add(url, name, sha256, guardExec = guardExec, execOk = execOk)
    }

    /** Set when the system refused a foreground start from the background; tried again once Glass is on screen. */
    @Volatile internal var retryInForeground = false

    /** API 33+: the progress notification needs the permission; ask once before the first download. Downloads run either way. */
    fun askNotifications(context: Context) {
        if (RuntimeSafety.isolated) return
        if (android.os.Build.VERSION.SDK_INT < 33) return
        if (context.checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS) == android.content.pm.PackageManager.PERMISSION_GRANTED) return
        var c: Context? = context
        while (c is android.content.ContextWrapper && c !is android.app.Activity) c = c.baseContext
        (c as? android.app.Activity)?.requestPermissions(arrayOf(android.Manifest.permission.POST_NOTIFICATIONS), 119)
    }

    /** Brings the service up for downloads still waiting after a process death or reboot. */
    fun resumePending(context: Context) {
        if (RuntimeSafety.isolated) return
        if (queue(context).items.value.any { it.state == DownloadState.Queued }) startService(context)
    }

    /** Glass is visible, so a foreground start is allowed: bring back the service that was refused earlier. */
    fun onForeground(context: Context) {
        if (RuntimeSafety.isolated) return
        if (!retryInForeground) return
        retryInForeground = false
        if (queue(context).items.value.any { it.state == DownloadState.Active || it.state == DownloadState.Queued }) startService(context)
    }

    internal fun startService(context: Context) {
        if (RuntimeSafety.isolated) return
        try {
            context.startForegroundService(Intent(context, DownloadService::class.java))
        } catch (e: Exception) {
            // API 31+ throws here from the background. The queue keeps going in the process; retry when Glass is shown.
            Log.w("Downloads", "download service did not start", e)
            retryInForeground = true
        }
    }

    private fun create(app: Context): DownloadQueue {
        if (RuntimeSafety.isolated) throw IllegalStateException(RuntimeSafety.reason)
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
        return DownloadQueue(scope, DownloadEngine(), folder(app), File(app.filesDir, "downloads/queue.json")) { startService(app) }
            .also { it.load() }
    }
}
