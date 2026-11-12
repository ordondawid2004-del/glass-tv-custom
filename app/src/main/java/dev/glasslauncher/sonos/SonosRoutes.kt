package dev.glasslauncher.sonos

import dev.glasslauncher.system.RuntimeSafety

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.media.AudioManager
import android.media.projection.MediaProjection
import android.os.Handler
import android.os.Looper
import dev.glasslauncher.data.AudioOutputConfig
import dev.glasslauncher.widgets.NowPlaying
import dev.glasslauncher.widgets.NowPlayingSource
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import java.net.Inet4Address
import java.net.InetAddress
import java.net.NetworkInterface

/**
 * Plays Glass's audio (captured from other apps) on one or several Sonos speakers. Order: consent, capture, SonosSession.start.
 * Consent is asked every session in every install mode (no appops grant, no root shortcut). The speaker volume is
 * never touched. All of the lifecycle lives in [RouteRunner]; this object wires it to Android.
 */
object SonosRoutes {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    @Volatile private var nowPlaying: NowPlaying? = null
    private var runner: RouteRunner? = null

    @Synchronized
    private fun runner(context: Context): RouteRunner {
        if (RuntimeSafety.isolated) throw IllegalStateException(RuntimeSafety.reason)
        runner?.let { return it }
        val app = context.applicationContext
        val r = RouteRunner(
            scope = scope,
            muter = MusicMuter(app.getSystemService(AudioManager::class.java)),
            flag = PrefsMuteFlag(app),
            sessionFor = { sp ->
                val host = InetAddress.getByName(sp.host)
                SonosSession(sp.host, LocalNet.pick(host) ?: error("no network path to ${sp.host}"))
            },
            playingApp = {
                nowPlaying?.let { np -> PlayingApp(AppCapture.label(app, np.packageName), np.playing, AppCapture.allows(app, np.packageName)) }
            },
            teardown = { app.stopService(Intent(app, CaptureService::class.java)) },
        )
        scope.launch { NowPlayingSource.flow(app).collect { nowPlaying = it } }
        runner = r
        return r
    }

    fun state(context: Context): StateFlow<RouteState> = runner(context).state

    /** App start: unmute a TV that a crashed or killed run left muted. */
    fun recoverMute(context: Context) = runner(context).recoverMute()

    /** Asks for consent (RECORD_AUDIO, then the projection dialog); everything else follows from the result. */
    fun start(context: Context, speakers: List<AudioOutputConfig>, muteTvWhilePlaying: Boolean) {
        if (!runner(context).begin(speakers, muteTvWhilePlaying)) return
        ConsentGate.begin(android.os.SystemClock.uptimeMillis())
        // Pro (su): pre-grant first so the system skips the projection dialog (P135). The plain APK has no su and keeps it.
        scope.launch {
            runCatching { ProjectionPregrant.ensure(context.applicationContext) }
            Handler(Looper.getMainLooper()).post { context.startActivity(consentIntent(context)) }
        }
    }

    /** Adds a speaker to a route that plays; the consent already given covers it. */
    fun addSpeaker(context: Context, speaker: AudioOutputConfig) { runner(context).addSpeaker(speaker) }

    fun removeSpeaker(context: Context, host: String) = runner(context).removeSpeaker(host)

    /** Applies the mute toggle to a route that is already running. */
    fun setMuteTv(context: Context, enabled: Boolean) = runner(context).setMuteTv(enabled)

    /** False on fixed-volume outputs (HDMI on Fire TV), where a local mute does nothing; see [MuteSupport]. */
    fun canMuteTv(context: Context): Boolean {
        val audio = context.getSystemService(AudioManager::class.java) ?: return false
        val outs = audio.getDevices(AudioManager.GET_DEVICES_OUTPUTS).map { it.type }
        val hdmi = outs.any { it == android.media.AudioDeviceInfo.TYPE_HDMI || it == android.media.AudioDeviceInfo.TYPE_HDMI_ARC }
        val bt = outs.any { it == android.media.AudioDeviceInfo.TYPE_BLUETOOTH_A2DP }
        return MuteSupport.supported(audio.isVolumeFixed, hdmi, bt)
    }

    /** Consent runs in its own transient task, so Glass Home never comes forward over the app being captured. */
    fun consentIntent(context: Context) = Intent(context, CaptureConsentActivity::class.java).addFlags(CONSENT_FLAGS)

    const val CONSENT_FLAGS = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_MULTIPLE_TASK or Intent.FLAG_ACTIVITY_NO_ANIMATION

    fun onConsent(context: Context, resultCode: Int, data: Intent?) {
        ConsentGate.end()
        val r = runner(context)
        if (resultCode != Activity.RESULT_OK || data == null) { r.consentDenied(); return }
        r.consentGranted()
        context.startForegroundService(Intent(context, CaptureService::class.java).putExtra("code", resultCode).putExtra("data", data))
    }

    fun onDenied(context: Context) { ConsentGate.end(); runner(context).consentDenied() }

    /** The foreground service has its projection. Its onStop (the user or the system ending the capture) stops the route. */
    fun onProjection(context: Context, projection: MediaProjection) {
        val r = runner(context)
        projection.registerCallback(object : MediaProjection.Callback() { override fun onStop() = r.stop() }, Handler(Looper.getMainLooper()))
        r.run(CaptureSource(projection))
    }

    fun stop(context: Context) { ConsentGate.end(); runner(context).stop() }

    /** The service went away on its own: stop the route if it still holds anything. */
    fun onServiceGone(context: Context) { val r = runner(context); if (r.holdsResources()) r.stop() }
}

private class MusicMuter(private val audio: AudioManager) : StreamMuter {
    override fun isMuted() = audio.isStreamMute(AudioManager.STREAM_MUSIC)
    override fun setMuted(muted: Boolean) =
        audio.adjustStreamVolume(AudioManager.STREAM_MUSIC, if (muted) AudioManager.ADJUST_MUTE else AudioManager.ADJUST_UNMUTE, 0)
}

private class PrefsMuteFlag(context: Context) : MuteFlag {
    private val prefs = context.getSharedPreferences("sonos-route", Context.MODE_PRIVATE)
    override var heldByGlass: Boolean
        get() = prefs.getBoolean("tvMutedByGlass", false)
        set(v) { prefs.edit().putBoolean("tvMutedByGlass", v).commit() } // commit: it must land before a possible crash
}

/** Which of this device's addresses reaches a speaker. */
object LocalNet {
    /** Picks the IPv4 address whose subnet contains [speaker]; null when none does. */
    fun pick(speaker: InetAddress): InetAddress? = try {
        val target = (speaker as? Inet4Address)?.address
        NetworkInterface.getNetworkInterfaces().toList().filter { it.isUp && !it.isLoopback }
            .flatMap { it.interfaceAddresses }
            .firstOrNull { ia -> ia.address is Inet4Address && target != null && sameSubnet(ia.address.address, target, ia.networkPrefixLength.toInt()) }
            ?.address
    } catch (_: Exception) { null }

    fun sameSubnet(a: ByteArray, b: ByteArray, prefix: Int): Boolean {
        if (a.size != 4 || b.size != 4 || prefix !in 0..32) return false
        val mask = if (prefix == 0) 0 else -1 shl (32 - prefix)
        fun v(x: ByteArray) = ((x[0].toInt() and 255) shl 24) or ((x[1].toInt() and 255) shl 16) or ((x[2].toInt() and 255) shl 8) or (x[3].toInt() and 255)
        return (v(a) and mask) == (v(b) and mask)
    }
}

/** Reads an app's allowCapture opt-out where the platform exposes it. */
object AppCapture {
    /** false = the app opted out; true = allowed; null = not readable here (silence detection decides). */
    fun allows(context: Context, pkg: String): Boolean? = try {
        val info = context.packageManager.getApplicationInfo(pkg, 0)
        ApplicationInfo::class.java.getMethod("isAudioPlaybackCaptureAllowed").invoke(info) as? Boolean
    } catch (_: Throwable) { null }

    fun label(context: Context, pkg: String): String = try {
        context.packageManager.getApplicationLabel(context.packageManager.getApplicationInfo(pkg, 0)).toString()
    } catch (_: Exception) { pkg }
}
