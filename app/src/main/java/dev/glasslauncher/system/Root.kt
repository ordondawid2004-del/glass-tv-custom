package dev.glasslauncher.system

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.TimeUnit

/**
 * Root access (Magisk's su), for the Root section of Settings and the Control Center root buttons.
 * Everything here is opt-in, reversible, and logged to `files/root-actions.log` (Settings › Root ›
 * Log). On devices without su all of it stays hidden.
 */
object Root {
    data class Result(val code: Int, val out: String) { val ok get() = code == 0 }

    @Volatile private var granted: Boolean? = null

    /**
     * Whether su exists and grants root to Glass. Cached after the first check (~100 ms).
     *
     * The path list is only a cheap pre-check, and it is NOT sufficient on this build: `/debug_ramdisk/su` is a Magisk
     * mount that a normal app cannot see (only a `--mount-master` shell can), so every path test fails while su works
     * perfectly. The authoritative test is running `id` and reading `uid=0` - if that works, su exists.
     */
    suspend fun available(): Boolean {
        if (RuntimeSafety.isolated) return false
        granted?.let { return it }
        val ok = withContext(Dispatchers.IO) { withTimeoutOrNull(4_000) { exec("id").out.contains("uid=0") } } == true
        granted = ok
        return ok
    }

    /** Checks again, ignoring the cached answer (root granted or revoked since). */
    suspend fun recheck(): Boolean { granted = null; return available() }

    /** Last known result of [available] without checking (false until checked). */
    val known: Boolean get() = !RuntimeSafety.isolated && granted == true

    /**
     * A file only root can read, as bytes (null if unreadable). Glass writes any copy itself, so the copy carries
     * Glass's own SELinux label rather than su's.
     *
     * Read through the one persistent shell (P301): a fresh `su` per file made Magisk toast "granted superuser
     * rights" once per tile. base64 keeps the bytes intact over the line protocol, which is text.
     */
    suspend fun readBytes(path: String): ByteArray? = withContext(Dispatchers.IO) {
        val quoted = "'" + path.replace("'", "'\\''") + "'"
        val r = Shell.run("base64 -w0 $quoted 2>/dev/null", 20_000)
        if (!r.ok) return@withContext null
        runCatching { android.util.Base64.decode(r.out, android.util.Base64.DEFAULT) }.getOrNull()?.takeIf { it.isNotEmpty() }
    }

    /**
     * Reads a file only root can read into [block] (a zip reader, say). The file is held whole in memory, which is
     * fine for what this is used on (icon-pack manifests); it goes through the persistent shell for the same reason
     * as [readBytes].
     */
    suspend fun <T> streamFile(path: String, block: (java.io.InputStream) -> T): T? =
        readBytes(path)?.let { bytes -> runCatching { block(java.io.ByteArrayInputStream(bytes)) }.getOrNull() }

    /**
     * Runs [command] as root with the bytes of [input] on its standard input (`pm install -S size` reads the package
     * there), so no file name is ever part of the command. Returns its exit code and combined output.
     */
    suspend fun runWithInput(command: String, input: File, timeoutSeconds: Long = 180): Result =
        withContext(Dispatchers.IO) { runProcess(listOf("su", "-c", command), input, timeoutSeconds * 1000) }

    /** Code 124 = timed out or interrupted (the process may have done its work); 127 = it never started. */
    internal fun runProcess(cmd: List<String>, input: File, timeoutMs: Long): Result {
        if (RuntimeSafety.isolated) return Result(126, RuntimeSafety.reason)
        val p = try { ProcessBoundary.start(cmd) } catch (e: Exception) { return Result(127, e.message ?: "su failed") }
        // Both pipes run on their own threads so the timeout below can fire while the process is stuck on either.
        val out = StringBuilder()
        val reader = Thread { runCatching { p.inputStream.bufferedReader().forEachLine { synchronized(out) { out.appendLine(it) } } } }.apply { start() }
        val writer = Thread { runCatching { input.inputStream().use { src -> p.outputStream.use { dst -> src.copyTo(dst) } } } }.apply { start() }
        var interrupted = false
        val done = try { p.waitFor(timeoutMs, TimeUnit.MILLISECONDS) } catch (_: InterruptedException) { interrupted = true; false }
        if (!done) p.destroyForcibly()
        try { writer.join(2_000); reader.join(2_000) } catch (_: InterruptedException) { interrupted = true }
        if (interrupted) Thread.currentThread().interrupt()
        val text = synchronized(out) { out.toString().trim() }
        return if (done) Result(p.exitValue(), text) else Result(124, text)
    }

    /** Runs [script] as root; returns its exit code and combined output. */
    suspend fun run(script: String): Result = withContext(Dispatchers.IO) { exec(script) }

    /**
     * One long-lived `su` for every root command (P299). Magisk pops "granted superuser rights" for each *new* su
     * process, and a fresh one per command had the toast firing over and over; a single session toasts once. It is
     * also much cheaper for the repeated calls (the boot reapply, memory tuning, the Control Center root buttons).
     *
     * A line-delimited protocol: each command is followed by `echo MARK:$?`, and the reader thread hands lines back
     * through a queue so a command can time out without the process being killed by a stuck read.
     */
    private object Shell {
        private const val EOF = "\u0000__glass_shell_eof__"
        private val lock = java.util.concurrent.locks.ReentrantLock()
        private var proc: Process? = null
        private var writer: java.io.BufferedWriter? = null
        private var queue: java.util.concurrent.LinkedBlockingQueue<String>? = null
        private var seq = 0

        private fun start(): Process? = runCatching {
            val p = ProcessBoundary.start(listOf("su", "--mount-master"))
            val q = java.util.concurrent.LinkedBlockingQueue<String>()
            queue = q
            writer = p.outputStream.bufferedWriter()
            Thread {
                runCatching {
                    p.inputStream.bufferedReader().forEachLine { q.put(it) }
                }
                runCatching { q.put(EOF) }
            }.apply { isDaemon = true; name = "glass-su-reader"; start() }
            p
        }.getOrNull()

        private fun stop() {
            runCatching { writer?.close() }
            runCatching { proc?.destroy() }
            proc = null; writer = null; queue = null
        }

        fun run(script: String, timeoutMs: Long): Result {
            if (RuntimeSafety.isolated) return Result(126, RuntimeSafety.reason)
            lock.lock()
            try {
                if (proc?.isAlive != true) { stop(); proc = start() }
                val p = proc ?: return Result(127, "su failed")
                val q = queue ?: return Result(127, "su failed")
                val w = writer ?: return Result(127, "su failed")
                val n = ++seq
                val mark = "__GLASS_$n"
                runCatching {
                    w.write(script); w.write("\n")
                    w.write("echo $mark:\$?\n")
                    w.flush()
                }.onFailure { stop(); return Result(127, "su write failed") }

                val out = StringBuilder()
                val deadline = System.currentTimeMillis() + timeoutMs
                while (true) {
                    val left = deadline - System.currentTimeMillis()
                    if (left <= 0) { stop(); return Result(124, out.toString().trim()) }
                    val line = q.poll(left, TimeUnit.MILLISECONDS) ?: run { stop(); return Result(124, out.toString().trim()) }
                    if (line == EOF) { stop(); return Result(127, out.toString().trim().ifBlank { "su exited" }) }
                    if (line.startsWith("$mark:")) {
                        // Anything older than this command's marker is leftover from a killed one: keep only what follows it.
                        val code = line.removePrefix("$mark:").trim().toIntOrNull() ?: 0
                        return Result(code, out.toString().trim())
                    }
                    out.appendLine(line)
                }
            } finally {
                lock.unlock()
            }
        }
    }

    private fun exec(script: String): Result = Shell.run(script, 20_000)

    /** Runs a root action and records it (what, and whether it worked) in the log. */
    suspend fun action(context: Context, what: String, script: String): Result {
        val r = run(script)
        log(context, "$what: ${if (r.ok) "ok" else "failed (${r.code}) ${r.out.take(200)}"}")
        return r
    }

    fun log(context: Context, line: String) {
        runCatching {
            val f = logFile(context)
            if (f.length() > 256 * 1024) f.writeText(f.readLines().takeLast(500).joinToString("\n") + "\n")
            f.appendText("${SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.US).format(Date())}  $line\n")
        }
    }

    fun readLog(context: Context): List<String> = runCatching { logFile(context).readLines().takeLast(60).reversed() }.getOrDefault(emptyList())

    private fun logFile(context: Context) = File(context.filesDir, "root-actions.log")
}
