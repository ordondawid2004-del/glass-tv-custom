package dev.glasslauncher.system

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.wifi.WifiManager
import android.provider.Settings
import kotlinx.coroutines.launch

/** What Control Center reads from the TV, and the system pages its tiles open. */
object SystemControls {

    /** The Wi-Fi network name, "Ethernet", "Not Connected", or "Connected" when Android hides the SSID. */
    fun network(context: Context): String {
        val cm = context.getSystemService(ConnectivityManager::class.java)
        val caps = cm?.getNetworkCapabilities(cm.activeNetwork) ?: return "Not Connected"
        if (caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET)) return "Ethernet"
        if (!caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)) return "Connected"
        // SSIDs need location access (grant once: adb shell pm grant <pkg> android.permission.ACCESS_FINE_LOCATION).
        if (context.checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) != PackageManager.PERMISSION_GRANTED) return "Connected"
        @Suppress("DEPRECATION")
        val ssid = context.applicationContext.getSystemService(WifiManager::class.java)?.connectionInfo?.ssid
        // Without permission Android reports "<unknown ssid>".
        return ssid?.removeSurrounding("\"")?.takeIf { it.isNotBlank() && !it.startsWith("<") } ?: "Connected"
    }

    fun wifiConnected(context: Context): Boolean {
        val cm = context.getSystemService(ConnectivityManager::class.java)
        return cm?.getNetworkCapabilities(cm.activeNetwork)?.let {
            it.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) || it.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET)
        } == true
    }

    /** Bluetooth on/off; null when the TV has no adapter or won't say. */
    @Suppress("DEPRECATION", "MissingPermission")
    fun bluetoothOn(): Boolean? = runCatching { android.bluetooth.BluetoothAdapter.getDefaultAdapter()?.isEnabled }.getOrNull()

    fun open(context: Context, action: String): Boolean = runCatching {
        if (RuntimeSafety.isolated) return false
        context.startActivity(Intent(action).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        true
    }.getOrDefault(false)

    /** The standard Bluetooth page, else Fire TV's "Controllers & Bluetooth Devices". */
    fun openBluetooth(context: Context): Boolean =
        open(context, Settings.ACTION_BLUETOOTH_SETTINGS) || open(context, "com.amazon.device.settings.action.CONTROLLERS")

    /** A top-level section of the TV's settings: a Fire TV screen, or a standard Android action elsewhere. */
    data class TvSettingsSection(val title: String, val fireActivity: String, val fallbackAction: String)

    /**
     * Fire TV's settings app has no main screen of its own (the stock home screen draws that list, and
     * Android's ACTION_SETTINGS lands on a do-nothing compliance stub), so Glass lists the sections.
     */
    val tvSettingsSections = listOf(
        TvSettingsSection("Network", "tv.network.NetworkActivity", Settings.ACTION_WIFI_SETTINGS),
        TvSettingsSection("Display & Sounds", "tv.display_sounds.DisplayAndSoundsActivity", Settings.ACTION_DISPLAY_SETTINGS),
        TvSettingsSection("Applications", "tv.applications.ApplicationsActivity", Settings.ACTION_APPLICATION_SETTINGS),
        TvSettingsSection("Controllers & Bluetooth Devices", "tv.controllers_bluetooth_devices.ControllersAndBluetoothActivity", Settings.ACTION_BLUETOOTH_SETTINGS),
        TvSettingsSection("Preferences", "tv.preferences.PreferencesActivity", Settings.ACTION_LOCALE_SETTINGS),
        TvSettingsSection("My Fire TV", "tv.device.DeviceActivity", Settings.ACTION_DEVICE_INFO_SETTINGS),
        TvSettingsSection("Accessibility", "tv.accessibility.AccessibilityActivity", Settings.ACTION_ACCESSIBILITY_SETTINGS),
        TvSettingsSection("Account & Profile Settings", "tv.my_account.MyAccountActivity", Settings.ACTION_SYNC_SETTINGS),
    )

    /**
     * Fire's own page for [section]. Display & Sounds, Preferences and Account & Profile are guarded by
     * com.amazon.tv.permission.LAUNCHER_SETTINGS (signature|privileged, held by Amazon's launcher), so
     * with root they open through `am start` as root; without it, the closest Android settings page.
     * (Requesting the permission instead would need it in glass-system's privapp allowlist, and Fire OS
     * won't boot with a privileged permission missing from it.)
     */
    fun openTvSettings(context: Context, section: TvSettingsSection): Boolean {
        if (RuntimeSafety.isolated) return false
        val component = "$FIRE_SETTINGS/.${section.fireActivity}"
        val opened = runCatching {
            context.startActivity(
                Intent().setClassName(FIRE_SETTINGS, "$FIRE_SETTINGS.${section.fireActivity}")
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP),
            )
        }.isSuccess
        if (opened) return true
        val app = context.applicationContext
        kotlinx.coroutines.CoroutineScope(kotlinx.coroutines.Dispatchers.IO).launch {
            val ok = Root.available() && Root.run("am start -n $component -f 0x14000000").ok
            if (!ok) kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.Main) { open(app, section.fallbackAction) }
        }
        return true
    }

    /**
     * Fire OS's own quick menu (the Settings-key panel), started directly (P280).
     *
     * The panel is guarded by `com.amazon.tv.permission.LAUNCHER_SETTINGS`, which a non-Amazon app cannot hold (P292),
     * so it has to be started *as root*: `su -c am start` works, `su <glass uid> -c am start` is still denied. A plain
     * `startActivity` also *succeeds* and is then refused asynchronously, so the success of the call proves nothing and
     * root has to be the first attempt rather than a fallback.
     */
    fun openQuickMenu(context: Context) {
        if (RuntimeSafety.isolated) return
        val app = context.applicationContext
        kotlinx.coroutines.CoroutineScope(kotlinx.coroutines.Dispatchers.IO).launch {
            val ok = Root.available() && Root.run("am start -n $QUICK_MENU/.StartQSUIActivity -f 0x10000000").ok
            if (ok) return@launch
            // No root: try anyway, and say so if the panel does not come up.
            val started = runCatching {
                context.startActivity(
                    Intent().setClassName(QUICK_MENU, "$QUICK_MENU.StartQSUIActivity")
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                )
            }.isSuccess
            if (!started) kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.Main) {
                dev.glasslauncher.system.RemoteNotice.show(app, "Could not open the quick menu", 3_000)
            }
        }
    }

    private const val QUICK_MENU = "com.amazon.tv.quicksettings.ui"
    private const val FIRE_SETTINGS = "com.amazon.tv.settings.v2"

    // Control Center's Alexa page: Amazon's own screens (resolved on Fire OS 8, Fire TV Stick 4K).
    fun openSmartHome(context: Context): Boolean = runCatching {
        if (RuntimeSafety.isolated) return false
        context.startActivity(Intent().setClassName("com.amazon.smarthomemapviewapp", "com.amazon.smarthomemapviewapp.SmartHomeActivity").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }.isSuccess || runCatching {
        context.startActivity(Intent(Intent.ACTION_VIEW, android.net.Uri.parse("com.amazon.smarthomemapviewapp://smarthomedashboard")).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }.isSuccess

    /**
     * Alexa's own voice UI, as holding the remote's mic button brings it up: with root, a virtual remote
     * (same key layout as the real one) holds the mic key. An ASSIST intent alone opened Alexa's voice
     * activity and it closed at once (no voice session). Alexa listens through the remote's microphone.
     */
    fun askAlexa(context: Context): Boolean {
        if (RuntimeSafety.isolated) return false
        val app = context.applicationContext
        kotlinx.coroutines.CoroutineScope(kotlinx.coroutines.Dispatchers.IO).launch {
            kotlinx.coroutines.delay(350) // Control Center closes first
            val press = if (Root.available()) VirtualRemote.path(app) else null
            if (press == null) open(app, Intent.ACTION_ASSIST) else Root.run("$press 120 $ALEXA_KEY:3000")
        }
        return true
    }

    /** Alexa's settings: guarded by an Amazon permission, so Glass opens it as root. */
    fun openAlexaSettings(context: Context): Boolean {
        if (RuntimeSafety.isolated) return false
        kotlinx.coroutines.CoroutineScope(kotlinx.coroutines.Dispatchers.IO).launch {
            Root.run("am start -n com.amazon.vizzini/.setting.AlexaSettingActivity -a amazon.intent.action.ALEXA_SETTING")
        }
        return true
    }

    /** The remote's mic button (scancode 217 maps to Fire OS's ALEXA key in its layout). */
    private const val ALEXA_KEY = 217

    fun openGameControllers(context: Context): Boolean =
        open(context, "com.amazon.device.settings.action.GAMEPADS") || openBluetooth(context)
}
