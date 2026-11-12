package dev.glasslauncher.system

import android.app.ActivityManager
import android.app.AppOpsManager
import android.app.usage.UsageEvents
import android.app.usage.UsageStatsManager
import android.content.Context
import android.os.Process
import dev.glasslauncher.apps.AppEntry

/** Recently used apps for the app switcher, most recent first. */
object RecentApps {

    /** Usage access lets the switcher include apps opened from anywhere (adb shell appops set <pkg> GET_USAGE_STATS allow). */
    fun hasUsageAccess(context: Context): Boolean {
        val ops = context.getSystemService(AppOpsManager::class.java) ?: return false
        @Suppress("DEPRECATION")
        return ops.checkOpNoThrow(AppOpsManager.OPSTR_GET_USAGE_STATS, Process.myUid(), context.packageName) == AppOpsManager.MODE_ALLOWED
    }

    /** A real recent task: [taskId] is what [resume] needs to bring it to the front with its state intact. */
    data class RecentTask(val packageName: String, val taskId: Int, val lastActiveMs: Long = 0L)

    /** [all] without Glass's own tasks: an unprivileged getRecentTasks returns only those, which must not count as "can see tasks". */
    fun others(all: List<RecentTask>, own: String): List<RecentTask> = all.filter { it.packageName != own }

    /**
     * Other apps' real tasks, most recent first. `getRecentTasks` is a deprecated public API whose behaviour
     * changed under it: since API 21 it returns only the caller's tasks unless the caller is privileged, in which
     * case it returns everyone's (I08 spec). Glass is a priv-app via the glass-system Magisk module. Empty (never
     * a throw) wherever that isn't true, so callers fall back to usage history.
     */
    fun tasks(context: Context, maxNum: Int = 50): List<RecentTask> = runCatching {
        val am = context.getSystemService(ActivityManager::class.java) ?: return@runCatching emptyList()
        @Suppress("DEPRECATION")
        others(
            am.getRecentTasks(maxNum, 0).mapNotNull { info -> info.baseIntent?.component?.packageName?.let { RecentTask(it, info.id, lastActive(info)) } },
            context.packageName,
        )
    }.getOrDefault(emptyList())

    /** When the task was last in front (wall-clock ms, like the switcher's close times), from the stack's own record; 0 when this Android does not tell. */
    private fun lastActive(info: Any): Long = runCatching {
        val sinceBoot = generateSequence(info.javaClass as Class<*>?) { it.superclass }.firstNotNullOf { c -> runCatching { c.getDeclaredField("lastActiveTime") }.getOrNull() }
            .apply { isAccessible = true }.getLong(info)
        wallTime(sinceBoot, System.currentTimeMillis(), android.os.SystemClock.elapsedRealtime())
    }.getOrDefault(0L)

    /**
     * P220: TaskInfo.lastActiveTime counts from boot (elapsedRealtime), but a close is stamped with the wall clock, so every task
     * looked older than any close and a closed app never came back to the switcher, even after it was opened again.
     */
    fun wallTime(sinceBootMs: Long, nowWallMs: Long, nowSinceBootMs: Long): Long =
        if (sinceBootMs <= 0L) 0L else nowWallMs - (nowSinceBootMs - sinceBootMs)

    /**
     * The task stack as "when it was last used" pairs for [order]. The real time when the system gives one. Without it every task used
     * to be stamped "now", which is later than any close, so a closed app came straight back (P189): the time is 0 then, and a task
     * is hidden once it has been closed, until the app is launched again (which clears the close).
     */
    fun usageFromTasks(tasks: List<RecentTask>): List<Pair<String, Long>> = tasks.map { it.packageName to it.lastActiveMs }

    /**
     * Removes [pkg]'s tasks from the recents (privileged builds only: ActivityManager.removeTask is a hidden call that needs the
     * REMOVE_TASKS permission, which the glass-system module can grant), so a closed app really leaves the stack.
     */
    fun removeTasks(context: Context, pkg: String) {
        if (RuntimeSafety.isolated) return
        val am = context.getSystemService(ActivityManager::class.java) ?: return
        tasks(context).filter { it.packageName == pkg }.forEach { t ->
            runCatching { ActivityManager::class.java.getMethod("removeTask", Int::class.javaPrimitiveType).invoke(am, t.taskId) }
        }
    }

    /**
     * Brings [pkg]'s live task to the front with its state intact, instead of relaunching it fresh. The id is looked
     * up now, from a fresh task list: moveTaskToFront doesn't throw for a dead id, so a stale one would swallow the
     * tap. False when there is no such task (evicted, never opened, or this build isn't privileged): the caller then
     * launches normally, which is the APK-mode behaviour this never regresses.
     */
    fun resume(context: Context, pkg: String, options: android.os.Bundle? = null): Boolean = runCatching {
        if (RuntimeSafety.isolated) return false
        val task = tasks(context).firstOrNull { it.packageName == pkg } ?: return@runCatching false
        val am = context.getSystemService(ActivityManager::class.java) ?: return@runCatching false
        @Suppress("DEPRECATION")
        am.moveTaskToFront(task.taskId, 0, options)
        true
    }.getOrDefault(false)

    /**
     * Apps brought to the front in the last [days] days per the system's usage history (Fire OS only
     * keeps it since the last restart), followed by launches Glass recorded itself ([own]). Only apps
     * on Home are returned, so system screens and launchers never show up. An app in [closed] (closed from the
     * switcher, with when) stays out until it comes to the foreground again after that.
     *
     * Prefers the real task stack ([tasks]) when this build can see other apps' tasks: no manual `adb appops`
     * grant needed. Falls back to usage history otherwise.
     */
    fun list(
        context: Context, installed: List<AppEntry>, own: List<String>, closed: Map<String, Long> = emptyMap(),
        limit: Int = 12, days: Int = 3,
    ): List<AppEntry> {
        val byPackage = installed.associateBy { it.packageName }
        val privileged = tasks(context)
        val usage = if (privileged.isNotEmpty()) {
            usageFromTasks(privileged)
        } else {
            if (hasUsageAccess(context)) fromUsage(context, days) else emptyList()
        }
        return order(usage, own, closed).mapNotNull { byPackage[it] }.filter { it.packageName != context.packageName }.take(limit)
    }

    /**
     * The switcher's order, newest first: [usage] (package to when it last came to the foreground) then [own]
     * (launched through Glass, no time). Closed apps are left out unless the system saw them in the foreground
     * after they were closed; an app of [own] has no foreground time, and is dropped from [own] when closed.
     */
    fun order(usage: List<Pair<String, Long>>, own: List<String>, closed: Map<String, Long>): List<String> {
        val fromUsage = usage.sortedByDescending { it.second }.filter { (pkg, at) -> at > (closed[pkg] ?: Long.MIN_VALUE) }.map { it.first }
        val recentOwn = own.filter { pkg -> pkg !in closed || usage.any { it.first == pkg && it.second > closed.getValue(pkg) } }
        return (fromUsage + recentOwn).distinct()
    }

    private fun fromUsage(context: Context, days: Int): List<Pair<String, Long>> {
        val usm = context.getSystemService(UsageStatsManager::class.java) ?: return emptyList()
        val end = System.currentTimeMillis()
        val events = usm.queryEvents(end - days * 86_400_000L, end) ?: return emptyList()
        val last = HashMap<String, Long>()
        val e = UsageEvents.Event()
        while (events.hasNextEvent()) {
            events.getNextEvent(e)
            @Suppress("DEPRECATION")
            if (e.eventType == UsageEvents.Event.MOVE_TO_FOREGROUND) last[e.packageName] = e.timeStamp
        }
        return last.entries.map { it.key to it.value }
    }

    /**
     * Really stops [pkg] (not just its background processes, which the system starts again at once): as a
     * privileged app through ActivityManager.forceStopPackage, else as root (`am force-stop`). Returns whether
     * something worked.
     */
    suspend fun stop(context: Context, pkg: String): Boolean {
        if (RuntimeSafety.isolated) return false
        runCatching { context.getSystemService(ActivityManager::class.java)?.killBackgroundProcesses(pkg) }
        removeTasks(context, pkg)
        val privileged = runCatching {
            val am = context.getSystemService(ActivityManager::class.java)
            ActivityManager::class.java.getMethod("forceStopPackage", String::class.java).invoke(am, pkg)
            true
        }.getOrDefault(false)
        if (privileged) return true
        // A "no root" answer cached before the grant must not stick: look again when something needs it.
        if (!Root.available() && !Root.recheck()) return false
        // A package name only (system-provided today); anything else never reaches the shell.
        if (!pkg.matches(Regex("[A-Za-z0-9_.]{1,255}"))) return false
        return Root.action(context, "Close $pkg", "am force-stop $pkg").ok
    }
}
