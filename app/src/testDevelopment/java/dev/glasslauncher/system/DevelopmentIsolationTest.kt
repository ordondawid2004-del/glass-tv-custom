package dev.glasslauncher.system

import android.content.ComponentName
import android.content.Intent
import android.content.pm.PackageInstaller
import android.content.pm.PackageManager
import android.os.Looper
import android.provider.Settings
import dev.glasslauncher.BuildConfig
import dev.glasslauncher.GlassApp
import dev.glasslauncher.MainActivity
import dev.glasslauncher.dream.Screensaver
import dev.glasslauncher.downloads.Downloads
import dev.glasslauncher.downloads.browser.BrowserLink
import kotlinx.coroutines.runBlocking
import okhttp3.OkHttpClient
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import java.io.File
import java.util.concurrent.atomic.AtomicInteger

/** The tripwire is installed BEFORE the real Application.onCreate; it never executes a process. */
class GuardedDevelopmentApplication : GlassApp() {
    val processAttempts = AtomicInteger()
    override fun onCreate() {
        ProcessBoundary.factory = { processAttempts.incrementAndGet(); throw AssertionError("Process execution attempted") }
        // Simulate hostile/stale preferences before startup.
        getSharedPreferences("root", 0).edit().putBoolean("takeover", true)
            .putBoolean("fast", true).putBoolean("ownRemotePairing", true).commit()
        getSharedPreferences("amazon-store", 0).edit().putBoolean("open", true).commit()
        super.onCreate()
    }
}

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [35], application = GuardedDevelopmentApplication::class)
class DevelopmentIsolationTest {
    private val app get() = RuntimeEnvironment.getApplication() as GuardedDevelopmentApplication
    private val own = "io.github.ordondawid2004del.tvhome.dev"

    @Test fun ownIdentityAndActualActivityClassResolve() {
        assertEquals(own, BuildConfig.APPLICATION_ID)
        assertEquals(own, app.packageName)
        assertEquals("TV Home – Entwicklung", app.getString(dev.glasslauncher.R.string.app_name))
        val component = ComponentName(app, MainActivity::class.java)
        assertEquals("$own/dev.glasslauncher.MainActivity", component.flattenToString())
        assertEquals("dev.glasslauncher.MainActivity", app.packageManager.getActivityInfo(component, 0).name)
        assertEquals(component, HomeSetup.homeIntent(app).component)
        assertFalse(HomeSetup.homeIntent(app).hasCategory(Intent.CATEGORY_HOME))
        assertTrue(MainActivity.ACTION_SETTINGS.startsWith("$own."))
    }

    @Test fun manualLaunchersResolveButHomeDoesNot() {
        for (category in listOf(Intent.CATEGORY_LAUNCHER, Intent.CATEGORY_LEANBACK_LAUNCHER)) {
            assertTrue(app.packageManager.queryIntentActivities(Intent(Intent.ACTION_MAIN).addCategory(category).setPackage(own), 0).isNotEmpty())
        }
        for (category in listOf(Intent.CATEGORY_HOME, "fireos.intent.category.MAIN_HOME")) {
            assertTrue(app.packageManager.queryIntentActivities(Intent(Intent.ACTION_MAIN).addCategory(category).setPackage(own), 0).isEmpty())
        }
    }

    @Test fun manifestHasOnlyOwnDeclarationsAndNoServices() {
        val info = app.packageManager.getPackageInfo(own, PackageManager.GET_PERMISSIONS or PackageManager.GET_SERVICES or PackageManager.GET_PROVIDERS or PackageManager.GET_RECEIVERS)
        assertNull(info.sharedUserId)
        assertTrue(info.services.isNullOrEmpty())
        assertTrue(info.receivers.isNullOrEmpty())
        assertTrue(info.permissions.orEmpty().isNotEmpty())
        info.permissions.orEmpty().forEach { assertTrue(it.name.startsWith("$own.")) }
        info.providers.orEmpty().forEach { assertTrue(it.authority.startsWith("$own.")) }
        val allowed = setOf("android.permission.INTERNET", "android.permission.ACCESS_NETWORK_STATE", "$own.DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION")
        assertEquals(allowed, info.requestedPermissions.orEmpty().toSet())
    }

    @Test fun startupAndResumeIgnoreEnablingConfiguration() = runBlocking {
        val cr = app.contentResolver
        Settings.Secure.putString(cr, "enabled_accessibility_services", "existing/Service")
        Settings.Secure.putInt(cr, "accessibility_enabled", 0)
        Settings.Global.putFloat(cr, "window_animation_scale", 1.25f)
        app.config.update { it.copy(homeGuard = true, instantBack = true, remoteButtons = mapOf("test" to "home")) }
        shadowOf(Looper.getMainLooper()).idle()
        val controller = Robolectric.buildActivity(MainActivity::class.java).create().start().resume()
        controller.pause().stop().restart().start().resume()
        assertEquals("existing/Service", Settings.Secure.getString(cr, "enabled_accessibility_services"))
        assertEquals(0, Settings.Secure.getInt(cr, "accessibility_enabled"))
        assertEquals(1.25f, Settings.Global.getFloat(cr, "window_animation_scale"), 0f)
        assertNull(shadowOf(app).nextStartedService)
        assertEquals(0, app.processAttempts.get())
        assertFalse(KeysService.homeCard.value)
        assertFalse(KeysService.card.value)
        assertTrue(app.getSharedPreferences("amazon-store", 0).getBoolean("open", false))
        controller.pause().stop().destroy()
        Unit
    }

    @Test fun rootProbeAndAllProcessPathsAreBlockedBeforeFactory() = runBlocking {
        Root::class.java.getDeclaredField("granted").apply { isAccessible = true }.set(null, true)
        assertFalse(Root.known)
        assertFalse(Root.available())
        assertFalse(Root.recheck())
        assertFalse(Root.known)
        assertFalse(Root.run("id").ok)
        assertNull(Root.readBytes("/never-read"))
        val file = File(app.cacheDir, "input").apply { writeText("unchanged") }
        assertEquals(126, Root.runWithInput("unused", file).code)
        assertEquals(126, Root.runProcess(listOf("unused"), file, 1).code)
        assertTrue(runCatching { ProcessBoundary.start(listOf("unused")) }.exceptionOrNull() is IllegalStateException)
        assertEquals("unchanged", file.readText())
        assertEquals(0, app.processAttempts.get())
    }

    @Test fun directSystemEntrancesCannotWriteSettingsOrLaunchActivities() = runBlocking {
        val cr = app.contentResolver
        Settings.Secure.putString(cr, "enabled_accessibility_services", "existing/Service")
        Settings.Secure.putString(cr, "screensaver_components", "existing/Dream")
        Settings.Secure.putLong(cr, "sleep_timeout", 123456L)
        assertFalse(HomeSetup.setGuardEnabled(app, true))
        assertNull(HomeSetup.requestDefaultHomeIntent(app))
        HomeSetup.startRequest(app, Intent(Settings.ACTION_HOME_SETTINGS))
        KeysService.check(app)
        RootFeatures.keepHome(app)
        RootFeatures.reapplyAtStart(app)
        assertFalse(Screensaver.setAsSystemScreensaver(app))
        assertFalse(Screensaver.setAlwaysOn(app, true))
        AmazonStore.close(app, bringHome = true)
        assertFalse(AmazonStore.open(app))
        assertEquals("existing/Service", Settings.Secure.getString(cr, "enabled_accessibility_services"))
        assertEquals("existing/Dream", Settings.Secure.getString(cr, "screensaver_components"))
        assertEquals(123456L, Settings.Secure.getLong(cr, "sleep_timeout"))
        assertNull(shadowOf(app).nextStartedActivity)
        assertEquals(0, app.processAttempts.get())
    }

    @Test fun updaterMakesNoNetworkRequestAndStagesNoFile() = runBlocking {
        val requests = AtomicInteger()
        val client = OkHttpClient.Builder().addInterceptor { requests.incrementAndGet(); error("HTTP attempted") }.build()
        val before = app.cacheDir.walkTopDown().filter { it.isFile }.map { it.path }.toSet()
        assertNull(Updater.latest(client))
        assertFalse(Updater.canInstall(app))
        val failure = runCatching { Updater.downloadAndInstall(app, client, Release("99", "https://invalid.example/app.apk", "")) { fail("Progress callback") } }.exceptionOrNull()
        assertTrue(failure is IllegalStateException)
        assertEquals(0, requests.get())
        assertEquals(before, app.cacheDir.walkTopDown().filter { it.isFile }.map { it.path }.toSet())
        assertEquals("", BuildConfig.UPDATE_REPO)
    }

    @Test fun packageInstallerAndResultReceiverRemainInactive() = runBlocking {
        val installer = app.packageManager.packageInstaller
        val before = installer.mySessions.size
        val attempts = AtomicInteger()
        val tripwire = object : android.content.ContextWrapper(app) {
            override fun getPackageManager(): PackageManager { attempts.incrementAndGet(); error("Installer lookup attempted") }
        }
        assertTrue(ApkInstall.commit(tripwire, File(app.cacheDir, "missing.apk"), userAction = false, await = false) is InstallResult.Failed)
        assertEquals(0, attempts.get())
        assertTrue(ApkInstall.commit(app, File(app.cacheDir, "missing.apk"), userAction = false, await = false) is InstallResult.Failed)
        assertEquals(before, installer.mySessions.size)
        InstallResultReceiver().onReceive(app, Intent().putExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_PENDING_USER_ACTION)
            .putExtra(Intent.EXTRA_INTENT, Intent("unexpected.install")))
        assertNull(shadowOf(app).nextStartedActivity)
    }

    @Test fun installOrchestrationNeverReachesAnyEdgeEvenWithElevatedMode() = runBlocking {
        val calls = mutableListOf<String>()
        val edges = object : ApkInstall.Edges {
            override suspend fun mode() = InstallMode.Os.also { calls += "mode" }
            override fun canInstall() = true.also { calls += "canInstall" }
            override fun unknownSources() = Intent().also { calls += "settings" }
            override suspend fun rootInstall(file: File) = Root.Result(0, "Success").also { calls += "root" }
            override fun installed(pkg: String): ApkInstall.Installed? = null.also { calls += "installed" }
            override fun now() = 0L.also { calls += "now" }
            override suspend fun commit(file: File, userAction: Boolean) = InstallResult.Success.also { calls += "commit" }
            override fun hash(file: File) = "hash".also { calls += "hash" }
            override fun log(line: String) { calls += "log" }
        }
        val staged = StagedApk(File("never-read.apk"), "hash", ApkInfo("p", "P", "1", 1, 1, listOf("a"), SignerCheck.NotInstalled))
        assertTrue(ApkInstall.install(edges, staged) is InstallResult.Failed)
        assertTrue(calls.isEmpty())
    }

    @Test fun companionTransportsAndDownloadsCannotStart() {
        val before = shadowOf(app).broadcastIntents.size
        val target = ComponentName("com.phairplay.firetv", "com.phairplay.service.ControlReceiver")
        assertNull(AirPlay.receiver(app))
        assertFalse(AirPlay.query(app, target))
        assertFalse(AirPlay.set(app, target, true))
        assertFalse(BrowserLink.available(app))
        BrowserLink.open(app)
        Downloads.retryInForeground = true
        Downloads.resumePending(app)
        Downloads.onForeground(app)
        Downloads.startService(app)
        assertEquals(before, shadowOf(app).broadcastIntents.size)
        assertNull(shadowOf(app).nextStartedActivity)
        assertNull(shadowOf(app).nextStartedService)
        assertTrue(Downloads.folder(app).canonicalPath.startsWith(app.filesDir.canonicalPath + File.separator))
        // Even a direct service invocation cannot create a channel, go foreground or load a queue.
        val service = Robolectric.buildService(dev.glasslauncher.downloads.DownloadService::class.java).create()
        assertEquals(android.app.Service.START_NOT_STICKY, service.get().onStartCommand(null, 0, 1))
        val manager = app.getSystemService(android.app.NotificationManager::class.java)
        assertTrue(manager.notificationChannels.none { it.id == "downloads" })
        assertTrue(manager.activeNotifications.isEmpty())
        service.destroy()
    }

    @Test fun backupsAndConfigImportCannotReadOrWriteSharedData() = runBlocking {
        assertNull(Backup.read(app))
        assertNull(Backup.readUri(app, android.net.Uri.parse("content://original/backup")))
        assertTrue(runCatching { Backup.export(app, "secret") }.isFailure)
        assertTrue(runCatching { app.config.import("{\"homeGuard\":true}") }.isFailure)
    }

    @Test fun deniedPublicInstallPreservesTheInputApk() = runBlocking {
        val file = File(app.cacheDir, "existing.apk").apply { writeText("must stay intact") }
        val staged = StagedApk(file, "unused", ApkInfo("p", "P", "1", 1, 1, listOf("a"), SignerCheck.NotInstalled))
        assertTrue(ApkInstall.install(app, staged) is InstallResult.Failed)
        assertEquals("must stay intact", file.readText())
    }

    @Test fun privatePathsRejectTraversalAndSiblingDirectories() {
        assertTrue(RuntimeSafety.privateFile(app, File(app.filesDir, "wallpapers/test")))
        assertTrue(RuntimeSafety.privateFile(app, File(app.cacheDir, "test")))
        assertFalse(RuntimeSafety.privateFile(app, File(app.filesDir, "../foreign")))
        assertFalse(RuntimeSafety.privateFile(app, File(app.filesDir.path + "-other/file")))
    }

    @Test fun appManagementNeverLaunchesDetailsOrUninstall() {
        val model = dev.glasslauncher.home.HomeModel(app)
        for (pkg in listOf("dev.glasslauncher", "com.amazon.tv.launcher", "any.other.app")) {
            val entry = dev.glasslauncher.apps.AppEntry(pkg, pkg, ComponentName(pkg, "$pkg.Main"))
            model.appInfo(entry)
            model.uninstall(entry)
            assertFalse(runBlocking { RecentApps.stop(app, pkg) })
        }
        assertNull(shadowOf(app).nextStartedActivity)
        assertEquals(0, app.processAttempts.get())
    }

    @Test fun addonDiscoveryBindingAndProviderAreBlockedBeforeAndroidCalls() = runBlocking {
        val calls = AtomicInteger()
        val context = object : android.content.ContextWrapper(app) {
            override fun getPackageManager(): PackageManager { calls.incrementAndGet(); error("Package query attempted") }
            override fun bindService(intent: Intent, connection: android.content.ServiceConnection, flags: Int): Boolean {
                calls.incrementAndGet(); error("Service binding attempted")
            }
            override fun getContentResolver(): android.content.ContentResolver { calls.incrementAndGet(); error("Provider access attempted") }
        }
        val info = dev.glasslauncher.addons.AddonInfo("companion", "companion.Service", "Companion", 1, 1, true, "cert", true, "companion.provider")
        assertTrue(dev.glasslauncher.addons.AddonScanner.scan(context).isEmpty())
        assertNull(dev.glasslauncher.addons.AddonClient.call(context, info, 1, "{}"))
        assertNull(dev.glasslauncher.addons.AddonClient.provider(context, info, "method", "{}"))
        assertNull(dev.glasslauncher.addons.AddonArt.fetch(context, "https://invalid.example/art.png"))
        assertFalse(BrowserLink.available(context))
        assertNull(AirPlay.receiver(context))
        assertEquals(0, calls.get())
    }
}
