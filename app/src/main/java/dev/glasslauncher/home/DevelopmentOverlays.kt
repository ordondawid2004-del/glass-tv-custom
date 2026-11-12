package dev.glasslauncher.home

/** Allowlist: newly added panels must be reviewed before they become reachable in M1. */
internal fun developmentOverlayAllowed(overlay: Overlay): Boolean = when (overlay) {
    Overlay.Settings, Overlay.Search,
    is Overlay.AppMenu, is Overlay.MoveTo, is Overlay.FolderMenu, is Overlay.FolderOpen,
    is Overlay.FolderPicker, is Overlay.TextInput, is Overlay.Confirm -> true
    else -> false
}
