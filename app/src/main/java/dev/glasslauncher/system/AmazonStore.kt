package dev.glasslauncher.system

import android.content.Context
import android.content.Intent

/**
 * The Amazon Appstore on Fire OS 8 has no screen of its own: its launcher entry sends Home a request
 * (`navigate_node=l_apps`) that only Amazon's launcher understands, and with that launcher disabled the
 * request lands on Glass and the store never appears. With root, Glass turns Amazon's launcher on just
 * long enough to show the store, and off again as soon as Home is pressed (see watchForHome;
 * Fire OS makes its launcher Home whenever it's enabled, so the press would otherwise stay there).
 */
object AmazonStore {
    const val PACKAGE = "com.amazon.venezia"
    private const val STOCK = "com.amazon.tv.launcher"
    private const val PREFS = "amazon-store"

    /** A Home intent asking for one of the stock launcher's pages (what the Appstore sends). */
    fun isStoreRequest(intent: Intent?): Boolean = intent?.getStringExtra("navigate_node") != null

    /** Only the Appstore (by the system's referrer, not one the intent supplies) may open it through Home. */
    internal fun trustedCaller(referrerHost: String?, referrerSupplied: Boolean): Boolean =
        !referrerSupplied && referrerHost == PACKAGE

    /**
     * Extras reach this from any app's intent (MainActivity is exported for HOME) and go into a root shell line, so only
     * plain identifiers pass; anything else falls back to the default instead of being quoted into the script.
     */
    internal fun safeExtra(value: String?, default: String): String =
        value?.takeIf { it.length in 1..64 && it.all { c -> c.isLetterOrDigit() || c == '_' || c == '.' || c == '-' } } ?: default

    suspend fun open(context: Context, node: String = "l_apps", source: String = "appstore"): Boolean {
        if (RuntimeSafety.isolated) return false
        if (!Root.available()) return false
        val node = safeExtra(node, "l_apps")
        val source = safeExtra(source, "appstore")
        context.getSharedPreferences(PREFS, 0).edit().putBoolean("open", true).apply()
        // P300: no longer turns Amazon's launcher on. Enabling it and starting its home put the *stock Fire OS home* on
        // screen, not the store, and Fire OS then made that launcher Home (so the press stayed there). The Appstore's own
        // ADE home opens the store directly with the launcher left disabled, so the launcher stays off throughout.
        val ok = Root.action(context, "Open Appstore", """
            am start -n $PACKAGE/.ade.ADEHomeActivity -a android.intent.action.MAIN -c android.intent.category.LAUNCHER --es navigate_node '$node' --es source '$source'
        """.trimIndent()).ok
        if (ok) watchForHome(context.applicationContext)
        return ok
    }

    @Volatile private var watcher: Process? = null

    /**
     * Home presses never reach apps or accessibility services on Fire OS (its key policy takes them, and
     * its HOME_PRESSED broadcast goes to two Amazon packages only), but every one logs an activity start
     * with the HOME category. Followed as root, from now on, while the store is open.
     */
    private fun watchForHome(context: Context) {
        if (RuntimeSafety.isolated) return
        watcher?.destroy()
        // Every Home press logs wm_new_intent (pressed in Amazon's launcher) or wm_set_resumed_activity on
        // it (pressed in an app opened from the store). Epoch times keep out anything logged before.
        val started = System.currentTimeMillis() / 1000.0
        // Its own su, but with the command written to stdin rather than passed with -c, which is the form the rest of
        // Root uses. logcat runs in the foreground of that shell, so the stream stays open until the watcher is
        // destroyed; closing stdin afterwards is harmless.
        val p = runCatching {
            ProcessBoundary.start(listOf("su", "--mount-master"))
        }.getOrNull() ?: return
        watcher = p
        runCatching {
            p.outputStream.bufferedWriter().use {
                it.write("logcat -T 1 -b events -v epoch wm_new_intent:I wm_set_resumed_activity:I '*:S'\n")
                it.flush()
            }
        }
        Thread {
            runCatching {
                p.inputStream.bufferedReader().useLines { lines ->
                    for (line in lines) {
                        if (!isOpen(context)) break
                        val at = line.trim().substringBefore(' ').toDoubleOrNull() ?: continue
                        if (at < started) continue
                        // The store's own opening can log a new intent in its first second (not always);
                        // after that, every one is a Home press.
                        if (at - started < 1.2) continue
                        val home = ("wm_new_intent" in line && STOCK in line && "android.intent.action.MAIN" in line) ||
                            // Home pressed in an app opened from the store brings Amazon's launcher back.
                            ("wm_set_resumed_activity" in line && STOCK in line && "resumeTopActivityInnerLocked" in line)
                        if (home) {
                            kotlinx.coroutines.runBlocking { close(context, bringHome = true) }
                            break
                        }
                    }
                }
            }
            p.destroy()
            if (watcher === p) watcher = null
        }.apply { isDaemon = true; name = "glass-store-home" }.start()
    }

    /** Puts Glass back as Home if a store visit left Amazon's launcher on. Cheap when nothing is pending. */
    suspend fun close(context: Context, bringHome: Boolean) {
        if (RuntimeSafety.isolated) return
        val prefs = context.getSharedPreferences(PREFS, 0)
        if (!prefs.getBoolean("open", false)) return
        prefs.edit().putBoolean("open", false).apply()
        watcher?.destroy(); watcher = null
        Root.action(context, "Close Appstore", buildString {
            // The launcher is normally already off; disabling is idempotent and covers a store visit that turned it on.
            appendLine("pm disable-user --user 0 $STOCK")
            appendLine("cmd package set-home-activity ${context.packageName}/dev.glasslauncher.MainActivity")
            if (bringHome) appendLine("am start -a android.intent.action.MAIN -c android.intent.category.HOME -n ${context.packageName}/dev.glasslauncher.MainActivity")
        })
    }

    fun isOpen(context: Context): Boolean = context.getSharedPreferences(PREFS, 0).getBoolean("open", false)
}
