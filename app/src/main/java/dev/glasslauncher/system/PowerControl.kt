package dev.glasslauncher.system

import android.content.Context
import android.content.pm.PackageManager
import kotlinx.coroutines.delay

/**
 * Control Center's Power Off (I09): HDMI-CEC standby to the TV, then sleep the stick itself. `HdmiControlManager`
 * is a hidden API on API 30, so it's reached by reflection against the "hdmi_control" service; any step of that
 * (service missing, no playback client, a SecurityException from a missing HDMI_CEC grant) just means no CEC,
 * never a crash. Sleeping the stick reuses [SleepTimer.sleepNow] (root KEYCODE_SLEEP, else the accessibility
 * lock action) so there's one place that does it.
 */
object PowerControl {
    /** The three ways Power Off can behave, from what this build/device actually grants. */
    enum class Mode { CecAndSleep, SleepOnly, Unavailable }

    fun mode(context: Context, root: Boolean, keysServiceOn: Boolean): Mode = when {
        !SleepTimer.canSleep(root, keysServiceOn) -> Mode.Unavailable
        cecAvailable(context) -> Mode.CecAndSleep
        else -> Mode.SleepOnly
    }

    fun mode(context: Context): Mode = mode(context, Root.known, RemoteKeysService.instance != null || HomeSetup.isRemoteKeysEnabled(context))

    /** True only when the HDMI_CEC permission is granted AND a playback client was actually obtained. */
    fun cecAvailable(context: Context): Boolean {
        if (context.checkSelfPermission("android.permission.HDMI_CEC") != PackageManager.PERMISSION_GRANTED) return false
        return playbackClient(context) != null
    }

    /**
     * Sends `<Standby>` to the TV and reports whether it was sent (false: no HDMI_CEC grant, no client, or the call
     * threw). Never throws.
     */
    fun sendCecStandby(context: Context): Boolean = runCatching {
        if (RuntimeSafety.isolated) return false
        val client = playbackClient(context) ?: return@runCatching false
        client.javaClass.getMethod("sendStandby").invoke(client)
        true
    }.getOrDefault(false)

    /**
     * Standby the TV over CEC, then (after a beat for the message to leave) sleep the stick. [onNoCec] runs at once
     * when the TV couldn't be told, so the caller can say the TV stays on; the stick still sleeps.
     */
    suspend fun standby(context: Context, onNoCec: () -> Unit = {}) {
        if (RuntimeSafety.isolated) return
        // A failed CEC call lingers longer so the "TV stays on" notice is seen before the screen goes dark.
        val sent = sendCecStandby(context)
        if (!sent) onNoCec()
        delay(if (sent) 1_000 else 3_000)
        SleepTimer.sleepNow(context)
    }

    private fun playbackClient(context: Context): Any? = runCatching {
        val manager = context.getSystemService("hdmi_control") ?: return@runCatching null
        manager.javaClass.getMethod("getPlaybackClient").invoke(manager)
    }.getOrNull()
}
