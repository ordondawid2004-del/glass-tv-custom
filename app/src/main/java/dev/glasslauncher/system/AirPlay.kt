package dev.glasslauncher.system

import android.content.BroadcastReceiver
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import androidx.compose.ui.platform.LocalContext
import androidx.core.content.ContextCompat
import dev.glasslauncher.system.AirPlayProtocol.Event
import dev.glasslauncher.system.AirPlayProtocol.SettingKey

/**
 * AirPlay receiving through PhairPlay (github.com/mazer666/PhairPlay), built and installed with the
 * control patch by `scripts/phairplay install`. Glass only switches the receiver on and off and shows
 * its state; PhairPlay advertises the TV, and brings its own player forward when a Mac or iPhone
 * starts mirroring. Messages go both ways under a signature permission, so only apps signed with
 * Glass's key can drive it. The wire format is in [AirPlayProtocol]; a PhairPlay that doesn't report
 * a protocol version is the old one and only gets the plain on/off toggle.
 */
object AirPlay {
    internal val PACKAGES = listOf("com.phairplay.firetv", "com.phairplay.googletv")
    private const val RECEIVER = "com.phairplay.service.ControlReceiver"

    /** [protocol] is 1 until PhairPlay reports otherwise; [settings] is null for protocol 1. */
    @Immutable
    data class State(
        val on: Boolean,
        val sender: String? = null,
        val protocol: Int = 1,
        val settings: AirPlayProtocol.Settings? = null,
    ) {
        val supportsControl get() = protocol >= AirPlayProtocol.VERSION
    }

    /** The installed PhairPlay that has the control receiver, or null. */
    fun receiver(context: Context): ComponentName? {
        if (RuntimeSafety.isolated) return null
        val pm = context.packageManager
        return PACKAGES.firstNotNullOfOrNull { pkg ->
            ComponentName(pkg, RECEIVER).takeIf {
                runCatching { pm.getReceiverInfo(it, 0) }.isSuccess &&
                    // Both apps declare the permission, so also require PhairPlay to carry Glass's signing cert.
                    pm.checkSignatures(context.packageName, pkg) == PackageManager.SIGNATURE_MATCH &&
                    pm.checkPermission(AirPlayProtocol.PERMISSION, context.packageName) == PackageManager.PERMISSION_GRANTED
            }
        }
    }

    fun set(context: Context, target: ComponentName, on: Boolean) = send(context, AirPlayProtocol.encodeToggle(target, on))

    fun query(context: Context, target: ComponentName) = send(context, AirPlayProtocol.encodeQuery(target))

    /** Writes one setting; false (nothing sent) when PhairPlay is the old protocol 1 and has none. */
    fun setSetting(context: Context, target: ComponentName, state: State, key: SettingKey, value: Any): Boolean {
        if (!state.supportsControl) return false
        val intent = when (value) {
            is Boolean -> AirPlayProtocol.encodeSet(target, key, value)
            is Int -> AirPlayProtocol.encodeSet(target, key, value)
            is String -> AirPlayProtocol.encodeSet(target, key, value, state.settings?.displayNameMax ?: AirPlayProtocol.DISPLAY_NAME_MAX)
            else -> throw IllegalArgumentException("unsupported value ${value::class.simpleName}")
        }
        return send(context, intent)
    }

    fun transport(context: Context, target: ComponentName, state: State, command: AirPlayProtocol.Transport): Boolean =
        state.supportsControl && send(context, AirPlayProtocol.encodeTransport(target, command))

    private fun send(context: Context, intent: Intent): Boolean =
        !RuntimeSafety.isolated && runCatching { context.sendBroadcast(intent, AirPlayProtocol.PERMISSION) }.isSuccess

    /** What a STATE event means for the UI's state; null for the other events. */
    fun stateOf(event: Event): State? = (event as? Event.State)?.let { State(it.running, it.sender, it.protocol, it.settings) }

    /**
     * Listens for PhairPlay's [actions] until the returned function is called. Registered with the signature permission,
     * so only a holder of it (PhairPlay, signed with Glass's key) can deliver; on Android 14+ the sending package is
     * checked too ([AirPlayEventReceiver]). Older systems rely on the permission and Glass's own signature check of the
     * installed PhairPlay ([receiver]).
     */
    fun register(context: Context, actions: List<String> = AirPlayProtocol.EVENT_ACTIONS, onEvent: (Event) -> Unit): () -> Unit {
        if (RuntimeSafety.isolated) return {}
        val listener = AirPlayEventReceiver(onEvent)
        val filter = IntentFilter().also { f -> actions.forEach(f::addAction) }
        ContextCompat.registerReceiver(context, listener, filter, AirPlayProtocol.PERMISSION, null, ContextCompat.RECEIVER_EXPORTED)
        return { runCatching { context.unregisterReceiver(listener) } }
    }

    /** A sender is trusted when the system could not say who it was (before Android 14) or it is a PhairPlay package. */
    fun trusted(sentFrom: String?): Boolean = sentFrom == null || sentFrom in PACKAGES

    @Volatile private var hubInstance: AirPlayHub? = null

    /** The one hub for the process: it listens to PhairPlay from [start] on, whichever screen is up. */
    fun hub(context: Context): AirPlayHub =
        hubInstance ?: synchronized(this) { hubInstance ?: AirPlayHub(context.applicationContext).also { hubInstance = it } }

    /** The hub and its latest cleaned state, for a screen. It asks PhairPlay again each time [active] resumes, so a missed event is repaired. */
    @Composable
    fun rememberAirPlay(active: Boolean): Pair<AirPlayUi, AirPlayHub> {
        val context = LocalContext.current
        val hub = remember { hub(context) }
        val ui by hub.ui.collectAsState()
        val lifecycle = androidx.lifecycle.compose.LocalLifecycleOwner.current.lifecycle
        DisposableEffect(hub, active, lifecycle) {
            if (!active) return@DisposableEffect onDispose { }
            val observer = androidx.lifecycle.LifecycleEventObserver { _, e -> if (e == androidx.lifecycle.Lifecycle.Event.ON_RESUME) hub.resume() }
            lifecycle.addObserver(observer)
            hub.resume()
            onDispose { lifecycle.removeObserver(observer) }
        }
        return ui to hub
    }
}

/** Delivers a decoded event unless the sending package (Android 14+) is not PhairPlay. */
open class AirPlayEventReceiver(private val onEvent: (Event) -> Unit) : BroadcastReceiver() {
    /** The package that sent the broadcast, where the system tells us (API 34+), else null. */
    protected open fun origin(): String? = if (android.os.Build.VERSION.SDK_INT >= 34) sentFromPackage else null

    override fun onReceive(c: Context, intent: Intent) {
        if (!AirPlay.trusted(origin())) return
        AirPlayProtocol.decode(intent)?.let(onEvent)
    }
}

/** How the hub reaches PhairPlay: look it up, send it an intent, hear its events. A fake in tests. */
interface AirPlayLink {
    /** May block (package manager): the hub calls it off the main thread. */
    fun target(): ComponentName?
    fun send(intent: Intent): Boolean
    /** [onPackages] runs when a PhairPlay package was installed, replaced or removed. */
    fun listen(onEvent: (Event) -> Unit, onPackages: () -> Unit = {})
}

private class ContextLink(private val app: Context) : AirPlayLink {
    private class Found(val target: ComponentName?)
    @Volatile private var found: Found? = null

    // The package manager is asked once and again only after a PhairPlay package changes, not on every resume.
    override fun target(): ComponentName? = (found ?: Found(AirPlay.receiver(app)).also { found = it }).target
    override fun send(intent: Intent) = !RuntimeSafety.isolated && runCatching { app.sendBroadcast(intent, AirPlayProtocol.PERMISSION) }.isSuccess
    override fun listen(onEvent: (Event) -> Unit, onPackages: () -> Unit) {
        if (RuntimeSafety.isolated) return
        AirPlay.register(app, onEvent = onEvent)
        val filter = IntentFilter().apply {
            addAction(Intent.ACTION_PACKAGE_ADDED); addAction(Intent.ACTION_PACKAGE_REMOVED)
            addAction(Intent.ACTION_PACKAGE_REPLACED); addAction(Intent.ACTION_PACKAGE_CHANGED); addDataScheme("package")
        }
        ContextCompat.registerReceiver(app, object : BroadcastReceiver() {
            override fun onReceive(c: Context, i: Intent) {
                if (i.data?.schemeSpecificPart !in AirPlay.PACKAGES) return
                found = null
                onPackages()
            }
        }, filter, ContextCompat.RECEIVER_NOT_EXPORTED)
    }
}

/**
 * The process-wide AirPlay link: listens to PhairPlay, folds events through [AirPlayReducer] and publishes the result.
 * Screens read [ui]; none keeps its own copy. The package lookup runs on [background]; everything else on the main
 * thread ([main]), which is where broadcasts arrive.
 */
class AirPlayHub(
    private val link: AirPlayLink,
    clock: () -> Long = android.os.SystemClock::elapsedRealtime,
    private val background: java.util.concurrent.Executor = java.util.concurrent.Executors.newSingleThreadExecutor { r -> Thread(r, "airplay-hub").apply { isDaemon = true } },
    private val main: (Runnable) -> Unit = android.os.Handler(android.os.Looper.getMainLooper())::post,
) {
    constructor(app: Context) : this(ContextLink(app))

    private val reducer = AirPlayReducer(clock)
    private val handler by lazy { android.os.Handler(android.os.Looper.getMainLooper()) }
    private val _ui = MutableStateFlow(AirPlayUi())
    val ui: StateFlow<AirPlayUi> = _ui
    private var target: ComponentName? = null
    private val started = java.util.concurrent.atomic.AtomicBoolean(false)

    /** Process start: listen for good and ask for the state. Safe to call from any thread, any number of times. */
    fun start() {
        if (RuntimeSafety.isolated) return
        if (!started.compareAndSet(false, true)) return
        background.execute {
            link.listen({ e -> main { publish(reducer.onEvent(e)) } }, { resume() })
            resolve()
        }
    }

    /** A screen resumed (or the link may have come back): find PhairPlay again and ask for its state, so a missed event is repaired. */
    fun resume() {
        if (!started.get()) { start(); return }
        background.execute { resolve() }
    }

    private fun resolve() {
        val t = link.target()
        main {
            target = t
            publish(reducer.setInstalled(t != null))
            t?.let { link.send(AirPlayProtocol.encodeQuery(it)) }
        }
    }

    fun setOn(on: Boolean) { target?.let { link.send(AirPlayProtocol.encodeToggle(it, on)) } }

    /** Sends one setting. Nothing changes here until PhairPlay answers with its STATE. */
    fun setSetting(key: AirPlayProtocol.SettingKey, value: Any): Boolean {
        val t = target ?: return false
        val ui = _ui.value
        if (ui.availability != AirPlayAvailability.Ready) return false
        val intent = runCatching {
            when (value) {
                is Boolean -> AirPlayProtocol.encodeSet(t, key, value)
                is Int -> AirPlayProtocol.encodeSet(t, key, value)
                is String -> AirPlayProtocol.encodeSet(t, key, value, ui.settings?.displayNameMax ?: AirPlayProtocol.DISPLAY_NAME_MAX)
                else -> null
            }
        }.getOrNull() ?: return false
        return link.send(intent)
    }

    fun playPause() {
        val t = target ?: return
        val ui = _ui.value
        val playing = ui.track?.playing ?: return
        if (ui.availability != AirPlayAvailability.Ready) return
        val cmd = if (playing) AirPlayProtocol.Transport.Pause else AirPlayProtocol.Transport.Play
        if (link.send(AirPlayProtocol.encodeTransport(t, cmd))) publish(reducer.onTransport(!playing))
    }

    /** Back on the pairing card for [code]: a newer code that replaced it stays up. */
    fun dismissPairing(code: String) { if (_ui.value.pairingCode == code) publish(reducer.dismissPairing()) }

    /** Drops what has expired; runs by itself when a TTL ends. */
    fun tick() = publish(reducer.tick())

    /** While a track is shown PhairPlay is asked again now and then: its replay renews the track, and silence lets it expire. */
    fun heartbeat() {
        target?.let { link.send(AirPlayProtocol.encodeQuery(it)) }
        if (_ui.value.track != null) schedule(HEARTBEAT_MS, beat)
    }

    /** Milliseconds the pairing card has left, for a banner that shows the code elsewhere. */
    fun pairingRemainingMs(): Long? = reducer.pairingRemaining()

    private fun publish(ui: AirPlayUi) {
        _ui.value = ui
        runCatching {
            handler.removeCallbacks(expire)
            reducer.nextWake()?.let { handler.postDelayed(expire, it + 10) }
            handler.removeCallbacks(beat)
            if (ui.track != null) handler.postDelayed(beat, HEARTBEAT_MS)
        }
    }

    private fun schedule(ms: Long, r: Runnable) = runCatching { handler.removeCallbacks(r); handler.postDelayed(r, ms) }

    private val expire = Runnable { tick() }
    private val beat = Runnable { heartbeat() }

    companion object { const val HEARTBEAT_MS = 5 * 60_000L }
}
