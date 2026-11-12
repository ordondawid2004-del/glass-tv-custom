package dev.glasslauncher.data

import dev.glasslauncher.system.RuntimeSafety

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.flowOn
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json

private val Context.dataStore by preferencesDataStore("launcher")

/**
 * [store] is the process-wide DataStore by default. It is tied to the first Context that asks, so a test that needs its own files
 * (a full disk, a read-only folder) passes one.
 */
class ConfigStore(
    context: Context,
    scope: CoroutineScope,
    private val store: androidx.datastore.core.DataStore<androidx.datastore.preferences.core.Preferences> = context.dataStore,
) {

    private val key = stringPreferencesKey("config")

    private val flow = store.data.map { prefs -> decode(prefs[key]) }

    // Read once synchronously so the first frame already has the user's layout and wallpaper; later reads decode off Main (P159).
    val config: StateFlow<LauncherConfig> = flow.flowOn(Dispatchers.Default).stateIn(
        scope,
        SharingStarted.Eagerly,
        runBlocking { flow.first() },
    )

    /** How many times the config was written to disk (tests and measurements). */
    @Volatile var writes = 0
        private set

    /**
     * Writes the config. A disk that cannot take the write (full storage: ENOSPC) leaves the saved config as it was and says so in
     * the log; it must never crash the launcher, whose callers run on Main. Returns whether the write happened.
     */
    suspend fun update(transform: (LauncherConfig) -> LauncherConfig): Boolean {
        try {
            store.edit { prefs ->
                prefs[key] = json.encodeToString(LauncherConfig.serializer(), transform(decode(prefs[key])))
            }
        } catch (e: java.io.IOException) {
            android.util.Log.w("ConfigStore", "config not saved: ${e.message}")
            return false
        }
        writes++ // after the write: a reader that sees the count change can read the saved config
        return true
    }

    fun export(): String = json.encodeToString(LauncherConfig.serializer(), config.value)

    suspend fun import(text: String) {
        if (RuntimeSafety.isolated) throw IllegalStateException(RuntimeSafety.reason)
        val parsed = json.decodeFromString(LauncherConfig.serializer(), text)
        update { parsed }
    }

    private fun decode(text: String?): LauncherConfig =
        text?.let { runCatching { json.decodeFromString(LauncherConfig.serializer(), it) }.getOrNull() }
            ?: LauncherConfig()

    companion object {
        /** coerceInputValues: a stored enum value this build doesn't know (a renamed or removed option) falls back to the field's default instead of failing the whole decode, which would reset every setting. */
        val json = Json { ignoreUnknownKeys = true; coerceInputValues = true; encodeDefaults = true; prettyPrint = true }
    }
}
