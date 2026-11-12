package dev.glasslauncher.system

import android.content.ContentValues
import android.content.Context
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File

/** Saves the layout and settings as JSON in Downloads so it survives a reinstall or moves to another TV. */
object Backup {
    const val FILE_NAME = "glass-launcher-backup.json"

    suspend fun export(context: Context, text: String): String = withContext(Dispatchers.IO) {
        if (RuntimeSafety.isolated) throw IllegalStateException(RuntimeSafety.reason)
        if (Build.VERSION.SDK_INT >= 29) {
            val resolver = context.contentResolver
            val collection = MediaStore.Downloads.EXTERNAL_CONTENT_URI
            findOwn(context)?.let { resolver.delete(it, null, null) }
            val values = ContentValues().apply {
                put(MediaStore.Downloads.DISPLAY_NAME, FILE_NAME)
                put(MediaStore.Downloads.MIME_TYPE, "application/json")
                put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS)
            }
            val uri = resolver.insert(collection, values) ?: error("Couldn't create the backup file")
            resolver.openOutputStream(uri)!!.use { it.write(text.toByteArray()) }
        } else {
            @Suppress("DEPRECATION")
            File(Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS), FILE_NAME).writeText(text)
        }
        "Download/$FILE_NAME"
    }

    suspend fun read(context: Context): String? = withContext(Dispatchers.IO) {
        if (RuntimeSafety.isolated) return@withContext null
        if (Build.VERSION.SDK_INT >= 29) {
            findOwn(context)?.let { uri -> context.contentResolver.openInputStream(uri)?.use { String(it.readBytes()) } }
        } else {
            @Suppress("DEPRECATION")
            File(Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS), FILE_NAME).takeIf { it.exists() }?.readText()
        }
    }

    suspend fun readUri(context: Context, uri: android.net.Uri): String? = withContext(Dispatchers.IO) {
        if (RuntimeSafety.isolated) return@withContext null
        runCatching { context.contentResolver.openInputStream(uri)?.use { String(it.readBytes()) } }.getOrNull()
    }

    private fun findOwn(context: Context): android.net.Uri? {
        if (Build.VERSION.SDK_INT < 29) return null
        val collection = MediaStore.Downloads.EXTERNAL_CONTENT_URI
        return context.contentResolver.query(
            collection, arrayOf(MediaStore.Downloads._ID),
            "${MediaStore.Downloads.DISPLAY_NAME} = ?", arrayOf(FILE_NAME), null,
        )?.use { c -> if (c.moveToFirst()) android.content.ContentUris.withAppendedId(collection, c.getLong(0)) else null }
    }
}
