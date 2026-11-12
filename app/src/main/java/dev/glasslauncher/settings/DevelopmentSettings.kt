package dev.glasslauncher.settings

import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.focusRequester
import androidx.tv.material3.Text
import dev.glasslauncher.BuildConfig
import dev.glasslauncher.data.Auto
import dev.glasslauncher.data.LauncherConfig
import dev.glasslauncher.data.ThemeMode
import dev.glasslauncher.home.HomeModel
import dev.glasslauncher.home.MenuList
import dev.glasslauncher.home.PanelTitle
import dev.glasslauncher.ui.MenuRow

/** M1 exposes only settings stored in this app's sandbox. System features return in later milestones. */
@Composable
internal fun DevelopmentSettings(model: HomeModel, cfg: LauncherConfig, active: Boolean) {
    MenuList(active) { first ->
        PanelTitle("TV Home – Entwicklung")
        MenuRow("Darstellung: ${if (cfg.theme == ThemeMode.Light) "Hell" else "Dunkel"}", {
            model.edit { it.copy(theme = if (it.theme == ThemeMode.Light) ThemeMode.Dark else ThemeMode.Light) }
        }, Modifier.focusRequester(first))
        MenuRow("Animationen: ${if (cfg.reduceMotion == Auto.On) "Reduziert" else "Normal"}", {
            model.edit { it.copy(reduceMotion = if (it.reduceMotion == Auto.On) Auto.Off else Auto.On) }
        })
        if (cfg.hidden.isNotEmpty()) MenuRow("Ausgeblendete Apps wieder anzeigen", { model.edit { it.copy(hidden = emptySet()) } })
        Text("Entwicklungsversion ${BuildConfig.VERSION_NAME}")
        Text("Diese App wird manuell geöffnet. Die Home-Taste behält ihre bisherige Funktion.")
    }
}
