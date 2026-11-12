#!/usr/bin/env python3
"""M1 allowlist for a merged or APK-decoded manifest. No device access; standard library only."""
import argparse
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

APP = "io.github.ordondawid2004del.tvhome.dev"
ANDROID = "{http://schemas.android.com/apk/res/android}"


def signature_level(value):
    if value == "signature":
        return True
    try:
        return int(value, 0) == 2
    except (TypeError, ValueError):
        return False


def audit(xml):
    root = ET.fromstring(xml)
    errors = []

    def require(condition, message):
        if not condition:
            errors.append(message)

    require(root.tag == "manifest" and root.get("package") == APP, "Falsche Application ID")
    require(root.get(ANDROID + "sharedUserId") is None, "Gemeinsame UID ist verboten")
    require(not root.findall("permission-tree"), "Permission-Trees sind verboten")
    expected_permission = APP + ".DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION"
    declared = root.findall("permission")
    require(len(declared) == 1 and declared[0].get(ANDROID + "name") == expected_permission,
            "Unerwartete eigene Berechtigungsdeklaration")
    if declared:
        require(signature_level(declared[0].get(ANDROID + "protectionLevel")),
                "Eigene Berechtigung muss signature-geschützt sein")
    requested = {n.get(ANDROID + "name", "") for n in root if n.tag.startswith("uses-permission")}
    require(requested == {"android.permission.INTERNET", "android.permission.ACCESS_NETWORK_STATE", expected_permission},
            "Unerwartete angeforderte Berechtigungen: " + ", ".join(sorted(requested - {
                "android.permission.INTERNET", "android.permission.ACCESS_NETWORK_STATE", expected_permission})))
    require(not any(n.get(ANDROID + "name") in ("android.intent.category.HOME", "fireos.intent.category.MAIN_HOME")
                    for n in root.iter("category")), "Home-Kategorie vorhanden")
    apps = root.findall("application")
    require(len(apps) == 1, "Genau eine Application ist erforderlich")
    if len(apps) != 1:
        return errors
    app = apps[0]
    require(app.get(ANDROID + "name") == "dev.glasslauncher.GlassApp", "Unerwartete Application-Klasse")
    require(app.get(ANDROID + "allowBackup") == "false", "Backup muss ausgeschaltet sein")
    require(app.get(ANDROID + "debuggable") == "true", "M1 muss ausdrücklich als Debug-Build markiert sein")
    for tag in ("service", "receiver", "activity-alias"):
        require(not app.findall(tag), f"Unerwartete Komponenten: {tag}")
    activities = app.findall("activity")
    require(len(activities) == 1 and activities[0].get(ANDROID + "name") == "dev.glasslauncher.MainActivity",
            "Nur die tatsächliche MainActivity darf registriert sein")
    if len(activities) == 1:
        activity = activities[0]
        require(activity.get(ANDROID + "exported") == "true", "MainActivity ist nicht manuell startbar")
        filters = activity.findall("intent-filter")
        require(len(filters) == 1, "Unerwartete Activity-Intentfilter")
        require({n.get(ANDROID + "name") for n in activity.iter("action")} == {"android.intent.action.MAIN"}, "Falsche Startaktion")
        require({n.get(ANDROID + "name") for n in activity.iter("category")} == {
            "android.intent.category.LAUNCHER", "android.intent.category.LEANBACK_LAUNCHER"}, "Falsche Startkategorien")
    providers = app.findall("provider")
    require(len(providers) == 1, "Nur der interne AndroidX-Startup-Provider ist erlaubt")
    for provider in providers:
        require(provider.get(ANDROID + "name") == "androidx.startup.InitializationProvider"
                and provider.get(ANDROID + "authorities") == APP + ".androidx-startup"
                and provider.get(ANDROID + "exported") == "false", "Unerwarteter oder öffentlicher Provider")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="Mit apkanalyzer manifest print erzeugtes XML oder zusammengeführtes Manifest")
    args = parser.parse_args()
    try:
        errors = audit(args.manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError, ET.ParseError) as exc:
        print(f"Ergebnis: NICHT PRÜFBAR – {exc}")
        return 2
    if errors:
        print("Ergebnis: M1-MANIFEST ABGELEHNT")
        for error in errors:
            print("- " + error)
        return 1
    print("Ergebnis: M1-MANIFEST OK – eigene Identität, nur manueller Start, private Authority, keine Systemdienste/privilegierten Berechtigungen")
    return 0


if __name__ == "__main__":
    sys.exit(main())
