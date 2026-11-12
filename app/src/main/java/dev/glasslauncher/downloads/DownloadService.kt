package dev.glasslauncher.downloads

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.util.Log
import dev.glasslauncher.R
import dev.glasslauncher.system.RuntimeSafety
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.FlowPreview
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.sample
import kotlinx.coroutines.launch

/** Keeps the process alive while downloads run and shows one summary notification. */
@OptIn(FlowPreview::class)
class DownloadService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private var watching: Job? = null
    private var lastStartId = 0

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        if (RuntimeSafety.isolated) return
        getSystemService(NotificationManager::class.java)
            .createNotificationChannel(NotificationChannel(CHANNEL, getString(R.string.downloads_channel), NotificationManager.IMPORTANCE_LOW))
    }

    // Lint checks all shared sources against the variant manifest. M1 removes this service and
    // POST_NOTIFICATIONS and rejects entry below; original declares it and still checks the grant.
    @android.annotation.SuppressLint("NotificationPermission")
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (RuntimeSafety.isolated) return START_NOT_STICKY
        lastStartId = startId
        // Foreground first: loading the queue reads a file, and the system gives a few seconds to call startForeground.
        if (!enterForeground(notification(emptyList()))) {
            stopSelf(startId)
            return START_NOT_STICKY
        }
        val queue = Downloads.queue(this)
        queue.startPending()
        if (watching == null) watching = scope.launch {
            queue.items.sample(1000).collect { items ->
                if (items.none { it.state == DownloadState.Active || it.state == DownloadState.Queued }) {
                    stopForeground(STOP_FOREGROUND_REMOVE)
                    stopSelf(lastStartId) // not stopSelf(): a start that arrived meanwhile keeps the service
                } else {
                    if (Build.VERSION.SDK_INT < 33 || checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS) == android.content.pm.PackageManager.PERMISSION_GRANTED) {
                        getSystemService(NotificationManager::class.java).notify(ID, notification(items))
                    }
                }
            }
        }
        return START_STICKY // killed mid-download: restart and continue from the sidecars
    }

    // Android 15 ends dataSync services after six hours; pause cleanly instead of being crashed.
    override fun onTimeout(startId: Int, fgsType: Int) {
        if (RuntimeSafety.isolated) return
        Downloads.queue(this).pauseAll()
        stopSelf(startId)
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    /** False when the system refuses (API 31+ throws for a start from the background); the downloads then run without it. */
    private fun enterForeground(n: Notification): Boolean = try {
        if (Build.VERSION.SDK_INT >= 29) startForeground(ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        else startForeground(ID, n)
        true
    } catch (e: Exception) {
        Log.w("Downloads", "no foreground service from the background", e)
        Downloads.retryInForeground = true
        false
    }

    private fun notification(items: List<DownloadItem>): Notification {
        val running = items.filter { it.state == DownloadState.Active }
        val waiting = items.count { it.state == DownloadState.Queued }
        val known = running.filter { it.size > 0 }
        val total = known.sumOf { it.size }
        val percent = if (total > 0) (known.sumOf { it.downloaded } * 100 / total).toInt() else 0
        val title = resources.getQuantityString(R.plurals.downloads_active, running.size, running.size)
        val text = if (waiting > 0) getString(R.string.downloads_waiting, waiting) else "$percent%"
        return Notification.Builder(this, CHANNEL)
            .setSmallIcon(android.R.drawable.stat_sys_download)
            .setContentTitle(title)
            .setContentText(text)
            .setProgress(100, percent, total <= 0)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .build()
    }

    private companion object {
        const val CHANNEL = "downloads"
        const val ID = 119
    }
}
