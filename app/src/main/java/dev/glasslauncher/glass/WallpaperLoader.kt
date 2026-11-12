package dev.glasslauncher.glass

import android.content.Context
import android.graphics.Bitmap
import androidx.compose.ui.graphics.asAndroidBitmap
import androidx.compose.runtime.getValue
import androidx.compose.runtime.setValue
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RadialGradient
import android.graphics.Rect
import android.graphics.Shader
import android.net.Uri
import androidx.compose.ui.graphics.ImageBitmap
import coil3.request.allowHardware
import coil3.toBitmap
import androidx.compose.ui.graphics.asImageBitmap
import dev.glasslauncher.data.Wallpaper
import dev.glasslauncher.data.WallpaperKind
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.asCoroutineDispatcher
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.security.MessageDigest
import kotlin.math.max

class Backdrop(
    val sharp: ImageBitmap,
    /** Blurred wallpaper for full-screen drawing. */
    val blurred: ImageBitmap,
    /**
     * Opaque wallpaper copies from sharp to fully blurred. Animating the blur steps through these, so
     * every frame is a single opaque full-screen draw instead of a two-image cross-fade.
     */
    ladder: List<ImageBitmap>,
    /** Same blur as a software bitmap, for the glass BitmapShader. */
    val blurredSoftware: ImageBitmap,
    /**
     * Lightly blurred software copy (scrim included) for clear glass such as the dock tray: the art
     * stays readable through it, as on tvOS 27, instead of turning into a frosted wash.
     */
    val clearSoftware: ImageBitmap,
    /** [clearSoftware] in the text-safe range (Blur.legible): clear glass that carries text (Control Center). */
    val clearLegible: ImageBitmap = clearSoftware,
    /** Baked for light appearance: the blurred copies are washed milky so dark text reads on them. */
    val isLight: Boolean,
    /** Luminance of the art (as shown, scrims included) on a [LUMA_COLS]×[LUMA_ROWS] grid. */
    private val luma: FloatArray = FloatArray(0),
    /** Control Center's material (see [GlassMatch.ccSheet]), baked here so opening Control Center does no work. */
    val ccSheet: GlassMatch.PanelSheet? = null,
    /** P205/P257: the ladder's blur rungs, baked only once Home settles on this scene (see [ensureLadder]). */
    private var pendingRungs: (() -> List<Bitmap>)? = null,
) {
    /**
     * The ladder in use: sharp to blurred. Its in-between rungs go to the GPU only once Home settles on this scene
     * ([ensureLadder]): uploading ~10 bitmaps per dock move contended with the frames (P205: focusDock's slow frames
     * were GPU backpressure). Until then it is just sharp and blurred, both on the GPU, so the scroll cross-fades them.
     */
    var ladder: List<ImageBitmap> by androidx.compose.runtime.mutableStateOf(ladder)
        private set

    @Volatile private var sheetOnDemand: GlassMatch.PanelSheet? = null

    /**
     * Control Center's sheet for [variant]: none for the default (E draws [clearLegible]). A scene baked under E has no sheet,
     * so a switch to another variant bakes it here once, from the clear texture, and keeps it (P257).
     */
    fun sheetFor(variant: String): GlassMatch.PanelSheet? {
        if (variant == "E") return null
        ccSheet?.let { return it }
        sheetOnDemand?.let { return it }
        val clear = clearSoftware.asAndroidBitmap()
        val soft = if (clear.config == Bitmap.Config.HARDWARE) clear.copy(Bitmap.Config.ARGB_8888, false) else clear
        return GlassMatch.ccSheet(soft, isLight).also { sheetOnDemand = it; if (soft !== clear) soft.recycle() }
    }

    /** Bakes the pending rungs and puts them on the GPU, off the main thread, and swaps in the full ladder. Idempotent. */
    suspend fun ensureLadder() {
        val bakeRungs = synchronized(this) { pendingRungs.also { pendingRungs = null } } ?: return
        val gpu = withContext(Dispatchers.Default) { bakeRungs().map { b -> (b.copy(Bitmap.Config.HARDWARE, false)?.also { b.recycle() } ?: b).asImageBitmap() } }
        val now = ladder
        ladder = listOf(now.first()) + gpu + now.last()
    }

    /**
     * Whether the art behind a region (fractions of the screen) is light, so text placed straight on it
     * (not on glass) should be dark. Uses the region's average (dark text over mostly dark art, the
     * brightest-cell rule, read worse). White text is flat (tvOS 27, no shadow), so mid-bright art counts.
     */
    fun artLight(left: Float, top: Float, right: Float, bottom: Float): Boolean {
        if (luma.isEmpty()) return isLight
        var sum = 0f; var n = 0
        val c0 = (left * LUMA_COLS).toInt().coerceIn(0, LUMA_COLS - 1); val c1 = (right * LUMA_COLS).toInt().coerceIn(c0, LUMA_COLS - 1)
        val r0 = (top * LUMA_ROWS).toInt().coerceIn(0, LUMA_ROWS - 1); val r1 = (bottom * LUMA_ROWS).toInt().coerceIn(r0, LUMA_ROWS - 1)
        for (r in r0..r1) for (c in c0..c1) { sum += luma[r * LUMA_COLS + c]; n++ }
        return sum / n > 0.55f
    }

    companion object {
        const val LUMA_COLS = 32
        const val LUMA_ROWS = 18
    }
}

data class Preset(val id: String, val name: String, val light: Boolean, val base: Int, val blobs: List<Blob>)
data class Blob(val color: Int, val x: Float, val y: Float, val r: Float)

class WallpaperLoader(private val context: Context, private val http: OkHttpClient) {
    /** The config the backdrop's opaque bitmaps go to the GPU as, or null for "leave as ARGB_8888". */
    private val gpuConfig get() = if (RGB565_BACKDROP) Bitmap.Config.RGB_565 else null

    /**
     * Copies an opaque software bitmap for the GPU, dithering first when the 565 flag is on (see [Dither]).
     *
     * Two steps, not one: `copy(HARDWARE)` keeps the source's pixel format, so ARGB_8888 would stay 32-bit. Converting
     * to a software RGB_565 first and only then to HARDWARE is what actually halves the memory. Recycles [b] on the
     * way, like the copies it replaces, and falls back to whatever it has so a caller never null-checks.
     */
    private fun gpuOf(b: Bitmap): Bitmap {
        if (gpuConfig == null) return b.copy(Bitmap.Config.HARDWARE, false)?.also { b.recycle() } ?: b
        Dither.apply(b)
        val narrow = b.copy(Bitmap.Config.RGB_565, false) ?: return b
        b.recycle()
        val gpu = narrow.copy(Bitmap.Config.HARDWARE, false) ?: return narrow
        narrow.recycle()
        return gpu
    }

    private val dir = File(context.filesDir, "wallpapers").apply { mkdirs() }
    // RenderScript setup takes tens of ms, so it happens on the first bake (a background thread).
    private val blurReady by lazy { Blur.init(context) }

    suspend fun load(wallpaper: Wallpaper, light: Boolean): Backdrop = withContext(Dispatchers.Default) {
        val source = when (wallpaper.kind) {
            WallpaperKind.Preset -> renderPreset(presets.firstOrNull { it.id == wallpaper.value } ?: presets.first())
            WallpaperKind.File -> decode(File(wallpaper.value)) ?: renderPreset(presets.first())
            WallpaperKind.Url -> decode(cachedUrlFile(wallpaper.value)) ?: renderPreset(presets.first())
        }
        bake(source, Scene.Wallpaper, light) { true }
    }

    /** Full-bleed top-shelf art (or a video frame) as the Home backdrop. */
    suspend fun fromImage(image: Bitmap, scene: Scene = Scene.Hero, background: Boolean = false, light: Boolean = false): Backdrop =
        withContext(if (background) BakeDispatcher else Dispatchers.Default) {
            val job = coroutineContext[kotlinx.coroutines.Job]
            bake(cropToScreen(image, recycleSource = false), scene, light) { job?.isActive != false }
        }

    /**
     * An app's logo art as its Top Shelf hero, sharp: kept at 1280×720 (Amazon's Fire TV icons are that size,
     * and logo art drawn stretched to 1080p stays crisp), which is 3.7 MB of sharp layer instead of 8.3, so a
     * few heroes can stay baked while browsing the tray. Baked at background priority.
     */
    suspend fun fromHeroArt(image: Bitmap, light: Boolean = false): Backdrop = withContext(BakeDispatcher) {
        val job = coroutineContext[kotlinx.coroutines.Job]
        // A hero laid out at 1080p (a raster logo at its own pixels, P108) is baked at 1080p: shrinking it to 720p
        // would have the screen enlarge it again.
        val full = image.width >= SHARP_W
        bake(cropToScreen(image, recycleSource = false, outW = if (full) SHARP_W else HERO_W, outH = if (full) SHARP_H else HERO_H), Scene.Wallpaper, light) { job?.isActive != false }
    }

    /** [fromHeroArt] from a URL (Coil's disk cache; nothing kept in memory). */
    suspend fun heroFromUrl(url: String, light: Boolean = false): Backdrop? {
        // A stock or placeholder picture is not the app's art: the caller falls back to the app's own banner/icon.
        if (ImageGate.isPlaceholder(url)) return null
        val request = coil3.request.ImageRequest.Builder(context).data(url).size(HERO_W, HERO_H).allowHardware(false)
            .memoryCachePolicy(coil3.request.CachePolicy.DISABLED).build()
        val image = (coil3.SingletonImageLoader.get(context).execute(request) as? coil3.request.SuccessResult)?.image ?: return null
        val bitmap = image.toBitmap()
        // Amazon's icon is the logo on its colour; laid out tvOS's way (about half the width, above the tray).
        // Decoded at its own size (Coil never enlarges): one art pixel is one native pixel.
        val hero = dev.glasslauncher.apps.LogoHero.compose(bitmap, HERO_W, HERO_H, nativeFactor = 1f)
        bitmap.recycle()
        return fromHeroArt(hero, light).also { hero.recycle() }
    }

    /**
     * A Cover Flow shelf's background (P33): the focused album or playlist cover filling the screen, very mildly
     * blurred, with a glossy sheen across the top and a darker bottom so the titles read. Baked once per cover
     * (nothing live), then baked like any backdrop, so the glass around it is tinted from it.
     */
    suspend fun coverFromUrl(url: String, light: Boolean = false): Backdrop? {
        val request = coil3.request.ImageRequest.Builder(context).data(url).size(COVER_SRC, COVER_SRC).allowHardware(false)
            .memoryCachePolicy(coil3.request.CachePolicy.DISABLED).build()
        val image = (coil3.SingletonImageLoader.get(context).execute(request) as? coil3.request.SuccessResult)?.image ?: return null
        return softFromBitmap(image.toBitmap(), light)
    }

    /**
     * The soft level of the quality gate ([ImageGate]): [art] (recycled) centre-cropped to the screen's shape,
     * heavily blurred and colour-boosted, with the cover look's sheen and shade; baked like any backdrop.
     */
    private suspend fun softFromBitmap(art: Bitmap, light: Boolean): Backdrop {
        val scene = withContext(BakeDispatcher) { blurReady; coverScene(art) }
        art.recycle()
        // At the hero size (1280x720), not 1080p: it is soft anyway, and a step in the Cover Flow must not wait on a
        // full-screen bake (that took ~1.1 s on the stick).
        return fromHeroArt(scene, light = light).also { scene.recycle() }
    }

    /** Starts the expensive one-time setup (RenderScript, the image loader) before the first bake needs it. */
    fun prewarm() {
        blurReady
        coil3.SingletonImageLoader.get(context)
    }

    enum class Scene { Wallpaper, Hero }

    /**
     * Downloads (or reuses Coil's cache for) a featured image and bakes it as the Home backdrop, through the
     * quality gate ([ImageGate], P79/P81): a larger variant of the same picture is tried first ([Variants]); a
     * picture that would be stretched more than 1.25x is baked as the soft colour backdrop made from it; icon-sized
     * or placeholder art gives null (the caller shows the theme's own backdrop). Never a stretched picture.
     */
    suspend fun fromUrl(url: String, background: Boolean = false, light: Boolean = false): Backdrop? {
        var best: Bitmap? = null
        for (candidate in Variants.candidates(url)) {
            val request = coil3.request.ImageRequest.Builder(context)
                .data(candidate)
                .size(SHARP_W, SHARP_H)
                .allowHardware(false)
                // Baked into the backdrop right away; keeping the 3.6 MB source in Coil's memory cache only costs PSS.
                .memoryCachePolicy(coil3.request.CachePolicy.DISABLED)
                .build()
            val image = (coil3.SingletonImageLoader.get(context).execute(request) as? coil3.request.SuccessResult)?.image ?: continue
            val bitmap = image.toBitmap()
            if (ImageGate.decide(candidate, bitmap.width, bitmap.height) == ImageGate.Level.Sharp) {
                best?.recycle()
                return fromImage(bitmap, background = background, light = light).also { bitmap.recycle() }
            }
            // Not sharp enough: keep the largest of the candidates for the soft level.
            if (best == null || bitmap.width.toLong() * bitmap.height > best.width.toLong() * best.height) { best?.recycle(); best = bitmap } else bitmap.recycle()
        }
        val art = best ?: return null
        return when (ImageGate.decide(url, art.width, art.height)) {
            ImageGate.Level.Plain -> { art.recycle(); null }
            else -> softFromBitmap(art, light)
        }
    }

    /**
     * Bakes everything Home draws from one source image: the sharp screen image, a ladder of
     * progressively blurred opaque copies for the scroll blur, and the small blurred texture the glass
     * surfaces sample (which is what tints glass from the content behind it).
     */
    /** [alive] is checked between steps, so a superseded bake (fast browsing, startup) stops early. */
    private fun bake(source: Bitmap, scene: Scene, light: Boolean, alive: () -> Boolean): Backdrop {
        fun check() { if (!alive()) { throw kotlinx.coroutines.CancellationException("bake superseded") } }
        blurReady
        // Every blurred copy comes from one half-size intermediate: cheaper than re-sampling the 1080p
        // source for each, and a cleaner downscale for the small ones.
        val mid = Blur.backdrop(source, SHARP_W / 2, SHARP_H / 2, radius = 0, saturation = 1f)
        check()
        val blurred = Blur.backdrop(mid, BLUR_W, BLUR_H, radius = 5)
        // Appearance changes the blurred copies, not the art: in light appearance they wash towards milky
        // white (more for dark art), in dark towards charcoal (only light art needs it), as tvOS's grid does.
        // Washes grow with each rung, so the scroll blur also fades into the appearance.
        val luminance = Blur.luminance(blurred)
        // Light appearance washes towards a pastel of the art's own colour (lavender, mint, sky), as tvOS's
        // light pages are hued, not near-white.
        val tint = Blur.pastel(blurred)
        val wash = if (light) (0.62f - luminance * 0.45f).coerceIn(0.3f, 0.6f) else ((luminance - 0.35f) * 0.9f).coerceIn(0f, 0.4f)
        // Ten rungs from sharp to fully blurred, so the blur ramps smoothly as Home scrolls to the grid
        // (six rungs read as visible jumps on the long tvOS scroll curve).
        val rungs = listOf(
            Triple(800, 450, 1), Triple(640, 360, 1), Triple(544, 306, 2), Triple(448, 252, 2),
            Triple(384, 216, 3), Triple(320, 180, 3), Triple(272, 153, 4), Triple(240, 135, 4),
        )
        // P257: the rungs are only for the scroll's blur ramp, so they're baked once Home settles (ensureLadder), not before the swap:
        // they were ~240 ms of every scene's bake. Until then the scroll cross-fades sharp and blurred (P205's path). They keep a
        // copy of the half-size intermediate (2 MB, less than the eight rungs it replaces) until then.
        val rungSource = mid.copy(Bitmap.Config.ARGB_8888, false)
        val steps = {
            rungs.mapIndexed { i, (w, h, r) ->
                val t = (i + 1f) / (rungs.size + 1)
                Blur.backdrop(rungSource, w, h, radius = r, saturation = 1f + 0.35f * t).also { bakeScrim(it, light, scene, wash * t, tint); Blur.legible(it, light, t * t); it.setHasAlpha(false) }
            }.also { rungSource.recycle() }
        }
        check()
        val clear = Blur.backdrop(mid, CLEAR_W, CLEAR_H, radius = 2, saturation = 1.15f).also { bakeScrim(it, light, scene, 0f); it.setHasAlpha(false) }
        // Control Center's texture (P206 E). It is one style with white ink in either theme, so it always takes the dark-ink
        // range: the light range lifted a white scene to white glass under white labels (P237).
        val clearText = clear.copy(Bitmap.Config.ARGB_8888, true).also { Blur.legible(it, light = false) }
        mid.recycle()
        // Scrims are baked in and the bitmaps are marked opaque, so drawing the backdrop is a single
        // non-blended full-screen pass. TV-stick GPUs only afford about two full-screen passes per frame.
        bakeScrim(source, light, scene, 0f)
        val luma = Blur.lumaGrid(source, Backdrop.LUMA_COLS, Backdrop.LUMA_ROWS)
        // The grid and every glass surface carry text, so both get the legibility range (see Blur.legible).
        val blurredScreen = blurred.copy(Bitmap.Config.ARGB_8888, true).also { bakeScrim(it, light, scene, wash, tint); Blur.legible(it, light) }
        // The glass texture gets the appearance wash but not the scrims, so panels keep their own tint.
        if (wash > 0f) Canvas(blurred).drawColor(washColor(light, wash, tint))
        Blur.legible(blurred, light)
        source.setHasAlpha(false)
        blurredScreen.setHasAlpha(false)
        blurred.setHasAlpha(false)
        val sharp = gpuOf(source)
        val blurredGpu = gpuOf(blurredScreen)
        // P205: the rungs stay software until Home settles on this scene (Backdrop.ensureLadder).
        val ladder = listOf(sharp, blurredGpu)
        // The glass samples these through a BitmapShader every frame: as software bitmaps the GPU re-uploads
        // them each frame of a scroll (measured: glass off cut janky frames from 39% to 15%), so they live
        // on the GPU too. The names say "Software" for history; they're only read by shaders.
        val glassBlur = gpuOf(blurred)
        // Control Center's sheet comes from the same clear texture, here, so its open has nothing to bake.
        // Control Center's own sheet, for the variants that draw it. The default (E, P206) draws the dock's clear texture instead,
        // so baking a sheet there was ~180 ms of every scene's bake for nothing (P257).
        CcAdapt.readVariant()
        val ccSheet = if (CcAdapt.variant == "E") null else GlassMatch.ccSheet(clear, light)
        val glassClear = gpuOf(clear)
        val glassClearText = gpuOf(clearText)
        return Backdrop(sharp.asImageBitmap(), blurredGpu.asImageBitmap(), ladder.map { it.asImageBitmap() }, glassBlur.asImageBitmap(), glassClear.asImageBitmap(), glassClearText.asImageBitmap(), light, luma, ccSheet, pendingRungs = steps)
    }

    /**
     * Glass textures only, for Control Center's overlay window (a capture of the screen behind it): the
     * tray's clear texture and Control Center's sheet, one small blur instead of the full ladder (~550 ms
     * there, which cost frames while Control Center opened). Nothing here is drawn full screen.
     */
    suspend fun glassOnly(source: Bitmap, light: Boolean = false): Backdrop = withContext(Dispatchers.Default) {
        blurReady
        val clear = Blur.backdrop(source, CLEAR_W, CLEAR_H, radius = 2, saturation = 1.15f).also { it.setHasAlpha(false) }
        CcAdapt.readVariant()
        val ccSheet = if (CcAdapt.variant == "E") null else GlassMatch.ccSheet(clear, light)
        val gpuClear = (clear.copy(Bitmap.Config.HARDWARE, false)?.also { clear.recycle() } ?: clear).asImageBitmap()
        Backdrop(gpuClear, gpuClear, listOf(gpuClear), gpuClear, gpuClear, gpuClear, isLight = light, ccSheet = ccSheet)
    }

    /** [wash] (0..1) is the appearance wash: white in light appearance, black in dark. */
    private fun bakeScrim(bitmap: Bitmap, light: Boolean, scene: Scene, wash: Float, tint: Int = Color.WHITE) {
        val w = bitmap.width.toFloat()
        val h = bitmap.height.toFloat()
        val canvas = Canvas(bitmap)
        // A light wash replaces the darkening scrims as it builds up.
        val scrim = if (light) (1f - wash / 0.4f).coerceIn(0f, 1f) else 1f
        // Light appearance gets no darkening scrims at all: under a pale wash they read as a grey band.
        if (scene == Scene.Hero && !light) {
            // Top-shelf art darkens under the tray and behind the title, as on tvOS.
            canvas.drawRect(0f, 0f, w, h, Paint().apply {
                shader = android.graphics.LinearGradient(0f, h * 0.45f, 0f, h, Color.TRANSPARENT, Color.argb((120 * scrim).toInt(), 0, 0, 0), Shader.TileMode.CLAMP)
            })
            canvas.drawRect(0f, 0f, w, h, Paint().apply {
                shader = android.graphics.LinearGradient(0f, 0f, w * 0.5f, 0f, Color.argb((115 * scrim).toInt(), 0, 0, 0), Color.TRANSPARENT, Shader.TileMode.CLAMP)
            })
        } else {
            val end = if (light) Color.argb(38, 255, 255, 255) else Color.argb(102, 0, 0, 0)
            canvas.drawRect(0f, 0f, w, h, Paint().apply {
                shader = android.graphics.LinearGradient(0f, h * 0.45f, 0f, h, Color.TRANSPARENT, end, Shader.TileMode.CLAMP)
            })
        }
        if (wash > 0f) canvas.drawColor(washColor(light, wash, tint))
    }

    private fun washColor(light: Boolean, wash: Float, tint: Int = Color.WHITE): Int {
        if (!light) return Color.argb((wash * 255).toInt(), 0, 0, 0)
        return Color.argb((wash * 255).toInt(), Color.red(tint), Color.green(tint), Color.blue(tint))
    }

    /** Downloads the image so it survives offline boots; returns false if it isn't a decodable image. */
    suspend fun downloadUrl(url: String): Boolean = withContext(Dispatchers.IO) {
        val target = cachedUrlFile(url)
        runCatching {
            http.newCall(Request.Builder().url(url).build()).execute().use { response ->
                if (!response.isSuccessful) return@use false
                val tmp = File(dir, target.name + ".tmp")
                tmp.outputStream().use { out -> response.body.byteStream().copyTo(out) }
                val ok = isImage(tmp)
                if (ok) tmp.renameTo(target) else tmp.delete()
                ok
            }
        }.getOrDefault(false)
    }

    /** Downloads an image to app storage under [name]; returns its path or null if it isn't an image. */
    suspend fun downloadImage(url: String, name: String): String? =
        if (downloadUrl(url)) importFile(cachedUrlFile(url), name) else null

    /** Copies a user-picked image into app storage and returns its path. */
    suspend fun importImage(uri: Uri, name: String): String? = withContext(Dispatchers.IO) {
        if (dev.glasslauncher.system.RuntimeSafety.isolated) return@withContext null
        val target = File(dir, name)
        runCatching {
            context.contentResolver.openInputStream(uri)!!.use { input -> target.outputStream().use { input.copyTo(it) } }
            if (isImage(target)) target.path else { target.delete(); null }
        }.getOrNull()
    }

    fun importFile(source: File, name: String): String? {
        if (dev.glasslauncher.system.RuntimeSafety.isolated) return null
        val target = File(dir, name)
        return runCatching { source.copyTo(target, overwrite = true); if (isImage(target)) target.path else null }.getOrNull()
    }

    private fun isImage(file: File): Boolean {
        val o = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(file.path, o)
        return o.outWidth > 0 && o.outHeight > 0
    }

    private fun cachedUrlFile(url: String): File {
        val hash = MessageDigest.getInstance("SHA-1").digest(url.toByteArray()).joinToString("") { "%02x".format(it) }
        return File(dir, "url-$hash")
    }

    private fun decode(file: File): Bitmap? {
        if (dev.glasslauncher.system.RuntimeSafety.isolated && !dev.glasslauncher.system.RuntimeSafety.privateFile(context, file)) return null
        if (!file.exists()) return null
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(file.path, bounds)
        if (bounds.outWidth <= 0) return null
        var sample = 1
        while (bounds.outWidth / (sample * 2) >= SHARP_W && bounds.outHeight / (sample * 2) >= SHARP_H) sample *= 2
        val decoded = BitmapFactory.decodeFile(file.path, BitmapFactory.Options().apply { inSampleSize = sample }) ?: return null
        return cropToScreen(decoded)
    }

    private fun cropToScreen(src: Bitmap, recycleSource: Boolean = true, outW: Int = SHARP_W, outH: Int = SHARP_H): Bitmap {
        val out = Bitmap.createBitmap(outW, outH, Bitmap.Config.ARGB_8888)
        val scale = max(outW / src.width.toFloat(), outH / src.height.toFloat())
        val w = (outW / scale).toInt()
        val h = (outH / scale).toInt()
        val left = (src.width - w) / 2
        val top = (src.height - h) / 2
        Canvas(out).drawBitmap(src, Rect(left, top, left + w, top + h), Rect(0, 0, outW, outH), Paint(Paint.FILTER_BITMAP_FLAG))
        if (recycleSource && src != out) src.recycle()
        return out
    }

    fun renderPreset(preset: Preset, width: Int = SHARP_W, height: Int = SHARP_H): Bitmap {
        val bitmap = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(bitmap)
        canvas.drawColor(preset.base)
        val paint = Paint(Paint.ANTI_ALIAS_FLAG)
        val scale = max(width, height).toFloat()
        for (blob in preset.blobs) {
            val cx = blob.x * width
            val cy = blob.y * height
            val r = blob.r * scale
            paint.shader = RadialGradient(cx, cy, r, intArrayOf(blob.color, Color.argb(0, Color.red(blob.color), Color.green(blob.color), Color.blue(blob.color))), floatArrayOf(0f, 1f), Shader.TileMode.CLAMP)
            canvas.drawCircle(cx, cy, r, paint)
        }
        return bitmap
    }

    companion object {
        // Native 1080p: hero art is shown 1:1, and a 720p bake looked soft next to the tiles.
        /** One low-priority thread for slideshow bakes: they must never compete with drawing or input. */
        val BakeDispatcher = java.util.concurrent.Executors.newSingleThreadExecutor { r ->
            Thread({ android.os.Process.setThreadPriority(android.os.Process.THREAD_PRIORITY_BACKGROUND); r.run() }, "glass-bake")
        }.asCoroutineDispatcher()

        const val HERO_W = 1280
        const val HERO_H = 720
        /** A cover is fetched this big for its background: it is blurred, so more would only cost memory. */
        const val COVER_SRC = 640
        const val SHARP_W = 1920
        const val SHARP_H = 1080
        const val BLUR_W = 192
        const val BLUR_H = 108
        const val CLEAR_W = 480
        const val CLEAR_H = 270

        /**
         * P285: the backdrop's full-screen bitmaps go to the GPU as RGB_565 when this is on - 4.0 MB each instead of
         * 8.3, and they are all already marked opaque so nothing is lost semantically. It was dropped once on GPU
         * time (correct: the conversion is a copy either way) but PSS is the binding limit here, not the GPU.
         *
         * OFF by default, and here is the measured cost/benefit rather than the hoped one.
         *
         * Saving is ~4.5 MB, NOT the ~21 MB the ticket assumed. Measured on the stick as a settled PSS at rest, in an
         * adjacent A/B (same build, one const flipped): 122,910 KB off vs 118,268 KB on, while GL mtrack barely moved
         * (36,794 -> 35,775). The reason is that `copy(HARDWARE)` returns a bitmap in the platform's display format,
         * so the GPU buffers stay 32-bit; only the short-lived software copies are ever actually narrow. Halving the
         * GPU buffers would need the hardware bitmap built at 565 rather than converted into.
         *
         * Cost is 15 changed screen goldens (mean 1.7/255, max 8 = one 565 step, across home/featured/grid/dock/
         * folder/app-menu/control-center, light and dark), which the dither keeps from banding (see [Dither]: longest
         * flat run 39 rows -> 10). Worth having, but not worth churning every golden on its own, so: flip this,
         * re-record the screenshots.
         */
        const val RGB565_BACKDROP = false

        val presets = listOf(
            Preset("aurora", "Aurora", false, 0xFF0B1026.toInt(), listOf(
                Blob(0xCC3A2E8F.toInt(), 0.18f, 0.22f, 0.55f), Blob(0xB30E7C86.toInt(), 0.88f, 0.28f, 0.5f),
                Blob(0xA67B2F7F.toInt(), 0.62f, 0.95f, 0.55f), Blob(0x991D4ED8.toInt(), 0.05f, 0.95f, 0.45f),
            )),
            Preset("ember", "Ember", false, 0xFF140A0E.toInt(), listOf(
                Blob(0xCCB4232C.toInt(), 0.15f, 0.85f, 0.55f), Blob(0xB3E07A2F.toInt(), 0.8f, 0.15f, 0.45f),
                Blob(0x995B1A7A.toInt(), 0.55f, 0.55f, 0.5f),
            )),
            Preset("abyss", "Abyss", false, 0xFF030712.toInt(), listOf(
                Blob(0xB3075985.toInt(), 0.25f, 0.3f, 0.5f), Blob(0x99115E59.toInt(), 0.85f, 0.85f, 0.55f),
                Blob(0x661E3A8A.toInt(), 0.7f, 0.1f, 0.4f),
            )),
            Preset("graphite", "Graphite", false, 0xFF101114.toInt(), listOf(
                Blob(0x803F4552.toInt(), 0.2f, 0.2f, 0.6f), Blob(0x66272B33.toInt(), 0.85f, 0.9f, 0.55f),
            )),
            Preset("dawn", "Dawn", true, 0xFFF4EEF8.toInt(), listOf(
                Blob(0xE6FFD3C2.toInt(), 0.15f, 0.2f, 0.55f), Blob(0xD9D6CCFF.toInt(), 0.85f, 0.3f, 0.5f),
                Blob(0xCCC4E6FF.toInt(), 0.55f, 0.95f, 0.55f),
            )),
            Preset("mist", "Mist", true, 0xFFEEF2F5.toInt(), listOf(
                Blob(0xCCC8F0E0.toInt(), 0.2f, 0.85f, 0.55f), Blob(0xCCCFE3FF.toInt(), 0.8f, 0.2f, 0.5f),
                Blob(0x99FFFFFF.toInt(), 0.5f, 0.45f, 0.4f),
            )),
        )
    }
}
