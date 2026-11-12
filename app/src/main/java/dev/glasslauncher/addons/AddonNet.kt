package dev.glasslauncher.addons

import android.content.Context
import coil3.intercept.Interceptor
import dev.glasslauncher.net.PublicHosts
import dev.glasslauncher.net.PublicHttp
import coil3.request.ErrorResult
import coil3.request.ImageResult
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.Dns
import okhttp3.OkHttpClient
import okhttp3.Request
import okio.BufferedSource
import java.io.File
import java.io.IOException
import java.net.InetAddress
import java.net.UnknownHostException
import java.security.MessageDigest
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.TimeUnit

/** Which hosts an add-on's art may come from: the shared public-hosts rule ([PublicHosts]). */
object AddonHosts {
    fun blockedName(host: String) = PublicHosts.blockedName(host)
    fun isPrivate(a: java.net.InetAddress) = PublicHosts.isPrivate(a)
}

/** DNS for add-on art: refuses any name that resolves to a private address, on every hop. */
typealias SafeDns = dev.glasslauncher.net.SafeDns

/** Static pictures only: what format and size a fetched file is, from its header, without decoding it. */
object ArtFormat {
    const val MAX_SIDE = 1280
    const val MAX_BYTES = 2 * 1024 * 1024
    class Info(val format: String, val width: Int, val height: Int)

    /** JPEG, PNG or WebP that is not animated (no APNG, no animated WebP, no GIF) and fits [MAX_SIDE]; null otherwise. */
    fun inspect(b: ByteArray): Info? {
        val i = when {
            isPng(b) -> png(b)
            isJpeg(b) -> jpeg(b)
            isWebp(b) -> webp(b)
            else -> null
        } ?: return null
        return i.takeIf { it.width in 1..MAX_SIDE && it.height in 1..MAX_SIDE }
    }

    private fun u8(b: ByteArray, i: Int) = if (i in b.indices) b[i].toInt() and 0xFF else -1
    private fun be16(b: ByteArray, i: Int) = (u8(b, i) shl 8) or u8(b, i + 1)
    private fun be32(b: ByteArray, i: Int) = (be16(b, i).toLong() shl 16 or be16(b, i + 2).toLong()).toInt()
    private fun le16(b: ByteArray, i: Int) = u8(b, i) or (u8(b, i + 1) shl 8)
    private fun le24(b: ByteArray, i: Int) = u8(b, i) or (u8(b, i + 1) shl 8) or (u8(b, i + 2) shl 16)
    private fun tag(b: ByteArray, i: Int, s: String) = s.indices.all { u8(b, i + it) == s[it].code }

    private fun isPng(b: ByteArray) = b.size > 33 && u8(b, 0) == 0x89 && tag(b, 1, "PNG") && u8(b, 4) == 0x0D && u8(b, 5) == 0x0A && u8(b, 6) == 0x1A && u8(b, 7) == 0x0A
    private fun isJpeg(b: ByteArray) = b.size > 4 && u8(b, 0) == 0xFF && u8(b, 1) == 0xD8
    private fun isWebp(b: ByteArray) = b.size >= 20 && tag(b, 0, "RIFF") && tag(b, 8, "WEBP")

    private fun png(b: ByteArray): Info? {
        var i = 8
        var w = 0; var h = 0
        while (i + 8 <= b.size) {
            val len = be32(b, i)
            if (len < 0) return null
            if (tag(b, i + 4, "IHDR")) { w = be32(b, i + 8); h = be32(b, i + 12) }
            if (tag(b, i + 4, "acTL")) return null // APNG
            if (tag(b, i + 4, "IDAT")) break
            i += 12 + len
        }
        return if (w > 0 && h > 0) Info("png", w, h) else null
    }

    private fun jpeg(b: ByteArray): Info? {
        var i = 2
        while (i + 4 < b.size) {
            if (u8(b, i) != 0xFF) return null
            var m = u8(b, i + 1)
            while (m == 0xFF) { i++; m = u8(b, i + 1) }
            if (m == 0xD8 || m == 0x01 || m in 0xD0..0xD7) { i += 2; continue }
            if (m in 0xC0..0xCF && m != 0xC4 && m != 0xC8 && m != 0xCC) return Info("jpeg", be16(b, i + 7), be16(b, i + 5))
            if (m == 0xD9 || m == 0xDA) return null
            i += 2 + be16(b, i + 2)
        }
        return null
    }

    private fun webp(b: ByteArray): Info? {
        var i = 12
        while (i + 8 <= b.size) {
            val size = le24(b, i + 4) or (u8(b, i + 7) shl 24)
            val d = i + 8
            when {
                tag(b, i, "ANIM") || tag(b, i, "ANMF") -> return null
                tag(b, i, "VP8X") -> { if (u8(b, d) and 0x02 != 0) return null; return Info("webp", le24(b, d + 4) + 1, le24(b, d + 7) + 1) }
                tag(b, i, "VP8L") -> if (u8(b, d) == 0x2F) return Info("webp", 1 + (u8(b, d + 1) or ((u8(b, d + 2) and 0x3F) shl 8)), 1 + ((u8(b, d + 2) shr 6) or (u8(b, d + 3) shl 2) or ((u8(b, d + 4) and 0x0F) shl 10)))
                tag(b, i, "VP8 ") -> if (u8(b, d + 3) == 0x9D && u8(b, d + 4) == 0x01 && u8(b, d + 5) == 0x2A) return Info("webp", le16(b, d + 6) and 0x3FFF, le16(b, d + 8) and 0x3FFF)
            }
            if (size < 0) return null
            i = d + size + (size and 1)
        }
        return null
    }
}

/** Reads at most [cap] bytes; null if the source has more. */
fun readCapped(source: BufferedSource, cap: Int): ByteArray? = PublicHttp.readCapped(source, cap)

/**
 * Fetches add-on art for Glass: its own OkHttp client (https only on every hop, no https-to-http redirects, public
 * addresses only, short timeouts), a 2 MB byte cap, a static-bitmap-only and 1280 px check from the header before anything
 * is decoded. The add-on never sends pixels. Decoding then happens at the shown size.
 */
object AddonArt {
    private const val TTL_MS = 6 * 3_600_000L

    /** URLs that came from an add-on, by package (replaced on each read): the image loader sends exactly these through [fetch]. */
    private val urls = ConcurrentHashMap<String, Set<String>>()

    fun allow(pkg: String, art: Collection<String>) { if (art.isEmpty()) urls.remove(pkg) else urls[pkg] = art.toSet() }
    fun forget(pkg: String) { urls.remove(pkg) }
    fun isAddonArt(url: String) = urls.values.any { url in it }

    fun client(dns: Dns = SafeDns()): OkHttpClient = PublicHttp.client(dns)

    private val http by lazy { client() }

    /** The bytes of a valid static picture at [url], or null. Cached on disk for a few hours. */
    suspend fun fetch(context: Context, url: String): ByteArray? = withContext(Dispatchers.IO) {
        if (dev.glasslauncher.system.RuntimeSafety.isolated) return@withContext null
        if (AddonLinks.image(url) == null) return@withContext null
        val dir = File(context.cacheDir, "addon-art")
        val file = File(dir, MessageDigest.getInstance("SHA-256").digest(url.toByteArray()).joinToString("") { "%02x".format(it) })
        if (file.exists() && System.currentTimeMillis() - file.lastModified() < TTL_MS) file.readBytes().takeIf { ArtFormat.inspect(it) != null }?.let { return@withContext it }
        val bytes = try {
            http.newCall(Request.Builder().url(url).header("Accept", "image/jpeg,image/png,image/webp").build()).execute().use { r ->
                val len = r.body.contentLength()
                if (!r.isSuccessful || len > ArtFormat.MAX_BYTES) null else readCapped(r.body.source(), ArtFormat.MAX_BYTES)
            }
        } catch (_: IOException) { null } ?: return@withContext null
        if (ArtFormat.inspect(bytes) == null) return@withContext null
        runCatching { dir.mkdirs(); file.writeBytes(bytes); trim(dir) }
        bytes
    }

    private fun trim(dir: File) {
        val files = dir.listFiles() ?: return
        if (files.size > 120) files.sortedBy { it.lastModified() }.take(files.size - 120).forEach { it.delete() }
    }
}

/** Coil: a URL that came from an add-on is fetched by [AddonArt] and handed over as bytes; anything it refuses is an error, never a fallback to the general client. */
class AddonArtInterceptor(private val context: Context) : Interceptor {
    override suspend fun intercept(chain: Interceptor.Chain): ImageResult {
        val url = chain.request.data as? String
        if (url == null || !AddonArt.isAddonArt(url)) return chain.proceed()
        val bytes = AddonArt.fetch(context, url) ?: return ErrorResult(null, chain.request, IOException("add-on art refused"))
        return chain.withRequest(chain.request.newBuilder().data(bytes).memoryCacheKey(url).diskCachePolicy(coil3.request.CachePolicy.DISABLED).build()).proceed()
    }
}
