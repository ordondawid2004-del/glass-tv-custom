# M1.4.1 – rein lesendes APK-Größenprofil

`scripts/apk-size-report.py` analysiert ausschließlich die ausdrücklich angegebene
APK. Python 3.10+, vorhandenes Android SDK, apkanalyzer und apksigner sind nötig.
Der unveränderte M0-Inspektor wird als lokales Python-Modul wiederverwendet;
keine zusätzlichen Python-Pakete, private Schlüssel, ADB oder Build-Aufgaben.

```bash
python3 -B scripts/apk-size-report.py /ABSOLUT/EXAKTE.apk \
  --output /ABSOLUT/NEUER-bericht.json --sdk /ABSOLUT/android-sdk
```

Exit 0 = Profil erfolgreich erstellt, **keine Baseline-, Produktions- oder
Uploadfreigabe**. Exit 2 = Eingabe/Analyse/Ausgabe nicht zuverlässig möglich.
Vorhandene Ausgabe wird nie überschrieben. Elternverzeichnis muss existieren.
Kein automatisch gewähltes APK, kein Schreiben ins Projekt, kein Budget-Gate.

## Format tvhome.apk-size-profile/1

| Feld | Herkunft / Bedeutung |
| --- | --- |
| schema | Feste Berichtsformatversion |
| apk_observed | Aus APK/SDK: ID, VersionName/-Code, Flags, Größe, SHA-256, Signierer-DN und öffentliche Zertifikat-SHA-256. Manifest geparst, Signatur kryptografisch geprüft, alle ZIP-Einträge CRC-geprüft. |
| apk_observed.embedded_build_metadata | Falls enthalten: AGP-Versionsangabe aus signierten APK-Metadaten. Kein unabhängiger Beweis der Compiler-/Buildherkunft. |
| size_profile.groups | dex, native, resources, assets, other: gespeicherte ZIP-Bytes, entpackte Bytes, Dateianzahl, Anteil an Gesamt-APK. |
| size_profile.abi_distribution | Native Dateianzahl, gespeicherte/entpackte Bytes und APK-Anteil je ABI. |
| size_profile.container_alignment_signature_overhead | Dateigröße minus Summe gespeicherter Eintragsdaten; Header, Alignment und Signaturbereich gemeinsam. Keine erfundene Einzelzuordnung; keine entpackte Größe für diesen Rest. |
| project_evidence | Optional beobachtete Projekt-Konfigurationshashes; keine bewiesene Zuordnung dieser Quellen zu dieser APK. |
| external_provenance | Optional übergebene, nicht unabhängig überprüfte Herkunftsbehauptungen; explizit an APK-SHA gebunden. |
| analysis_tools | Python-Version, Profil-/M0-Codehash und SDK-Launcherpfade/-Hashes, Build-Tools-Verzeichnisversion. Keine Behauptung einer vollständigen SDK-/Compiler-Fingerabdruckkette. |
| comparison_context | Nicht aus APK allein belegbare Variante/Rezept/Build-Toolchain/Abhängigkeiten/Packaging bleiben null. Erforderliche spätere Vergleichsbedingungen sind benannt. |
| approval | baseline_approved, production_approved, regression_gate_executed immer false. |

DEX umfasst Dateinamen mit .dex-Suffix; native Einträge müssen lib/ABI/NAME.so sein.
Ressourcen: res/ und resources.arsc; Assets: assets/; alles andere sonstige Einträge.
Verzeichnisse zählen nicht als Dateien. Prozentwerte werden auf acht Nachkommastellen
gerundet; die Summe aller Gruppen einschließlich Overhead ist bis auf Rundung
(Prüftoleranz 0,000001 Prozentpunkte) 100. Bytes summieren exakt zur Dateigröße.
Entpackte Bytes sind weder RAM-/PSS-Messung noch garantierter Installationsplatz.

## Herkunft bewusst unterscheiden

Optional `--project-root /PFAD` liest nur sechs ausdrücklich bekannte Build-/Settings-/
Katalog-/Wrapper-Konfigurationen und erzeugt Einzel- und Gesamthashes. Es liest keinen
privaten Schlüssel und erfindet keinen Git-HEAD als vollständige M1-Quellidentität.
Die Konfiguration kann von der tatsächlich gebauten APK abweichen. Ein uncommitteter
M1-Stand ist nicht allein durch den M0-HEAD identifiziert.

Optional `--provenance /DATEI.json` nimmt ausschließlich folgende flache Angaben an:

```json
{
  "apk_sha256": "64-Hexzeichen-der-tatsächlich-untersuchten-APK",
  "application_id": "io.github.ordondawid2004del.tvhome.dev",
  "version_code": 448529,
  "variant": "developmentDebug",
  "source_reference": "vollständige Quellbaumreferenz, einschließlich uncommitteter Änderungen",
  "build_recipe": "konkrete Aufgabe und Build-Einstellungen",
  "build_toolchain": "belegte oder ausdrücklich behauptete Werkzeugstände",
  "dependencies_reference": "Referenz des aufgelösten Runtime-Graphen",
  "packaging_reference": "Referenz der ABI-/Packaging-Konfiguration"
}
```

apk_sha256 ist bei angegebener Herkunft zwingend; andere Felder sind optional.
ID und VersionCode werden, falls angegeben, mit der APK abgeglichen. Ungültige,
fehlende ausdrücklich benannte Dateien und widersprüchliche Werte führen zu Fehlern.
Ohne Herkunftsdatei bleibt die Herkunft ausdrücklich unbekannt, nicht erfolgreich
verifiziert. Auch passende externe Angaben werden **nicht** zu verifizierten
Builddaten hochgestuft. Keine Baseline wird automatisch erzeugt oder genehmigt.

## Reproduzierbarkeit, Sicherheit und Grenzen

Für gleiche Eingaben und gleiche Analyseumgebung ist die Struktur deterministisch:
keine aktuelle Uhrzeit oder laufabhängige UUID. Werkzeugpfade/-versionen können auf
anderen Rechnern variieren; sie sind keine Vergleichsmetriken. Unterschiedliche
Signierer, zeitabhängige VersionCodes, Toolchains, Abhängigkeiten, Build-Typen und
Packaging-Rezepte müssen bei einem späteren, gesondert freizugebenden Vergleich
berücksichtigt werden. Dieses Tool entscheidet keine Vergleichbarkeit/Grenzwerte.

Hash und Dateisystemidentität werden vor/nach den Lesephasen und nach Berichtserstellung
geprüft. Bei erkanntem Fehler wird der gerade angelegte Bericht entfernt; andere
Dateien werden nicht gelöscht. Exklusive Ausgabeerzeugung verhindert versehentliches
Überschreiben. SDK-Prozesse verwenden Argumentlisten und die Zeitgrenzen des M0-Prüfers.
Die Prüfung garantiert keine Gerätekompatibilität oder vollständige DEX-/Runtime-
Verifikation. Insbesondere ist ein gültig signierter Debug-Build kein Release.

Konservative Grenzen: höchstens 100.000 ZIP-Einträge, 2 GiB entpackte Eintragsdaten,
keine verschlüsselten, doppelten, unsicheren oder unbekannt komprimierten Einträge.
Bestehende M0-/M1- und Größenchecker sind unverändert; dieses Werkzeug ersetzt sie nicht.

```bash
python3 -B -m unittest discover -s scripts/tests -p test_apk_size_report.py -v
```

Die SDK-Antworten dieser Unit-Tests sind ausdrücklich simuliert. Eine reale APK-
Profilierung mit echten SDK-Werkzeugen ist getrennt nachzuweisen; kein APK-Neubau
ist dafür erforderlich. CI, Produktionssignierung, Release-Sperren und das bestehende
3.563.000-Byte-Budget werden durch das Werkzeug nicht geändert.
