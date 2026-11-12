package dev.glasslauncher.apps

import dev.glasslauncher.system.RuntimeSafety

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.LauncherApps
import android.content.pm.PackageManager
import android.os.Handler
import android.os.Looper
import android.os.UserHandle
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.flow.conflate
import kotlinx.coroutines.flow.flowOn
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.onStart

@androidx.compose.runtime.Immutable
data class AppEntry(
    val packageName: String,
    val label: String,
    val component: ComponentName,
    /**
     * When the package was last installed or updated (PackageInfo.lastUpdateTime; 0 if unknown). It is part of
     * the entry, so part of every tile-art cache key ([TileSpec]): an updated app misses the cache and its tile
     * is drawn again from its new banner or icon instead of keeping the old art until the process dies.
     */
    val updated: Long = 0L,
)

class AppRepository(private val context: Context) {

    private val pm: PackageManager = context.packageManager

    fun apps(): Flow<List<AppEntry>> = packageChanges()
        .onStart { emit(Unit) }
        .conflate()
        .map { queryApps() }
        .flowOn(Dispatchers.IO)

    fun launchIntent(app: AppEntry): Intent? {
        // P300: Fire OS packages whose own launcher entry is a trampoline back into Amazon's launcher. The Appstore's
        // CATEGORY_LAUNCHER activity (`.grid.AppsGridLauncherActivity`) asks Home for Amazon's app grid and, with that
        // launcher disabled, lands on the *stock Fire OS home* instead of the store. Its ADE home opens the store
        // directly, so it is used in place of the launcher entry.
        LAUNCH_OVERRIDES[app.packageName]?.takeUnless { RuntimeSafety.isolated }?.let { cls ->
            val explicit = Intent(Intent.ACTION_MAIN).setComponent(ComponentName(app.packageName, cls))
            if (pm.resolveActivity(explicit, 0) != null) return explicit.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        return (pm.getLeanbackLaunchIntentForPackage(app.packageName)
            ?: pm.getLaunchIntentForPackage(app.packageName))
            ?.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    }

    companion object {
        /** Packages whose launcher entry is the wrong door (see [launchIntent]). */
        private val LAUNCH_OVERRIDES = mapOf(
            "com.amazon.venezia" to "com.amazon.venezia.ade.ADEHomeActivity",
        )
    }

    private fun queryApps(): List<AppEntry> {
        val byPackage = LinkedHashMap<String, AppEntry>()
        // One call for every package's last update time, not one binder call per app.
        val updatedAt = runCatching { pm.getInstalledPackages(0).associate { it.packageName to it.lastUpdateTime } }.getOrDefault(emptyMap())
        // Leanback entries win: they are the TV-specific activity with a banner.
        for (category in listOf(Intent.CATEGORY_LEANBACK_LAUNCHER, Intent.CATEGORY_LAUNCHER)) {
            val intent = Intent(Intent.ACTION_MAIN).addCategory(category)
            for (info in pm.queryIntentActivities(intent, 0)) {
                val ai = info.activityInfo
                if (ai.packageName == context.packageName || ai.packageName in byPackage) continue
                byPackage[ai.packageName] = AppEntry(
                    packageName = ai.packageName,
                    label = info.loadLabel(pm).toString().trim(),
                    component = ComponentName(ai.packageName, ai.name),
                    updated = updatedAt[ai.packageName] ?: runCatching { pm.getPackageInfo(ai.packageName, 0).lastUpdateTime }.getOrDefault(0L),
                )
            }
        }
        return byPackage.values.sortedBy { it.label.lowercase() }
    }

    private fun packageChanges(): Flow<Unit> = callbackFlow {
        val launcherApps = context.getSystemService(LauncherApps::class.java)
        val callback = object : LauncherApps.Callback() {
            override fun onPackageRemoved(packageName: String, user: UserHandle) { trySend(Unit) }
            override fun onPackageAdded(packageName: String, user: UserHandle) { trySend(Unit) }
            override fun onPackageChanged(packageName: String, user: UserHandle) { trySend(Unit) }
            override fun onPackagesAvailable(p: Array<out String>, user: UserHandle, replacing: Boolean) { trySend(Unit) }
            override fun onPackagesUnavailable(p: Array<out String>, user: UserHandle, replacing: Boolean) { trySend(Unit) }
        }
        launcherApps.registerCallback(callback, Handler(Looper.getMainLooper()))
        awaitClose { launcherApps.unregisterCallback(callback) }
    }
}
