package dev.glasslauncher.downloads.browser

import dev.glasslauncher.system.RuntimeSafety

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.Message
import android.os.Messenger
import android.os.SystemClock
import dev.glasslauncher.browserlink.Link
import dev.glasslauncher.browserlink.Msg
import dev.glasslauncher.data.DownloadsConfig
import dev.glasslauncher.downloads.DownloadPlanner
import dev.glasslauncher.downloads.Downloads
import dev.glasslauncher.downloads.Plan
import dev.glasslauncher.home.Overlay
import kotlinx.coroutines.MainScope
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.launch

/**
 * Glass's end of the Glass Browser (a separate, unprivileged APK, scripts/browser). Glass opens it by component and
 * hands it a Messenger; only that package, holding the signature permission, is listened to. It asks for downloads and
 * the keyboard, Glass applies its own rules, and the browser never writes the Downloads folder.
 */
object BrowserLink {
    private val component = ComponentName(Link.BROWSER_PACKAGE, Link.BROWSER_ACTIVITY)
    private const val TEXT_TTL_MS = 60_000L

    /** Something for Home to do for the browser: it has the settings and the overlay stack. */
    fun interface Ask { suspend fun run(context: Context, downloads: DownloadsConfig, open: (Overlay) -> Unit) }

    private val _asks = MutableSharedFlow<Ask>(extraBufferCapacity = 4)
    /** Collected by Home; with no collector a request is dropped, since nothing could show it. */
    val asks: SharedFlow<Ask> = _asks.asSharedFlow()

    // Main thread only (the handler's looper).
    private var app: Context? = null
    private val approved = HashSet<String>()
    private val gate = TextGate(TEXT_TTL_MS)
    private val handler = Handler(Looper.getMainLooper()) { m -> handle(m); true }
    private val messenger = Messenger(handler)

    /** Whether the browser is installed and Glass holds the permission, as the AirPlay row checks PhairPlay. */
    fun available(context: Context): Boolean {
        if (RuntimeSafety.isolated) return false
        val pm = context.packageManager
        return runCatching { pm.getActivityInfo(component, 0) }.isSuccess &&
            pm.checkPermission(Link.PERMISSION, context.packageName) == PackageManager.PERMISSION_GRANTED
    }

    /** Starts (or brings forward) the browser; [url] is opened when it is https, else the browser starts on its address bar. */
    fun open(context: Context, url: String = "") {
        if (RuntimeSafety.isolated) return
        app = context.applicationContext
        val intent = Intent().setComponent(component).putExtra(Link.EXTRA_LINK, messenger).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        if (Link.httpsHost(url) != null) intent.putExtra(Link.EXTRA_URL, url)
        runCatching { context.startActivity(intent) }
    }

    /** Glass is in front again after a keyboard or a card: show the page the user left, with no new URL. */
    private fun backToBrowser(c: Context) {
        if (RuntimeSafety.isolated) return
        runCatching { c.startActivity(Intent().setComponent(component).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
    }

    private fun trusted(c: Context, m: Message): Boolean {
        val pm = c.packageManager
        val uid = runCatching { pm.getPackageUid(Link.BROWSER_PACKAGE, 0) }.getOrNull()
        return Link.trusted(
            m.sendingUid, uid,
            pm.checkPermission(Link.PERMISSION, Link.BROWSER_PACKAGE) == PackageManager.PERMISSION_GRANTED,
            pm.checkSignatures(c.packageName, Link.BROWSER_PACKAGE) == PackageManager.SIGNATURE_MATCH,
        )
    }

    private fun handle(m: Message) {
        if (RuntimeSafety.isolated) return
        val c = app ?: return
        if (!trusted(c, m)) return
        val msg = runCatching { Msg.decode(m.what) { m.data?.getString(it) } }.getOrNull() ?: return
        when (msg) {
            is Msg.Download -> download(c, msg, m.replyTo)
            is Msg.TextRequest -> text(c, msg, m.replyTo)
            Msg.Closed -> gate.clear()
            else -> Unit
        }
    }

    private fun reply(to: Messenger?, msg: Msg) {
        if (RuntimeSafety.isolated) return
        val m = Message.obtain(null, msg.what)
        m.data = Bundle().also { b -> msg.fields().forEach { (k, v) -> b.putString(k, v) } }
        runCatching { to?.send(m) }
    }

    /** Hands [ask] to Home; false when Home isn't there to run it. The browser is told, so it never reports a success that didn't happen. */
    private fun post(ask: Ask, to: Messenger?, showGlass: Boolean): Boolean {
        val ok = _asks.subscriptionCount.value > 0 && _asks.tryEmit(ask)
        if (!ok) reply(to, Msg.Notice("VITRE can't show that right now"))
        else if (showGlass) reply(to, Msg.ShowGlass)
        return ok
    }

    /** Plans (the manager's link rules) and starts one browser download with the user's settings; tells the browser how it went. True when the plan was accepted. */
    private suspend fun startPlanned(context: Context, cfg: DownloadsConfig, to: Messenger?, url: String, name: String?, execOk: String): Boolean {
        val plan = DownloadPlanner.plan(url, emptyList(), cfg.acceptedUrls) { emptyList() }
        if (plan !is Plan.Start) { reply(to, Msg.Notice((plan as Plan.Refuse).message)); return false }
        runCatching { Downloads.start(context, cfg, plan.url, name, guardExec = true, execOk = execOk) }
            .onSuccess { reply(to, Msg.Notice("Downloading ${name ?: "the file"}")) }
            .onFailure { reply(to, Msg.Notice(it.message ?: "Couldn't start that download")) }
        return true
    }

    private fun download(c: Context, d: Msg.Download, to: Messenger?) {
        when (val r = BrowserDownload.decide(d, approved)) {
            // No Home to take it: refuse, never download around the planner and the user's settings.
            is BrowserDownload.Queue -> post({ ctx, cfg, _ -> startPlanned(ctx, cfg, to, r.url, r.name, r.execOk) }, to, showGlass = false)
            is BrowserDownload.Ask -> post({ _, cfg, open ->
                val from = if (r.referrer.isNotEmpty() && r.referrer != r.host) " (from a page on ${r.referrer})" else ""
                open(Overlay.Confirm("Download an app file?", "${r.host} is offering ${r.name ?: "a file"}$from. Only keep files from sites you trust.", "Download") {
                    // Remembered only for a link the planner accepted: a refused one earns no approval.
                    MainScope().launch { if (startPlanned(c, cfg, to, r.url, r.name, r.allow)) approved += r.key; backToBrowser(c) }
                })
            }, to, showGlass = true)
            is BrowserDownload.Refuse -> reply(to, Msg.Notice(r.reason))
        }
    }

    private fun text(c: Context, r: Msg.TextRequest, to: Messenger?) {
        val prompt = TextPrompt.of(r)
        if (prompt == null) { reply(to, Msg.Notice("VITRE can't type into that")); return }
        if (!gate.tryBegin(r.id, SystemClock.elapsedRealtime())) { reply(to, Msg.Notice("VITRE is waiting for the last keyboard")); return }
        val shown = post({ _, _, open ->
            open(Overlay.TextInput(
                prompt.title, prompt.initial, prompt.hint, isPassword = prompt.password, offerPhone = prompt.phoneOk,
                // Closing for any reason, a cancel included, frees the next request.
                onClosed = { gate.end(r.id) },
            ) { typed ->
                reply(to, Msg.TextResult(r.id, typed.take(Link.MAX_TEXT)))
                backToBrowser(c)
            })
        }, to, showGlass = true)
        if (!shown) gate.end(r.id)
    }
}
