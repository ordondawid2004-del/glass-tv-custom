package dev.glasslauncher.home

import android.view.KeyEvent
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.test.core.app.ActivityScenario
import dev.glasslauncher.MainActivity
import dev.glasslauncher.shots.PinnedClockRule
import dev.glasslauncher.shots.TvHarness
import dev.glasslauncher.shots.waitForHome
import dev.glasslauncher.system.GuardedDevelopmentApplication
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

/** Real Compose Home on Robolectric with fake installed apps; no emulator, network or device. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [35], application = GuardedDevelopmentApplication::class, qualifiers = "w960dp-h540dp-land-television-xhdpi")
class DevelopmentUiTest {
    @get:Rule val compose = createEmptyComposeRule()
    @get:Rule val clock = PinnedClockRule()

    @Test fun remoteSettingsKeyOpensOnlyPrivateDevelopmentSettings() {
        TvHarness.setUp(featured = false)
        ActivityScenario.launch(MainActivity::class.java).use { scenario ->
            compose.waitForHome()
            scenario.onActivity {
                it.dispatchKeyEvent(KeyEvent(KeyEvent.ACTION_DOWN, KeyEvent.KEYCODE_SETTINGS))
                it.dispatchKeyEvent(KeyEvent(KeyEvent.ACTION_UP, KeyEvent.KEYCODE_SETTINGS))
            }
            compose.waitForIdle()
            compose.onNodeWithText("TV Home – Entwicklung").assertExists()
            compose.onNodeWithText("Darstellung: Dunkel").assertExists()
            compose.onNodeWithText("Uninstall").assertDoesNotExist()
            compose.onNodeWithText("AirPlay").assertDoesNotExist()
            compose.onNodeWithText("Make VITRE your Home").assertDoesNotExist()
            val app = RuntimeEnvironment.getApplication() as GuardedDevelopmentApplication
            assertEquals(0, app.processAttempts.get())
            org.junit.Assert.assertNull(shadowOf(app).nextStartedService)
        }
    }
}
