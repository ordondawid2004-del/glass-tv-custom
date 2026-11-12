package dev.glasslauncher.system

import dev.glasslauncher.BuildConfig

/** Build-time boundary, independent of preferences, imports, permissions and root availability. */
object RuntimeSafety {
    const val isolated = BuildConfig.ISOLATED_DEVELOPMENT
    const val reason = "In der isolierten Entwicklungsvariante gesperrt"

    fun requireUnrestricted() {
        check(!isolated) { reason }
    }

    /** Canonical paths also reject traversal and symlinks escaping the private sandbox. */
    fun privateFile(context: android.content.Context, file: java.io.File): Boolean = runCatching {
        val path = file.canonicalFile.toPath()
        listOf(context.filesDir, context.cacheDir).any { path.startsWith(it.canonicalFile.toPath()) }
    }.getOrDefault(false)
}

/** Single process creation boundary. Tests replace the factory, never start su. */
internal object ProcessBoundary {
    internal var factory: (List<String>) -> Process = { command ->
        ProcessBuilder(command).redirectErrorStream(true).start()
    }

    fun start(command: List<String>): Process {
        RuntimeSafety.requireUnrestricted()
        return factory(command)
    }
}
