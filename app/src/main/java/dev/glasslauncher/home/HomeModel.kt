package dev.glasslauncher.home

import dev.glasslauncher.system.RuntimeSafety

import android.app.Application
import android.content.ActivityNotFoundException
import android.content.Intent
import android.net.Uri
import android.provider.Settings
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import dev.glasslauncher.app
import dev.glasslauncher.apps.AppEntry
import dev.glasslauncher.apps.TileSpec
import dev.glasslauncher.data.Folder
import dev.glasslauncher.data.LauncherConfig
import dev.glasslauncher.data.appKey
import dev.glasslauncher.data.folderKey
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.flowOn
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import java.util.UUID

@androidx.compose.runtime.Immutable
sealed interface GridItem {
    val key: String

    data class App(val app: AppEntry) : GridItem {
        override val key get() = appKey(app.packageName)
    }

    data class FolderItem(val folder: Folder, val apps: List<AppEntry>) : GridItem {
        override val key get() = folderKey(folder.id)
    }
}

data class HomeLayout(
    val dock: List<AppEntry> = emptyList(),
    val grid: List<GridItem> = emptyList(),
    val installed: List<AppEntry> = emptyList(),
    val loaded: Boolean = false,
)

const val DOCK_SIZE = 6
const val RECENT_LIMIT = 12
const val COLUMNS = 6

class HomeModel(application: Application) : AndroidViewModel(application) {

    private val graph = application.app
    private val store = graph.config

    /**
     * While an app is being moved (Edit Home Screen, Rearrange Folder) the config is edited here, in memory, and
     * written once when the move ends ([endMove]): a held D-pad is many steps, and each used to be a disk write
     * and a round trip through the saved copy before the screen followed. Null otherwise.
     */
    private val moving = kotlinx.coroutines.flow.MutableStateFlow<LauncherConfig?>(null)

    /**
     * The config in effect: the saved one, with the move's working layout (dock, grid order, folders) while moving.
     * Everything else always comes from the saved copy, so a setting changed meanwhile is never shown stale.
     */
    val config: StateFlow<LauncherConfig> = combine(store.config, moving) { saved, working ->
        if (working == null) saved else saved.copy(dock = working.dock, order = working.order, folders = working.folders)
    }.stateIn(viewModelScope, SharingStarted.Eagerly, store.config.value)

    /** The saved config (what is on disk or about to be), without a move's working layout. */
    val savedConfig: StateFlow<LauncherConfig> get() = store.config

    // Built off Main (P159); the first value stays the unloaded HomeLayout() until a real one lands, so the `loaded` gate holds.
    val layout: StateFlow<HomeLayout> = combine(graph.apps.apps(), config) { apps, cfg ->
        buildLayout(apps, cfg)
    }.flowOn(kotlinx.coroutines.Dispatchers.Default).stateIn(viewModelScope, SharingStarted.Eagerly, HomeLayout())

    init {
        viewModelScope.launch {
            layout.collect { l ->
                if (!l.loaded) return@collect
                seedDefaults(l.installed)
                seedSeen(l.installed)
                // Draw (or read back from disk) the tiles ahead of time, in the order they appear on screen, so
                // scrolling never waits on bitmap generation and what is seen first is ready first.
                val cfg = config.value
                val onScreen = l.dock + l.grid.filterIsInstance<GridItem.App>().map { it.app } +
                    l.grid.filterIsInstance<GridItem.FolderItem>().flatMap { it.apps }
                val rest = l.installed.filter { app -> onScreen.none { it.packageName == app.packageName } }
                graph.tileArt.preload((onScreen + rest).map { spec(it, cfg) })
            }
        }
    }

    fun spec(app: AppEntry, cfg: LauncherConfig = config.value) =
        TileSpec(app, cfg.customIcons[app.packageName], cfg.iconPack)

    /** Opens an app; with a source view and tile bounds the system zooms it out of the tile (MOTION-03). */
    fun launch(app: AppEntry, from: android.view.View? = null, bounds: androidx.compose.ui.geometry.Rect? = null) {
        // A fade, not makeScaleUpAnimation (P302): the scale-up is the "genie" that stuttered and then snapped, and it
        // also fights the zoom Glass draws itself. Both the resume path and the plain launch use the same short fade.
        val options = android.app.ActivityOptions.makeCustomAnimation(
            getApplication(), dev.glasslauncher.R.anim.app_resume_enter, dev.glasslauncher.R.anim.app_open_hold,
        ).toBundle()
        noteLaunched(app.packageName)
        // The real task, with its state, when this build can see one (I08): looked up off the main thread (a binder
        // call), with the same zoom options. Anything else (no task, no privilege) launches normally.
        viewModelScope.launch {
            val resumed = kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) {
                dev.glasslauncher.system.RecentApps.resume(getApplication(), app.packageName, options)
            }
            if (!resumed) {
                // A launch that fails says so (P162), whether the app is gone, turned off or refuses to start.
                val ok = graph.apps.launchIntent(app)?.let { intent ->
                    try { getApplication<Application>().startActivity(intent, options); true } catch (_: ActivityNotFoundException) { false } catch (_: SecurityException) { false }
                } ?: false
                if (!ok) dev.glasslauncher.system.LaunchNotice.couldNotOpen(getApplication(), app.label)
            }
        }
    }

    fun launchIntent(app: AppEntry): Intent? = graph.apps.launchIntent(app)

    /** Remembers the launch for the app switcher and clears the new-app dot. */
    fun noteLaunched(pkg: String) = edit { launchedConfig(it, pkg) }

    /** New since the launcher last looked, and not opened yet: shows the blue dot. */
    fun isNew(pkg: String, cfg: LauncherConfig = config.value) = cfg.seenApps.isNotEmpty() && pkg !in cfg.seenApps

    fun appInfo(app: AppEntry) = startSafely(
        Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:${app.packageName}")),
    )

    fun uninstall(app: AppEntry) = startSafely(
        Intent(Intent.ACTION_DELETE, Uri.parse("package:${app.packageName}")),
    )

    private fun startSafely(intent: Intent) {
        if (RuntimeSafety.isolated) return
        try { getApplication<Application>().startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) } catch (_: Exception) { }
    }

    /** Move to › App Dock: a full tray takes the app and its rightmost app moves down to the grid (see [addedToTray]). */
    fun addToDock(pkg: String) {
        val apps = layout.value.installed
        edit { c -> addedToTray(c, apps, pkg) }
    }

    /** Out of the tray to the front of the grid; the first other grid app takes its place (the tray stays six). */
    fun removeFromDock(pkg: String) {
        val apps = layout.value.installed
        edit { c -> removedFromTray(c, apps, pkg) }
    }

    fun hide(pkg: String) = edit { c ->
        c.copy(hidden = c.hidden + pkg, dock = c.dock - pkg, folders = c.folders.withoutApp(pkg))
    }

    fun unhide(pkg: String) = edit { c -> c.copy(hidden = c.hidden - pkg) }

    fun setCustomIcon(pkg: String, path: String?) = edit { c ->
        c.copy(customIcons = if (path == null) c.customIcons - pkg else c.customIcons + (pkg to path))
    }

    /**
     * The layout the next edit will start from. [layout] is built off Main and trails the working copy by a dispatcher round trip,
     * so a held D-pad (about 30 ms apart) read stale positions and lost steps; this builds it from the newest config on the spot.
     */
    fun currentLayout(): HomeLayout = buildLayout(layout.value.installed, moving.value ?: config.value)

    /**
     * Moves a dock or grid item by [delta] positions within its own row set. Returns false at an edge. The position is resolved
     * against the working copy ([movedBy]), both here and inside the update, never against [layout].
     */
    fun move(key: String, delta: Int): Boolean {
        val apps = layout.value.installed
        if (movedBy(moving.value ?: config.value, apps, key, delta) == null) return false
        edit { c -> movedBy(c, apps, key, delta) ?: c }
        return true
    }

    /** Moves an app between the dock and the grid while in move mode. */
    fun moveIntoDock(pkg: String, position: Int? = null): Boolean {
        val apps = layout.value.installed
        // The tray is derived from the config the update receives, so back-to-back moves never see a stale layout.
        edit { c -> addedToTray(c, apps, pkg, position) }
        return true
    }

    fun moveOutOfDock(pkg: String) = removeFromDock(pkg)

    fun newFolder(pkg: String, name: String = "Folder"): String {
        val id = UUID.randomUUID().toString().take(8)
        edit { c ->
            val folders = c.folders.withoutApp(pkg) + Folder(id, name, listOf(pkg))
            val key = appKey(pkg)
            val order = c.order.map { if (it == key) folderKey(id) else it }.let { if (folderKey(id) in it) it else it + folderKey(id) }
            c.copy(folders = folders, dock = c.dock - pkg, order = order)
        }
        return id
    }

    fun addToFolder(pkg: String, folderId: String) = edit { c ->
        c.copy(
            folders = c.folders.withoutApp(pkg).map { if (it.id == folderId) it.copy(apps = it.apps + pkg) else it },
            dock = c.dock - pkg,
        )
    }

    fun removeFromFolder(pkg: String, folderId: String) = edit { c ->
        val folderIndex = c.order.indexOf(folderKey(folderId)).coerceAtLeast(0)
        val order = c.order.toMutableList().apply { remove(appKey(pkg)); add((folderIndex + 1).coerceAtMost(size), appKey(pkg)) }
        c.copy(folders = c.folders.map { if (it.id == folderId) it.copy(apps = it.apps - pkg) else it }, order = order)
    }

    /**
     * Rearranging inside an open folder: [pkg] moves one place ([dx]) or one row ([dy]) among the folder's apps
     * as shown. Resolved inside the config update, against the newest order, so a held or double D-pad press never
     * works from a stale one. An edge, or an app that isn't shown, changes nothing.
     */
    fun moveInFolder(folderId: String, pkg: String, dx: Int, dy: Int, columns: Int) {
        val installed = layout.value.installed.map { it.packageName }.toSet()
        edit { c -> movedInFolderBy(c, installed, folderId, pkg, dx, dy, columns) }
    }

    fun renameFolder(folderId: String, name: String) = edit { c ->
        c.copy(folders = c.folders.map { if (it.id == folderId) it.copy(name = name.ifBlank { it.name }) else it })
    }

    fun deleteFolder(folderId: String) = edit { c ->
        val folder = c.folders.firstOrNull { it.id == folderId } ?: return@edit c
        val idx = c.order.indexOf(folderKey(folderId))
        val order = c.order.toMutableList().apply {
            if (idx >= 0) { removeAt(idx); addAll(idx, folder.apps.map(::appKey)) }
        }
        c.copy(folders = c.folders - folder, order = order)
    }

    fun edit(transform: (LauncherConfig) -> LauncherConfig) {
        // Moving: change the working layout now (the screen follows at once); it is saved when the move ends.
        // A change that touches anything but the layout is saved straight away, as before.
        val working = moving.value
        if (working != null) {
            val next = transform(working)
            val layoutOnly = next.copy(dock = working.dock, order = working.order, folders = working.folders) == working
            moving.update { c -> c?.let(transform) }
            if (layoutOnly) return
        }
        viewModelScope.launch { store.update(transform) }
    }

    /** How many times the config has been written to disk (tests and measurements). */
    val saveCount: Int get() = store.writes

    /** A move starts: from here edits stay in memory until [endMove]. */
    fun beginMove() {
        moveEpoch++
        if (moving.value == null) moving.value = store.config.value
    }

    /** Counts moves begun: an [endMove] still catching up must not drop the working layout of a move begun since. */
    private var moveEpoch = 0

    /**
     * The move is over: its result is saved in one write (only the layout parts: other changes made meanwhile are
     * kept). The working copy stays in effect until the saved copy has caught up, so the screen never steps back.
     */
    fun endMove() {
        val working = moving.value ?: return
        val epoch = moveEpoch
        viewModelScope.launch {
            store.update { c -> c.copy(dock = working.dock, order = working.order, folders = working.folders) }
            kotlinx.coroutines.withTimeoutOrNull(1_000) {
                store.config.first { it.dock == working.dock && it.order == working.order && it.folders == working.folders }
            }
            // Another move began meanwhile: it keeps (and saves) its own working layout.
            if (epoch == moveEpoch) moving.value = null
        }
    }

    private suspend fun seedDefaults(installed: List<AppEntry>) {
        // Hidden-by-default entries added after a launcher was already set up (seeding runs once).
        if (config.value.seededDefaults && config.value.hiddenMigration < HIDDEN_MIGRATION) {
            val have = installed.map { it.packageName }.toSet()
            store.update { c -> c.copy(hidden = c.hidden + LATER_HIDDEN.filter { it in have }, hiddenMigration = HIDDEN_MIGRATION) }
        }
        if (config.value.seededDefaults) return
        val have = installed.map { it.packageName }.toSet()
        val dock = DEFAULT_DOCK.filter { it in have }.distinct().take(DOCK_SIZE)
        val hidden = DEFAULT_HIDDEN.filter { it in have }.toSet()
        store.update { c -> c.copy(dock = c.dock.ifEmpty { dock }, hidden = c.hidden + hidden, seededDefaults = true) }
    }

    /** Everything installed when the launcher first runs counts as already seen. */
    private suspend fun seedSeen(installed: List<AppEntry>) {
        if (config.value.seenApps.isNotEmpty()) return
        store.update { c -> c.copy(seenApps = c.seenApps + installed.map { it.packageName }) }
    }

    companion object {
        /** The dock a first run seeds (internal: the screenshot harness seeds the same one up front). */
        internal val DEFAULT_DOCK = listOf(
            "com.netflix.ninja", "com.netflix.mediaclient",
            "com.amazon.firetv.youtube", "com.google.android.youtube.tv", "com.teamsmart.videomanager.tv",
            "com.amazon.avod", "com.amazon.amazonvideo.livingroom",
            "com.apple.atve.amazon.appletv", "com.apple.atve.androidtv.appletv",
            "com.disney.disneyplus", "com.plexapp.android", "com.stremio.one", "com.spotify.tv.android",
        )

        /** Fire TV system entries with placeholder icons that most people never open from a launcher. */
        private val DEFAULT_HIDDEN = listOf(
            "com.amazon.firebat", "com.amazon.firetv.troubleshooting", "com.amazon.ftv.profilepicker",
            "com.amazon.ftv.screensaver", "com.amazon.hedwig", "com.amazon.ssm", "com.amazon.tv.earlyaccess",
            "com.amazon.tv.ftvambient", "com.amazon.whasettings",
        )

        /** Added to existing installs once (version [HIDDEN_MIGRATION]): Fire TV Early Access traps the remote. */
        private val LATER_HIDDEN = listOf("com.amazon.tv.earlyaccess")
        private const val HIDDEN_MIGRATION = 1

        /**
         * The config after [pkg] is launched through Glass: seen, newest in the switcher's own list, and no longer
         * "closed" (closing hid it only until it is next used, and without usage access this is the only signal).
         */
        fun launchedConfig(c: LauncherConfig, pkg: String): LauncherConfig = c.copy(
            seenApps = c.seenApps + pkg,
            recentApps = (listOf(pkg) + (c.recentApps - pkg)).take(RECENT_LIMIT),
            closedRecents = c.closedRecents - pkg,
        )

        /** [cfg] with [key] moved by [delta] positions in its own row set as shown; null at an edge or for an item that isn't shown. */
        fun movedBy(cfg: LauncherConfig, apps: List<AppEntry>, key: String, delta: Int): LauncherConfig? {
            val l = buildLayout(apps, cfg)
            val pkg = key.removePrefix("app:")
            val dockIndex = l.dock.indexOfFirst { it.packageName == pkg }
            if (key.startsWith("app:") && dockIndex >= 0) {
                val target = dockIndex + delta
                if (target !in l.dock.indices) return null
                // The tray as shown (filled apps included), so positions match what's on screen.
                val d = l.dock.map { it.packageName }.toMutableList()
                d.add(target, d.removeAt(dockIndex))
                return cfg.copy(dock = d)
            }
            val keys = l.grid.map { it.key }.toMutableList()
            val from = keys.indexOf(key)
            val target = from + delta
            if (from < 0 || target !in keys.indices) return null
            keys.add(target, keys.removeAt(from))
            return cfg.copy(order = keys)
        }

        fun buildLayout(apps: List<AppEntry>, cfg: LauncherConfig): HomeLayout {
            val byPkg = apps.associateBy { it.packageName }
            val visible = apps.filter { it.packageName !in cfg.hidden }
            val visiblePkgs = visible.map { it.packageName }.toSet()
            val chosen = cfg.dock.filter { it in visiblePkgs }.distinct().take(DOCK_SIZE).map { byPkg.getValue(it) }
            val chosenPkgs = chosen.map { it.packageName }.toSet()

            val folders = cfg.folders.mapNotNull { f ->
                val members = f.apps.filter { it in visiblePkgs && it !in chosenPkgs }.distinct().map { byPkg.getValue(it) }
                if (members.isEmpty()) null else GridItem.FolderItem(f, members)
            }
            val inFolders = folders.flatMap { it.apps }.map { it.packageName }.toSet()
            val loose = visible.filter { it.packageName !in chosenPkgs && it.packageName !in inFolders }

            val items = LinkedHashMap<String, GridItem>()
            (loose.map { GridItem.App(it) } + folders).forEach { items[it.key] = it }
            val grid = cfg.order.mapNotNull { items.remove(it) } + items.values
            // The tray always holds six: the first apps of the grid (not folders) move up to fill it.
            val fill = grid.filterIsInstance<GridItem.App>().take(DOCK_SIZE - chosen.size)
            val filled = fill.map { it.key }.toSet()
            return HomeLayout(chosen + fill.map { it.app }, grid.filter { it.key !in filled }, apps, loaded = true)
        }

        /**
         * Puts [pkg] into the tray as shown in [layout] (chosen apps plus grid apps filling it up), at [position]
         * (null = the rightmost slot). A tray with six apps never refuses: the new app goes in and the tray's
         * rightmost app moves down to the grid, taking the slot [pkg] came from (a swap), or the first grid slot
         * when [pkg] came from a folder. Nothing is lost or duplicated, other grid items keep their order, and
         * hidden apps stay hidden. The result names all of the tray's apps, so it matches what was on screen.
         */
        fun addedToTray(cfg: LauncherConfig, apps: List<AppEntry>, pkg: String, position: Int? = null): LauncherConfig {
            val layout = buildLayout(apps, cfg)
            val shown = layout.dock.map { it.packageName }
            if (pkg in shown) return cfg
            val full = shown.size >= DOCK_SIZE
            val displaced = if (full) shown.last() else null
            val kept = if (displaced != null) shown.dropLast(1) else shown
            val dock = kept.toMutableList().also { it.add((position ?: it.size).coerceIn(0, it.size), pkg) }

            val gridKeys = layout.grid.map { it.key }
            val slot = gridKeys.indexOf(appKey(pkg)).coerceAtLeast(0)
            val newGrid = gridKeys.toMutableList().apply {
                remove(appKey(pkg))
                if (displaced != null) add(slot.coerceAtMost(size), appKey(displaced))
            }
            // Entries for apps the grid doesn't show (hidden ones) stay where they were relative to each other.
            val rest = cfg.order.filter { it !in newGrid && it != appKey(pkg) && it != displaced?.let(::appKey) }
            return cfg.copy(dock = dock, order = newGrid + rest, folders = cfg.folders.withoutApp(pkg))
        }

        /** Out of the tray to the front of the grid; the first other grid app takes its place (the tray stays six). */
        fun removedFromTray(cfg: LauncherConfig, apps: List<AppEntry>, pkg: String): LauncherConfig {
            val l = buildLayout(apps, cfg)
            val next = l.grid.filterIsInstance<GridItem.App>().firstOrNull { it.app.packageName != pkg }?.app?.packageName
            val shown = l.dock.map { it.packageName }.ifEmpty { cfg.dock }
            val dock = shown.map { if (it == pkg && next != null) next else it }.filter { it != pkg }
            return cfg.copy(dock = dock, order = listOf(appKey(pkg)) + (cfg.order - appKey(pkg)))
        }

        /**
         * [pkg] takes the place of [target] (the app it moves onto, as shown) in folder [folderId]; the apps between
         * shift by one. The same config back when either isn't in the folder or they are the same app.
         */
        fun movedInFolder(c: LauncherConfig, folderId: String, pkg: String, target: String): LauncherConfig {
            val folder = c.folders.firstOrNull { it.id == folderId } ?: return c
            val from = folder.apps.indexOf(pkg)
            val to = folder.apps.indexOf(target)
            if (from < 0 || to < 0 || from == to) return c
            val apps = folder.apps.toMutableList().apply { add(to, removeAt(from)) }
            return c.copy(folders = c.folders.map { if (it.id == folderId) it.copy(apps = apps) else it })
        }

        /** [movedInFolder] for one D-pad step: the folder's shown apps (installed, visible, not in the tray) in [columns] columns. */
        fun movedInFolderBy(c: LauncherConfig, installed: Set<String>, folderId: String, pkg: String, dx: Int, dy: Int, columns: Int): LauncherConfig {
            val folder = c.folders.firstOrNull { it.id == folderId } ?: return c
            // The tray as buildLayout shows it: its visible, installed apps, first DOCK_SIZE of them.
            val tray = c.dock.filter { it in installed && it !in c.hidden }.distinct().take(DOCK_SIZE).toSet()
            val shown = folder.apps.filter { it in installed && it !in c.hidden && it !in tray }.distinct()
            val i = shown.indexOf(pkg)
            if (i < 0) return c
            val last = shown.lastIndex
            val to = when {
                dx != 0 -> i + dx
                dy < 0 -> i - columns
                // A row below that is short still takes the app, at the end of it.
                dy > 0 -> if (i + columns <= last) i + columns else if (i / columns < last / columns) last else i
                else -> i
            }
            if (to !in 0..last || to == i) return c
            return movedInFolder(c, folderId, pkg, shown[to])
        }

        private fun List<Folder>.withoutApp(pkg: String) = map { it.copy(apps = it.apps - pkg) }
    }
}
