package dev.glasslauncher.system

import android.annotation.SuppressLint
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothProfile
import android.app.PendingIntent
import android.bluetooth.le.ScanFilter
import android.bluetooth.le.ScanResult
import android.bluetooth.le.ScanSettings
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.Handler
import android.os.Looper
import android.os.ParcelUuid
import android.os.PowerManager
import android.util.Log
import dev.glasslauncher.system.RemotePairPolicy.Action
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

/**
 * Glass's own remote pairing (P75 B), for when Fire's AutoPairService is off (`persist.sys.auto_pair_sys_service`
 * false: it never starts, so nothing of Fire's can unpair the remote). Glass is a privileged system app (module
 * glass-system), so it may bond and connect on its own.
 *
 * It scans while no Amazon remote is connected, screen on or off (a stick without a remote idles to sleep, which is
 * when pairing is needed), re-registering the scan every 25 s because Fire's BtLeManager stops scans silently
 * (hardware filter: company id 0x0171 + the HID service 0x1812, low-power mode) and acts through [RemotePairPolicy]: an unbonded remote in pairing mode
 * (LE General Discoverable) is bonded; a bonded one that isn't connected is only connected, never unbonded on
 * sight. Every step is logged under [TAG].
 *
 * Some calls are hidden API (createBond(transport), the HID host proxy, setConnectionPolicy): reflection inside
 * runCatching, logged on failure. Needs BLUETOOTH_PRIVILEGED, which only a system app is granted.
 */
class RemotePairer(private val context: Context) {
    private val app = context.applicationContext
    private val handler = Handler(Looper.getMainLooper())
    private val adapter: BluetoothAdapter? = app.getSystemService(BluetoothManager::class.java)?.adapter
    private val power = app.getSystemService(PowerManager::class.java)
    private var scanning = false
    private var started = false
    private var lastRebond = 0L
    private var lastStaleNotice = 0L
    private var scanStartedAt = 0L
    private var lastRemoteAdvert = 0L
    /** When the remote was first seen missing in this stretch (0 = it is connected, or the window was just opened again). */
    private var remoteGoneAt = 0L
    private var lastBatteryHint = 0L
    private val failures = HashMap<String, Int>()
    private val lastAction = HashMap<String, Long>()
    private val pendingConnect = HashSet<String>()
    private var hidHost: BluetoothProfile? = null

    private val screen = object : BroadcastReceiver() {
        override fun onReceive(c: Context, i: Intent) {
            when (i.action) {
                // Each of these opens the look-for-the-remote window again (RemotePairPolicy.shouldScan).
                Intent.ACTION_SCREEN_ON -> { Log.i(TAG, "screen on"); remoteGoneAt = 0L; evaluate() }
                Intent.ACTION_SCREEN_OFF -> { Log.i(TAG, "screen off: the scan keeps running while no remote is connected"); evaluate() }
                BluetoothDevice.ACTION_ACL_CONNECTED, BluetoothDevice.ACTION_ACL_DISCONNECTED -> { remoteGoneAt = 0L; evaluate() }
                BluetoothDevice.ACTION_BOND_STATE_CHANGED -> onBond(i)
                BluetoothAdapter.ACTION_STATE_CHANGED -> onAdapterState(i.getIntExtra(BluetoothAdapter.EXTRA_STATE, BluetoothAdapter.ERROR))
            }
        }
    }

    fun start() {
        if (RuntimeSafety.isolated) return
        if (started) return
        if (adapter == null) { Log.i(TAG, "no bluetooth adapter"); return }
        started = true
        val f = IntentFilter().apply {
            addAction(Intent.ACTION_SCREEN_ON); addAction(Intent.ACTION_SCREEN_OFF)
            addAction(BluetoothDevice.ACTION_ACL_CONNECTED); addAction(BluetoothDevice.ACTION_ACL_DISCONNECTED)
            addAction(BluetoothDevice.ACTION_BOND_STATE_CHANGED); addAction(BluetoothAdapter.ACTION_STATE_CHANGED)
        }
        androidx.core.content.ContextCompat.registerReceiver(app, screen, f, androidx.core.content.ContextCompat.RECEIVER_NOT_EXPORTED)
        runCatching { adapter!!.getProfileProxy(app, object : BluetoothProfile.ServiceListener {
            override fun onServiceConnected(profile: Int, proxy: BluetoothProfile) { hidHost = proxy; Log.i(TAG, "HID host proxy ready") }
            override fun onServiceDisconnected(profile: Int) { hidHost = null }
        }, HID_HOST) }.onFailure { Log.w(TAG, "no HID host proxy", it) }
        Log.i(TAG, "started (screen ${if (power?.isInteractive == true) "on" else "off"})")
        ensureLocation()
        evaluate()
    }

    /** Bluetooth turned off and on (a remote re-pair, a Settings toggle) must not leave the pairer asleep with a dead scan. */
    private fun onAdapterState(state: Int) {
        Log.i(TAG, "bluetooth state $state")
        when (state) {
            BluetoothAdapter.STATE_ON -> { scanning = false; handler.postDelayed({ evaluate() }, 1_000) }
            BluetoothAdapter.STATE_TURNING_OFF, BluetoothAdapter.STATE_OFF -> { scanning = false }
        }
    }

    private fun hasLocation() = app.checkSelfPermission(android.Manifest.permission.ACCESS_FINE_LOCATION) == android.content.pm.PackageManager.PERMISSION_GRANTED

    /**
     * Android 11 drops every BLE scan result for an app without ACCESS_FINE_LOCATION, so hold-Home pairing never sees the
     * remote. Pro/OS grant it at install (module service.sh, default-permissions); with su here it is granted now;
     * otherwise this logs loudly and tells the user once.
     */
    private fun ensureLocation() {
        if (hasLocation()) return
        Log.e(TAG, "ACCESS_FINE_LOCATION not granted: Android 11 drops BLE scan results, so hold-Home pairing cannot work")
        CoroutineScope(Dispatchers.IO).launch {
            if (Root.known || Root.available()) {
                Root.action(app, "Grant location for remote pairing", "pm grant ${app.packageName} android.permission.ACCESS_FINE_LOCATION")
            }
            if (hasLocation()) { Log.i(TAG, "ACCESS_FINE_LOCATION granted"); handler.post { evaluate() } }
            else RemoteNotice.show(app, "Remote pairing needs the Location permission. Update VITRE Pro, or allow Location in VITRE's app settings.", 10_000, actionable = true)
        }
    }

    fun stop() {
        if (!started) return
        started = false
        handler.removeCallbacks(audioRecheck)
        stopScan()
        runCatching { app.unregisterReceiver(screen) }
        Log.i(TAG, "stopped")
    }

    /** Scan while the screen is on and no Amazon remote is connected; stop otherwise. */
    @SuppressLint("MissingPermission")
    private fun evaluate() {
        val remoteConnected = anyRemoteConnected()
        val now = System.currentTimeMillis()
        if (remoteConnected) remoteGoneAt = 0L else if (remoteGoneAt == 0L) remoteGoneAt = now
        val bonded = runCatching { adapter?.bondedDevices.orEmpty().any { isAmazonRemote(it) } }.getOrDefault(false)
        val shouldScan = RemotePairPolicy.shouldScan(remoteConnected, bonded, if (remoteGoneAt == 0L) 0L else now - remoteGoneAt)
        // No scan while Bluetooth audio is connected (P169/P175): a scan every 25 s on the radio carrying A2DP glitched the
        // music, and the stick hard-reset twice under headphones + gamepad + scanning. Pairing waits until they disconnect.
        val audio = btAudioConnected()
        handler.removeCallbacks(audioRecheck)
        // Paused for Bluetooth audio only: look again later, so pairing resumes once the headphones disconnect.
        if (audio && shouldScan) handler.postDelayed(audioRecheck, RemotePairPolicy.RESCAN_EVERY_MS)
        if (!audio && shouldScan) startScan() else {
            if (scanning) Log.i(TAG, if (remoteConnected) "scan stops (remote connected)" else "scan stops (bonded remote quiet for ${(now - remoteGoneAt) / 60_000} min: asleep, not lost)")
            stopScan()
        }
    }

    @SuppressLint("MissingPermission")
    private fun anyRemoteConnected(): Boolean = remoteInputPresent() || runCatching {
        adapter?.bondedDevices.orEmpty().any { isAmazonRemote(it) && isConnected(it) }
    }.getOrDefault(false)

    private val audioRecheck = Runnable { evaluate() }

    private fun btAudioConnected(): Boolean = runCatching {
        app.getSystemService(android.media.AudioManager::class.java)?.getDevices(android.media.AudioManager.GET_DEVICES_OUTPUTS).orEmpty()
            .any { it.type == android.media.AudioDeviceInfo.TYPE_BLUETOOTH_A2DP || it.type == android.media.AudioDeviceInfo.TYPE_BLUETOOTH_SCO }
    }.getOrDefault(false)

    /**
     * A remote whose keys are arriving is connected, whatever BluetoothDevice.isConnected (a hidden API, read by
     * reflection) says. When that reflection failed, Glass rescanned every 25 s with the remote in use, on the same radio
     * as A2DP headphones and a gamepad (P169/P175: glitchy music, and a hard reset under that load). An external input
     * device named like Fire's remote is the public-API answer.
     */
    private fun remoteInputPresent(): Boolean = runCatching {
        android.view.InputDevice.getDeviceIds().any { id ->
            val d = android.view.InputDevice.getDevice(id) ?: return@any false
            (android.os.Build.VERSION.SDK_INT < 29 || d.isExternal) && RemotePairPolicy.isAmazonRemoteName(d.name)
        }
    }.getOrDefault(false)

    /** An input peripheral (class 0x0500) or one named like Fire's remote: only these matter to the policy. */
    @SuppressLint("MissingPermission")
    private fun isAmazonRemote(d: BluetoothDevice): Boolean {
        return RemotePairPolicy.looksLikeRemote(runCatching { d.bluetoothClass?.majorDeviceClass }.getOrNull(), runCatching { d.name }.getOrNull())
    }

    private fun isConnected(d: BluetoothDevice): Boolean =
        runCatching { BluetoothDevice::class.java.getMethod("isConnected").invoke(d) as Boolean }.getOrDefault(false)

    @SuppressLint("MissingPermission")
    private fun startScan() {
        if (scanning) return
        val scanner = adapter?.bluetoothLeScanner ?: run { Log.i(TAG, "no LE scanner (Bluetooth off?)"); return }
        val filter = ScanFilter.Builder()
            .setManufacturerData(AMAZON_COMPANY_ID, ByteArray(0))
            .setServiceUuid(ParcelUuid.fromString(HID_SERVICE_UUID))
            .build()
        val settings = ScanSettings.Builder().setScanMode(ScanSettings.SCAN_MODE_LOW_POWER).build()
        runCatching { scanner.startScan(listOf(filter), settings, scanIntent); scanning = true; Log.i(TAG, "scan started") }
            .onFailure { Log.w(TAG, "scan failed to start", it) }
        if (scanning) {
            scanStartedAt = System.currentTimeMillis()
            // Exactly one of each, ever (P169 #4): every 25 s rescan came back through here and added another
            // self-reposting batteryCheck without removing the last, so after 18 h some 2,600 ran on the main thread
            // (~130 Bluetooth binder calls a second): Glass's main thread at 34% CPU, Bluetooth at 45%, an ANR at 03:01
            // and the stick rebooting three times overnight with nobody using it.
            handler.removeCallbacks(batteryCheck)
            handler.removeCallbacks(rescan)
            handler.postDelayed(batteryCheck, 20_000)
            handler.postDelayed(rescan, RemotePairPolicy.RESCAN_EVERY_MS)
        }
    }

    /** Register the scan again: something else (Fire's BtLeManager) may have stopped it without telling us. */
    private val rescan = object : Runnable {
        @SuppressLint("MissingPermission")
        override fun run() {
            if (!scanning) return
            val scanner = adapter?.bluetoothLeScanner
            runCatching { scanner?.stopScan(scanIntent) }
            scanning = false
            val started = scanStartedAt
            // Through the policy again: headphones may have connected since (no scan then) or the remote come back.
            evaluate()
            if (scanning) scanStartedAt = started
        }
    }

    /** A bonded remote that never advertises while we scan: usually dead batteries (they masked this once). */
    private val batteryCheck = object : Runnable {
        @SuppressLint("MissingPermission")
        override fun run() {
            if (!scanning) return
            val now = System.currentTimeMillis()
            val since = maxOf(scanStartedAt, lastRemoteAdvert)
            val hint = RemotePairPolicy.batteryHint(
                haveBondedRemote = runCatching { adapter?.bondedDevices.orEmpty().any { isAmazonRemote(it) } }.getOrDefault(false),
                remoteConnected = anyRemoteConnected(), screenOn = power?.isInteractive == true,
                homeInFront = RemoteWatch.homeInFront,
                sinceOtherInputMs = RemoteWatch.sinceOtherInputMs(),
                scanningForMs = now - scanStartedAt, silentForMs = now - since, sinceLastHintMs = now - lastBatteryHint,
            )
            if (hint) {
                lastBatteryHint = now
                Log.w(TAG, "bonded remote silent for ${(now - since) / 1000} s while scanning")
                RemoteNotice.show(app, "The remote isn't answering. Try fresh batteries, then hold Home for 10 s.", 15_000, actionable = true)
            }
            handler.postDelayed(this, 20_000)
        }
    }

    @SuppressLint("MissingPermission")
    private fun stopScan() {
        handler.removeCallbacks(batteryCheck)
        handler.removeCallbacks(rescan)
        if (!scanning) return
        scanning = false
        runCatching { adapter?.bluetoothLeScanner?.stopScan(scanIntent) }
    }

    @SuppressLint("MissingPermission")
    private fun onAdvert(r: ScanResult) {
        val rec = r.scanRecord ?: return
        val parsed = rec.bytes?.let { AdvParser.parse(it) }
        // Limited OR General discoverable (the Fire remote advertises flags 0x05: Limited + BR/EDR not supported).
        val flags = (parsed?.flags ?: 0) or rec.advertiseFlags.coerceAtLeast(0)
        val ad = RemotePairPolicy.Advert(
            address = r.device.address,
            amazon = rec.manufacturerSpecificData?.get(AMAZON_COMPANY_ID) != null,
            hid = rec.serviceUuids?.any { it.uuid == ParcelUuid.fromString(HID_SERVICE_UUID).uuid } == true,
            discoverable = AdvParser.discoverable(flags),
            rssi = r.rssi,
            appearance = parsed?.appearance,
        )
        if (ad.amazon && ad.hid) {
            lastRemoteAdvert = System.currentTimeMillis()
            runCatching { RemoteRegistry(app).record(ad.address, r.device.name, r.rssi, lastRemoteAdvert) }
        }
        val now = System.currentTimeMillis()
        val bonded = runCatching { adapter?.bondedDevices.orEmpty().map { it.address }.toSet() }.getOrDefault(emptySet())
        val state = RemotePairPolicy.State(
            bonded = bonded, remoteConnected = anyRemoteConnected(), screenOn = power?.isInteractive == true,
            nowMs = now, lastRebondMs = lastRebond, connectFailures = failures[ad.address] ?: 0,
            haveBondedRemote = runCatching { adapter?.bondedDevices.orEmpty().any { isAmazonRemote(it) } }.getOrDefault(false),
        )
        val action = RemotePairPolicy.decide(ad, state)
        if (action == Action.Ignore) return
        if (action == Action.StaleBond) {
            // Logged on every advert; the user is told at most every 5 minutes.
            Log.w(TAG, "advert ${ad.address} (rssi ${r.rssi}) is a remote that kept a bond the stick lost: it must be reset first")
            if (now - lastStaleNotice >= 5 * 60_000L) {
                lastStaleNotice = now
                RemoteNotice.show(app, RemoteNotice.STALE_BOND, 20_000, actionable = true)
            }
            return
        }
        // One act per address per 8 s (a remote advertises many times a second).
        if (now - (lastAction[ad.address] ?: 0L) < 8_000L) return
        lastAction[ad.address] = now
        Log.i(TAG, "advert ${ad.address} (rssi ${r.rssi}, discoverable=${ad.discoverable}): $action")
        when (action) {
            Action.Bond -> bond(r.device)
            Action.Connect -> connect(r.device)
            Action.Rebond -> {
                lastRebond = now; failures[ad.address] = 0
                Log.i(TAG, "re-bonding ${ad.address}: ${RemotePairPolicy.REBOND_AFTER_FAILURES} connects failed")
                runCatching { BluetoothDevice::class.java.getMethod("removeBond").invoke(r.device) }.onFailure { Log.w(TAG, "removeBond failed", it) }
                handler.postDelayed({ bond(r.device) }, 1_500)
            }
            Action.Ignore, Action.StaleBond -> Unit
        }
    }

    @SuppressLint("MissingPermission")
    private fun bond(d: BluetoothDevice) {
        pendingConnect += d.address
        val ok = runCatching { BluetoothDevice::class.java.getMethod("createBond", Int::class.javaPrimitiveType).invoke(d, TRANSPORT_LE) as Boolean }
            .recoverCatching { d.createBond() }.getOrDefault(false)
        Log.i(TAG, "createBond(${d.address}) -> $ok")
    }

    private fun onBond(i: Intent) {
        val d = i.getParcelableExtra<BluetoothDevice>(BluetoothDevice.EXTRA_DEVICE) ?: return
        val state = i.getIntExtra(BluetoothDevice.EXTRA_BOND_STATE, BluetoothDevice.ERROR)
        Log.i(TAG, "bond state ${d.address}: $state")
        if (state == BluetoothDevice.BOND_BONDED && d.address in pendingConnect) {
            pendingConnect -= d.address
            hidPolicyAllowed(d)
            connect(d)
            RemoteNotice.show(app, "Remote paired", 2_500)
        }
    }

    private fun hidPolicyAllowed(d: BluetoothDevice) {
        val proxy = hidHost ?: return Unit.also { Log.w(TAG, "no HID host proxy: connection policy not set") }
        val ok = runCatching { proxy.javaClass.getMethod("setConnectionPolicy", BluetoothDevice::class.java, Int::class.javaPrimitiveType).invoke(proxy, d, CONNECTION_POLICY_ALLOWED) }
        Log.i(TAG, "setConnectionPolicy(${d.address}, ALLOWED) -> ${ok.getOrNull() ?: ok.exceptionOrNull()}")
    }

    private fun connect(d: BluetoothDevice) {
        val proxy = hidHost
        val ok = if (proxy != null) runCatching { proxy.javaClass.getMethod("connect", BluetoothDevice::class.java).invoke(proxy, d) }.getOrNull() else null
        Log.i(TAG, "connect(${d.address}) -> $ok")
        // Did it come up? Not within 12 s counts as one failure (see RemotePairPolicy.REBOND_AFTER_FAILURES).
        handler.postDelayed({
            if (isConnected(d)) failures[d.address] = 0 else failures[d.address] = (failures[d.address] ?: 0) + 1
            Log.i(TAG, "after connect: ${d.address} connected=${isConnected(d)}, failures=${failures[d.address]}")
        }, 12_000)
    }

    /** The scan's delivery: a broadcast to [RemoteScanReceiver], so it reaches Glass even when the process was killed. */
    private val scanIntent: PendingIntent by lazy {
        val flags = PendingIntent.FLAG_UPDATE_CURRENT or
            (if (android.os.Build.VERSION.SDK_INT >= 31) PendingIntent.FLAG_MUTABLE else 0)
        PendingIntent.getBroadcast(app, 0, Intent(app, RemoteScanReceiver::class.java).setAction(ACTION_SCAN), flags)
    }

    /** Results the system delivered for the registered scan (the receiver calls this, on the main thread). */
    fun onResults(results: List<android.bluetooth.le.ScanResult>) {
        if (RuntimeSafety.isolated) return
        handler.post {
            for (r in results) onAdvert(r)
        }
    }

    companion object {
        const val ACTION_SCAN = "${dev.glasslauncher.BuildConfig.APPLICATION_ID}.action.REMOTE_SCAN"
        @Volatile private var instance: RemotePairer? = null

        /** The one pairer; a scan result that wakes a cold process starts it, if Glass's own pairing is on. */
        @Synchronized
        fun obtain(context: Context): RemotePairer {
            instance?.let { return it }
            return RemotePairer(context.applicationContext).also { instance = it; if (RootFeatures.ownRemotePairing(context)) it.start() }
        }

        const val TAG = "GlassRemotePair"
        const val AMAZON_COMPANY_ID = 0x0171
        const val HID_SERVICE_UUID = "00001812-0000-1000-8000-00805f9b34fb"
        /** BluetoothProfile.HID_HOST (hidden constant) and BluetoothDevice.TRANSPORT_LE, CONNECTION_POLICY_ALLOWED. */
        private const val HID_HOST = 4
        private const val TRANSPORT_LE = 2
        private const val CONNECTION_POLICY_ALLOWED = 100
    }
}
