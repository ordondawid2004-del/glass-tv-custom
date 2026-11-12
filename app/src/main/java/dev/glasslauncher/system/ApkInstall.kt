package dev.glasslauncher.system

import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.content.pm.PackageManager
import android.os.Build
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import java.io.File
import java.util.concurrent.ConcurrentHashMap

sealed interface InstallResult {
    data object Success : InstallResult
    /** The user has to act first (allow unknown sources for Glass); start [settings], then call install again. */
    data class NeedsUserAction(val settings: Intent) : InstallResult
    data class Failed(val reason: String) : InstallResult
}

enum class InstallMode { Apk, Pro, Os }

/** One-press install of a downloaded APK, by what this device lets Glass do. */
object ApkInstall {
    private val waiting = ConcurrentHashMap<Int, CompletableDeferred<InstallResult>>()

    internal val waitingForTest get() = waiting

    /** Glass OS (privileged INSTALL_PACKAGES) installs silently, Glass Pro (root) through su, else the system card. */
    internal fun chooseMode(hasInstallPackages: Boolean, root: Boolean) = when {
        hasInstallPackages -> InstallMode.Os
        root -> InstallMode.Pro
        else -> InstallMode.Apk
    }

    suspend fun mode(context: Context): InstallMode {
        val perm = context.checkSelfPermission("android.permission.INSTALL_PACKAGES") == PackageManager.PERMISSION_GRANTED
        return chooseMode(perm, !perm && (Root.known || Root.available()))
    }

    /** What [install] needs from Android and su, so the order of events can be tested with fakes. */
    internal interface Edges {
        suspend fun mode(): InstallMode
        fun canInstall(): Boolean
        fun unknownSources(): Intent
        suspend fun rootInstall(file: File): Root.Result
        fun installed(pkg: String): Installed?
        fun now(): Long
        suspend fun commit(file: File, userAction: Boolean): InstallResult
        fun hash(file: File): String
        fun log(line: String)
    }

    private fun edges(context: Context) = object : Edges {
        override suspend fun mode() = mode(context)
        override fun canInstall() = Updater.canInstall(context)
        override fun unknownSources() = Updater.unknownSourcesIntent(context)
        override suspend fun rootInstall(file: File) = Root.runWithInput("pm install -S ${file.length()} -r", file)
        override fun installed(pkg: String) = runCatching { context.packageManager.getPackageInfo(pkg, 0).let { Installed(it.longVersionCode, it.lastUpdateTime) } }.getOrNull()
        override fun now() = System.currentTimeMillis()
        override suspend fun commit(file: File, userAction: Boolean) = commit(context, file, userAction)
        override fun hash(file: File) = StagedApk.hash(file)
        override fun log(line: String) = Root.log(context, line)
    }

    /**
     * Suspends until the install ends; in APK mode that includes the user's answer on the system card. Installs
     * exactly the staged copy, and refuses if it no longer matches the hash the card was shown for.
     */
    suspend fun install(context: Context, staged: StagedApk): InstallResult {
        if (RuntimeSafety.isolated) return InstallResult.Failed(RuntimeSafety.reason)
        return install(edges(context), staged).also { if (it !is InstallResult.NeedsUserAction) staged.discard() }
    }

    internal data class Installed(val versionCode: Long, val updatedAt: Long)

    /** Whether the staged version landed after [since]: version code and update time, polled while pm may still be finishing. */
    internal suspend fun landed(e: Edges, info: ApkInfo, since: Long, attempts: Int, pollMs: Long): Boolean {
        repeat(attempts) {
            e.installed(info.pkg)?.let { if (it.versionCode == info.versionCode && it.updatedAt >= since) return true }
            delay(pollMs)
        }
        return false
    }

    internal suspend fun install(e: Edges, staged: StagedApk, attempts: Int = 5, pollMs: Long = 1_000): InstallResult {
        if (RuntimeSafety.isolated) return InstallResult.Failed(RuntimeSafety.reason)
        // Hashing a whole APK is disk work; the caller may be on Main.
        suspend fun intact() = withContext(Dispatchers.IO) { runCatching { e.hash(staged.file) == staged.sha256 }.getOrDefault(false) }
        val changed = InstallResult.Failed("The file changed after it was checked")
        if (!intact()) return changed
        val mode = e.mode()
        if (mode == InstallMode.Apk && !e.canInstall()) return InstallResult.NeedsUserAction(e.unknownSources())
        if (mode == InstallMode.Pro) {
            // No -d: a downgrade needs the user's own uninstall, not a silent one.
            val since = e.now()
            val r = e.rootInstall(staged.file)
            e.log("Install ${staged.info.pkg}: ${if (r.ok) "ok" else "failed (${r.code}) ${r.out.take(200)}"}")
            when (val o = fromRoot(r.code, r.out)) {
                is RootOutcome.Done -> return o.result
                // su may have finished the install after we stopped listening: look, never install a second time.
                RootOutcome.Unknown -> return if (landed(e, staged.info, since, attempts, pollMs)) InstallResult.Success
                    else InstallResult.Failed("The install didn't report back")
                RootOutcome.NoSu -> if (!e.canInstall()) return InstallResult.NeedsUserAction(e.unknownSources())
            }
            if (!intact()) return changed
        }
        return e.commit(staged.file, userAction = mode != InstallMode.Os)
    }

    internal sealed interface RootOutcome {
        data class Done(val result: InstallResult) : RootOutcome
        /** Nothing ran (no su, or it was denied): the PackageInstaller path is safe. */
        data object NoSu : RootOutcome
        /** Timed out or said nothing: pm may or may not have installed. */
        data object Unknown : RootOutcome
    }

    internal fun fromRoot(code: Int, out: String): RootOutcome = when {
        code == 0 && out.contains("Success") -> RootOutcome.Done(InstallResult.Success)
        code == 127 -> RootOutcome.NoSu
        code == 124 || (code == 0 && out.isBlank()) -> RootOutcome.Unknown
        out.isBlank() -> RootOutcome.NoSu
        else -> RootOutcome.Done(InstallResult.Failed(out.lines().lastOrNull { it.isNotBlank() }.orEmpty().removePrefix("Failure ").trim()))
    }

    /** Null while the install is still waiting on the user (PENDING_USER_ACTION). */
    internal fun fromStatus(status: Int, message: String?): InstallResult? = when (status) {
        PackageInstaller.STATUS_SUCCESS -> InstallResult.Success
        PackageInstaller.STATUS_PENDING_USER_ACTION -> null
        PackageInstaller.STATUS_FAILURE_ABORTED -> InstallResult.Failed("Cancelled")
        PackageInstaller.STATUS_FAILURE_CONFLICT -> InstallResult.Failed("Conflicts with an installed app (different signature?)")
        PackageInstaller.STATUS_FAILURE_INCOMPATIBLE -> InstallResult.Failed("Not compatible with this device")
        PackageInstaller.STATUS_FAILURE_STORAGE -> InstallResult.Failed("Not enough storage")
        PackageInstaller.STATUS_FAILURE_INVALID -> InstallResult.Failed("The file is not a valid APK")
        else -> InstallResult.Failed(message?.takeIf { it.isNotBlank() } ?: "Install failed ($status)")
    }

    private const val ANSWER_TIMEOUT_MS = 10 * 60_000L

    /** The session's result, or null if none came in [ms] (the caller abandons the session). */
    internal suspend fun awaitResult(done: CompletableDeferred<InstallResult>, ms: Long): InstallResult? =
        withTimeoutOrNull(ms) { done.await() } ?: if (done.isCompleted) done.await() else null // a result that raced the timeout wins

    /** Called by [InstallResultReceiver] for every status of a session. */
    internal fun onStatus(sessionId: Int, status: Int, message: String?) {
        val result = fromStatus(status, message) ?: return
        waiting.remove(sessionId)?.complete(result)
    }

    /** The PackageInstaller commit shared with [Updater] (which wants no card where the system allows it). */
    internal suspend fun commit(context: Context, file: File, userAction: Boolean, await: Boolean = true): InstallResult = withContext(Dispatchers.IO) {
        if (RuntimeSafety.isolated) return@withContext InstallResult.Failed(RuntimeSafety.reason)
        val installer = context.packageManager.packageInstaller
        val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL)
        if (Build.VERSION.SDK_INT >= 31) params.setRequireUserAction(
            if (userAction) PackageInstaller.SessionParams.USER_ACTION_REQUIRED else PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED,
        )
        val id = installer.createSession(params)
        val done = CompletableDeferred<InstallResult>().also { if (await) waiting[id] = it }
        try {
            installer.openSession(id).use { session ->
                session.openWrite("install.apk", 0, file.length()).use { out -> file.inputStream().use { it.copyTo(out) }; session.fsync(out) }
                val intent = Intent(context, InstallResultReceiver::class.java)
                val flags = PendingIntent.FLAG_UPDATE_CURRENT or (if (Build.VERSION.SDK_INT >= 31) PendingIntent.FLAG_MUTABLE else 0)
                session.commit(PendingIntent.getBroadcast(context, id, intent, flags).intentSender)
            }
            if (await) awaitResult(done, ANSWER_TIMEOUT_MS) ?: run { installer.abandonSession(id); InstallResult.Failed("Timed out waiting for the installer") } else InstallResult.Success
        } catch (e: Exception) {
            runCatching { installer.abandonSession(id) }
            if (e is kotlinx.coroutines.CancellationException) throw e
            InstallResult.Failed(e.message ?: "Install failed")
        } finally {
            waiting.remove(id)
        }
    }
}
