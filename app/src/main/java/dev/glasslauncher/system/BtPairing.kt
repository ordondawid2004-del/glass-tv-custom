package dev.glasslauncher.system

import android.annotation.SuppressLint
import android.bluetooth.BluetoothClass
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

/** What a nearby device is, from its Bluetooth class, so a page lists only what it was opened for. */
enum class BtKind { Controller, Remote, Other }

data class BtFound(val address: String, val name: String, val kind: BtKind, val bonding: Boolean = false)

/**
 * Pairing inside Glass (P174): a plain BluetoothAdapter discovery plus createBond, the public API, so it works in
 * every install mode. No root is needed where the permissions can be granted (BLUETOOTH_SCAN/CONNECT on API 31+, the
 * location permission below that); [permissionsMissing] says when they can't, and the caller falls back to the system
 * Bluetooth page ([SystemControls.openBluetooth]).
 */
class BtPairing(context: Context) {
    private val app = context.applicationContext
    private val adapter: BluetoothAdapter? = app.getSystemService(BluetoothManager::class.java)?.adapter
    private val _found = MutableStateFlow<List<BtFound>>(emptyList())
    val found: StateFlow<List<BtFound>> = _found
    private val _scanning = MutableStateFlow(false)
    val scanning: StateFlow<Boolean> = _scanning
    private val _bonded = MutableStateFlow<BtFound?>(null)
    /** The device that just bonded (P186): the pairing page returns to the list when this is set. */
    val bonded: StateFlow<BtFound?> = _bonded
    private var registered = false

    private val receiver = object : BroadcastReceiver() {
        @SuppressLint("MissingPermission")
        override fun onReceive(c: Context, i: Intent) {
            when (i.action) {
                BluetoothDevice.ACTION_FOUND -> {
                    val d = i.device() ?: return
                    if (d.bondState == BluetoothDevice.BOND_BONDED) return
                    val name = runCatching { d.name }.getOrNull() ?: i.getStringExtra(BluetoothDevice.EXTRA_NAME) ?: return
                    val kind = kindOf(runCatching { d.bluetoothClass?.majorDeviceClass }.getOrNull(), runCatching { d.bluetoothClass?.deviceClass }.getOrNull())
                    _found.value = (_found.value.filter { it.address != d.address } + BtFound(d.address, name, kind)).sortedBy { it.name.lowercase() }
                }
                BluetoothDevice.ACTION_BOND_STATE_CHANGED -> {
                    val d = i.device() ?: return
                    when (d.bondState) {
                        BluetoothDevice.BOND_BONDING -> mark(d.address, true)
                        else -> {
                            // Bonded: it moves to the connected list on the next refresh. Failed: back to idle in the list.
                            if (d.bondState == BluetoothDevice.BOND_BONDED) {
                                _bonded.value = _found.value.firstOrNull { it.address == d.address } ?: BtFound(d.address, runCatching { d.name }.getOrNull().orEmpty(), BtKind.Other)
                                _found.value = _found.value.filter { it.address != d.address }
                            } else mark(d.address, false)
                        }
                    }
                }
                BluetoothAdapter.ACTION_DISCOVERY_FINISHED -> _scanning.value = false
            }
        }
    }

    @Suppress("DEPRECATION")
    private fun Intent.device(): BluetoothDevice? =
        if (android.os.Build.VERSION.SDK_INT >= 33) getParcelableExtra(BluetoothDevice.EXTRA_DEVICE, BluetoothDevice::class.java) else getParcelableExtra(BluetoothDevice.EXTRA_DEVICE)

    private fun mark(address: String, bonding: Boolean) {
        _found.value = _found.value.map { if (it.address == address) it.copy(bonding = bonding) else it }
    }

    /** True when discovery can't start for want of a permission (the page then offers the system Bluetooth page). */
    fun permissionsMissing(): Boolean = needed().any { app.checkSelfPermission(it) != android.content.pm.PackageManager.PERMISSION_GRANTED }

    private fun needed(): List<String> =
        if (android.os.Build.VERSION.SDK_INT >= 31) listOf(android.Manifest.permission.BLUETOOTH_SCAN, android.Manifest.permission.BLUETOOTH_CONNECT)
        else listOf(android.Manifest.permission.ACCESS_FINE_LOCATION)

    /** Grants the missing permissions through the root shell when there is one (Glass Pro / Glass OS); true if none is missing after. */
    suspend fun grantWithRoot(): Boolean {
        if (!permissionsMissing()) return true
        Root.run(needed().joinToString("\n") { "pm grant ${app.packageName} $it" })
        return !permissionsMissing()
    }

    @SuppressLint("MissingPermission")
    fun start(): Boolean {
        if (RuntimeSafety.isolated) return false
        val a = adapter ?: return false
        if (!a.isEnabled || permissionsMissing()) return false
        if (!registered) {
            app.registerReceiver(receiver, IntentFilter().apply {
                addAction(BluetoothDevice.ACTION_FOUND); addAction(BluetoothDevice.ACTION_BOND_STATE_CHANGED); addAction(BluetoothAdapter.ACTION_DISCOVERY_FINISHED)
            })
            registered = true
        }
        _found.value = emptyList(); _bonded.value = null
        _scanning.value = runCatching { a.isDiscovering || a.startDiscovery() }.getOrDefault(false)
        return _scanning.value
    }

    @SuppressLint("MissingPermission")
    fun stop() {
        runCatching { adapter?.cancelDiscovery() }
        if (registered) { runCatching { app.unregisterReceiver(receiver) }; registered = false }
        _scanning.value = false
    }

    @SuppressLint("MissingPermission")
    fun pair(address: String): Boolean {
        if (RuntimeSafety.isolated) return false
        val d = runCatching { adapter?.getRemoteDevice(address) }.getOrNull() ?: return false
        runCatching { adapter?.cancelDiscovery() }
        return runCatching { d.createBond() }.getOrDefault(false)
    }

    companion object {
        /** Classifies a device by its Bluetooth class: gamepads and joysticks are controllers; keyboards, pointers and the Amazon remote are remotes. */
        fun kindOf(major: Int?, device: Int?): BtKind = when {
            device == 0x0508 /* gamepad */ || device == 0x0504 /* joystick */ -> BtKind.Controller
            major == BluetoothClass.Device.Major.PERIPHERAL -> BtKind.Remote
            else -> BtKind.Other
        }
    }
}
