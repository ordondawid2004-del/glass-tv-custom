package dev.glasslauncher.system

import android.content.Context
import android.content.pm.PackageManager
import android.provider.Settings
import dev.glasslauncher.app
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

/** Carries out [KeysServicePolicy]'s decision (P93). Called on every Home resume; "at boot" is a changed system boot counter, not a new process. */
object KeysService {
    private const val PREFS = "keysService"
    private const val SERVICE = "${dev.glasslauncher.BuildConfig.APPLICATION_ID}/dev.glasslauncher.system.RemoteKeysService"

    private val _card = MutableStateFlow(false)
    /** True when Home should show its one "turn on remote buttons" card (consumed with [cardShown]). */
    val card: StateFlow<Boolean> = _card

    fun cardShown() { _card.value = false }

    private val _homeCard = MutableStateFlow(false)
    /** True when Home should show its one "Make VITRE your Home" card (P95 gap 1); consumed with [homeCardShown]. */
    val homeCard: StateFlow<Boolean> = _homeCard

    fun homeCardShown() { _homeCard.value = false }

    /** The setting changed (Settings): turning it on again re-arms the enable attempt and the card. */
    fun onSettingChanged(context: Context, on: Boolean) {
        if (RuntimeSafety.isolated) return
        val p = context.getSharedPreferences(PREFS, 0).edit()
        if (on) p.remove("lastOnUpdate").putBoolean("cardShown", false)
        p.apply()
        if (!on) _card.value = false
    }

    private fun serviceOn(context: Context): Boolean {
        val list = Settings.Secure.getString(context.contentResolver, "enabled_accessibility_services").orEmpty()
        val on = Settings.Secure.getInt(context.contentResolver, "accessibility_enabled", 0) == 1
        return on && list.split(':').any { it == SERVICE }
    }

    suspend fun check(context: Context) {
        if (RuntimeSafety.isolated) return
        val app = context.applicationContext
        val prefs = app.getSharedPreferences(PREFS, 0)
        // A boot, not a process restart (LMK and crashes restart Glass too): the system's boot counter changed.
        val bootCount = Settings.Global.getInt(app.contentResolver, "boot_count", -1)
        val lastBoot = if (prefs.contains("bootCount")) prefs.getInt("bootCount", -1) else null
        val atBoot = KeysServicePolicy.isBoot(bootCount, lastBoot)
        if (bootCount >= 0 && bootCount != lastBoot) prefs.edit().putInt("bootCount", bootCount).apply()
        val settingOn = app.app.config.config.value.instantBack
        // lastUpdateTime, not versionCode: versionCode is hard-coded in build.gradle.kts and never changes between updates.
        val updateTime = runCatching { app.packageManager.getPackageInfo(app.packageName, 0).lastUpdateTime }.getOrDefault(0L)
        val on = serviceOn(app)
        val hasSecure = app.checkSelfPermission("android.permission.WRITE_SECURE_SETTINGS") == PackageManager.PERMISSION_GRANTED
        // su is only asked when it is needed (the service is off, the setting on and there is no secure-settings write).
        val hasRoot = settingOn && !on && !hasSecure && Root.available()
        homeSetup(app, prefs, hasSecure, atBoot, updateTime)
        val state = KeysServicePolicy.State(
            settingOn = settingOn, serviceOn = on, mode = KeysServicePolicy.mode(hasSecure, hasRoot), updateTime = updateTime,
            lastOnUpdate = if (prefs.contains("lastOnUpdate")) prefs.getLong("lastOnUpdate", 0L) else null,
            atBoot = atBoot, cardShown = prefs.getBoolean("cardShown", false),
            userUnbound = prefs.getLong("unboundAt", 0L) > updateTime,
        )
        val action = KeysServicePolicy.decide(state)
        android.util.Log.i("GlassKeys", "check: setting=$settingOn service=$on mode=${state.mode} boot=$atBoot update=$updateTime last=${state.lastOnUpdate} -> $action")
        when (action) {
            KeysServicePolicy.Action.Nothing -> Unit
            KeysServicePolicy.Action.RecordOn -> prefs.edit().putLong("lastOnUpdate", updateTime).remove("unboundAt").apply()
            KeysServicePolicy.Action.Enable -> {
                enable(app, state.mode)
                // Record this install as healed: a manual off right after the heal is the user's, not an update's.
                prefs.edit().putLong("lastOnUpdate", updateTime).remove("unboundAt").apply()
            }
            KeysServicePolicy.Action.UserTurnedOff -> {
                app.app.config.update { it.copy(instantBack = false) }
                RemoteNotice.show(app, "Remote buttons off. Turn on in VITRE Settings", 4_000)
            }
            KeysServicePolicy.Action.ShowCard -> {
                // Robolectric has no root and no secure write, so every UI test would open the card over Home.
                if (android.os.Build.FINGERPRINT == "robolectric") return
                prefs.edit().putBoolean("cardShown", true).apply()
                _card.value = true
            }
        }
    }

    /**
     * Becoming Home (P95 gap 1): re-enable the Home guard after a boot or an update when WRITE_SECURE_SETTINGS is
     * held (a manual off in Accessibility settings is respected: the setting flips off), otherwise offer the card
     * (once) that says how to make Glass Home.
     */
    private suspend fun homeSetup(app: Context, prefs: android.content.SharedPreferences, hasSecure: Boolean, atBoot: Boolean, updateTime: Long) {
        val cfg = app.app.config.config.value
        val guardOn = HomeSetup.isGuardEnabled(app)
        val last = if (prefs.contains("guardOnUpdate")) prefs.getLong("guardOnUpdate", 0L) else null
        when (HomeGuardPolicy.decide(cfg.homeGuard, guardOn, hasSecure, atBoot, updateTime, last)) {
            HomeGuardPolicy.Action.RecordOn -> prefs.edit().putLong("guardOnUpdate", updateTime).apply()
            HomeGuardPolicy.Action.Enable -> {
                HomeSetup.setGuardEnabled(app, true)
                prefs.edit().putLong("guardOnUpdate", updateTime).apply()
                return
            }
            HomeGuardPolicy.Action.UserTurnedOff -> {
                app.app.config.update { it.copy(homeGuard = false) }
                RemoteNotice.show(app, "Home Button Takeover off. Turn on in VITRE Settings", 4_000)
                return
            }
            HomeGuardPolicy.Action.Nothing -> Unit
        }
        val isHome = HomeSetup.isDefaultHome(app)
        if (isHome || guardOn || prefs.getBoolean("homeCardShown", false)) return
        if (!HomeCardPolicy.show(isHome, guardOn, Root.known || Root.available(), false)) return
        if (android.os.Build.FINGERPRINT == "robolectric") return
        prefs.edit().putBoolean("homeCardShown", true).apply()
        _homeCard.value = true
    }

    private suspend fun enable(context: Context, mode: WriteMode) {
        if (RuntimeSafety.isolated) return
        val current = Settings.Secure.getString(context.contentResolver, "enabled_accessibility_services")
        val wanted = RootFeatures.withService(current, SERVICE)
        when (mode) {
            WriteMode.SecureSettings -> runCatching {
                Settings.Secure.putString(context.contentResolver, "enabled_accessibility_services", wanted)
                Settings.Secure.putInt(context.contentResolver, "accessibility_enabled", 1)
            }
            WriteMode.Root -> Root.action(context, "Remote keys service was off: turned back on",
                "settings put secure enabled_accessibility_services ${dev.glasslauncher.apps.IconPackSources.shellQuote(wanted)}\nsettings put secure accessibility_enabled 1")
            WriteMode.None -> Unit
        }
    }
}
