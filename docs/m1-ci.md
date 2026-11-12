# CI für den lokalen M1-Stand

Diese Pipeline ist ausschließlich für `developmentDebug`, Application ID
`io.github.ordondawid2004del.tvhome.dev`, Name **TV Home – Entwicklung**.
Sie ist lokal vorbereitet; ein erfolgreicher GitHub-Actions-Lauf oder Fire-OS-Test
wird damit nicht behauptet. Die bestehenden M1-Sperren und die Versionsstrategie
bleiben unverändert. Der Versionscode ist weiterhin zeitabhängig; `447603` gehört
zur früher übergebenen APK und wird für neue Builds nicht festgeschrieben.

## Ausführung und Grenzen

`.github/workflows/build.yml` prüft Pull Requests gegen den eigenen `master` und
Pushes auf `master` oder `feature/m1-isolated-development`. Die Entwicklungsaufgabe
läuft nicht für Tags. `v*`-Tags landen ausschließlich in einer ausdrücklich
fehlschlagenden Produktionssperre: Es gibt in M1 weder Produktionsidentität noch
Produktionszertifikat oder eine freigegebene Release-Pipeline. Der bisherige
Release-Upload ist entfernt; weder Entwicklungsprüfung noch Entwicklungsschlüssel
ersetzen eine spätere Produktionsprüfung. Kein `pull_request_target`, kein
`workflow_dispatch`, keine Release-Secrets oder Schreibrechte. Checkout behält
keine Git-Zugangsdaten. Die Pipeline veröffentlicht keine GitHub-Release.

Java 21, Python 3.12 und SDK-Kommandozeilenwerkzeuge werden ausdrücklich eingerichtet.
SDK-Pakete: `cmdline-tools;latest`, `platforms;android-37.0` und
`build-tools;37.0.0`. Der Helfer verlangt Java/keytool, Python >=3.10, Plattform 37,
apkanalyzer, apksigner und aapt2; fehlende Werkzeuge führen zu Fehlern. Die lokale
Cloud-Konfiguration `/workspace/setup/glass-env.sh` ist kein Bestandteil des Workflows.

`scripts/ci-development.py` führt gerätefrei und ohne Fehlerunterdrückung aus:

1. Eigene CI-Negativtests: ausdrücklich gepflegte Sollzahl 56 (48 erhaltene plus acht neue Fälle), keine Skips und strenge Ergebnisdateiauswertung.
2. Alle **37 M0-Tests**, SDK-Integration ausdrücklich aktiviert, keine Skips erlaubt.
3. **11 M1-Manifestprüfer-Tests**, keine Skips erlaubt.
4. `assembleDevelopmentDebug`, `lintDevelopmentDebug` und die erweiterte Development-JVM-Auswahl sowie die getrennten
   Originalmodus-Prozess-/Installer-Tests. Die tatsächlich gelaufenen Fälle werden aus frischen JUnit-XMLs ermittelt.
   Es gibt keine verbundenen Tests, Emulatoraktionen oder Screenshot-Aufzeichnung.
5. Exakte APK-Auswahl einschließlich Gradle-Metadaten und Frischeprüfung.
6. M0 mit `--mode development`, fest vorgegebener M1-ID und unabhängigem Zertifikat-Pin.
   M0 nutzt apksigner zur kryptografischen Prüfung. Die Produktionsprüfung muss
   dieselbe Debug-APK weiterhin zurückweisen (erwarteter Exit 1).
7. Dekodierung des **finalen APK-Manifests** und unveränderter M1-Manifestprüfer.
8. `scripts/apk-size-check <exakte-entwicklungs-apk>` gegen das unveränderte Budget.

Gradle erhält `--rerun-tasks --no-configuration-cache` und einen ausdrücklich
angegebenen Entwicklungsschlüssel. Alte APKs oder Testberichte werden nicht als
frischer Build akzeptiert. Es gibt keinen Rückgriff auf Original-/Release-Ausgaben.
Das bestehende Originalartefakt wird nicht bereinigt oder überschrieben.

Die verbindlichen Kernklassen bleiben mit ihren bisherigen Mindestzahlen erhalten.
Die Development-Auswahl enthält zusätzlich genau diese Filter:

```text
--tests 'dev.glasslauncher.net.*'
--tests 'dev.glasslauncher.search.*'
--tests 'dev.glasslauncher.featured.YouTubeFeedTest'
--tests 'dev.glasslauncher.featured.DiskFeedCacheTest'
--tests 'dev.glasslauncher.data.ConfigStoreFullDiskTest'
--tests 'dev.glasslauncher.system.LaunchGateTest'
--tests 'dev.glasslauncher.apps.AppArtTest'
```

Original bleibt ausschließlich ApkInstallTest und RootProcessTest; diese liefern
keinen Nachweis der isolierten M1-App. Die vorhandenen sechs Development-Klassen
bleiben erhalten. Aktuell ergeben die Quellen 15 Development- und zwei
Original-Klassen; dies ist keine behauptete Laufzahl.

Die vollständige historische Projekttestsuite und pixelbasierte Prüfungen gehören
nicht zu dieser Pipeline.

## Entwicklungsschlüssel

Jeder Lauf erzeugt einen neuen, kurzlebigen **Android-Debug-Schlüssel** in einem
privaten temporären Ordner außerhalb von Checkout und Berichten. Der Soll-Pin
entsteht aus dessen separat exportiertem öffentlichen DER-Zertifikat **vor** der
APK-Prüfung, niemals aus der geprüften APK. Kein M0-Fixture-Schlüssel und kein
Produktionsschlüssel werden verwendet. Der Helfer entfernt die temporären Schlüssel
auch bei regulären Prüfungsfehlern. Runner-Abbruch/Timeout wird zusätzlich durch die
Lebensdauer des flüchtigen Actions-Runners begrenzt.

Das bewusste öffentliche Debug-Kennwort ist kein Produktionsgeheimnis.
Keystore-Dateien werden nie für den Upload vorgemerkt. `RELEASE_*`-Umgebungsvariablen
werden nicht an die Prüf-/Build-Unterprozesse weitergegeben.

**Folge:** CI-APKs unterschiedlicher Läufe sind wegen wechselnder Schlüssel nicht
als gegenseitige Updates vorgesehen. Für den späteren manuellen Paralleltest ist
der separat aufbewahrte stabile M1-Entwicklungsschlüssel zu verwenden. Eine dauerhaft
signaturstabile CI-Verteilung wäre eine eigene, später freizugebende Entscheidung.
Die lokale CI-Validierung überschreibt diesen stabilen Schlüssel nicht.

## Artefakte und Fehlerfälle

Nur wenn jede Pflichtprüfung erfolgreich war, erzeugt der Helfer `approved/`
und zuletzt `apk_ready=true`. Der Actions-Upload verlangt zusätzlich `success()`.
Der Erfolgsupload enthält genau die geprüfte APK, zugehörige Logs, Test-/Lint-Berichte,
Manifest, Summary und SHA256SUMS. Name:
`tvhome-development-<vollständiger-commit>-<run-id>-<run-attempt>-developmentDebug`.

Die APK-Datei trägt denselben Entwicklungs-/Commit-/Variantennamen. Die Summary
ordnet APK-SHA-256, Version/Versionscode, Application ID, Anzeigename, Zertifikat-Pin,
Commit, schmutzigen lokalen Arbeitsstand und Prüfergebnisse zu. Bei PRs ist der
Commit der tatsächlich von Actions ausgecheckte Prüf-/Merge-Commit.

`reports/` kann auch bei Fehlern als separates Diagnoseartefakt hochgeladen werden,
aber nur über den für diesen Aufruf ausdrücklich freigegebenen Step-Ausgabepfad.
Dort liegen weder APK noch privater Schlüssel. Beide Uploads verwenden
`if-no-files-found: error`. Fehlende, mehrdeutige, alte oder abgelehnte APKs und
fehlerhafte/übersprungene Pflichtprüfungen erzeugen keinen erfolgreichen APK-Upload.
Ein Ausgabeverzeichnis muss neu sein; alte Freigaben werden niemals wiederverwendet.

**Bekannte aktuelle Sperre:** Das unveränderte Release-Größenbudget beträgt
**3.563.000 Bytes**. Die unoptimierte M1-Debug-APK ist etwa 20,2 MB groß. Diese Pipeline
muss am Größenlimit fehlschlagen und ausschließlich Diagnosen bereitstellen.
Es wurde weder ein größeres Budget noch ein schwächerer Sonderpfad eingeführt.
Eine Lösung (gezielte Größenoptimierung oder ausdrücklich genehmigtes, begründetes
Entwicklungsbudget) ist ein separater Auftrag; nicht über eine Produktionsvariante
oder abgeschwächte Signaturprüfung umgehen.

## Lokale Validierung

In einer vorbereiteten Linux-/SDK-Umgebung, Java 21 und Python >=3.10:

```bash
python3 -B -m unittest discover -s scripts/tests -p test_ci_development.py -v
python3 -B scripts/ci-development.py run \
  --output /tmp/tvhome-ci-run-NEU --temp-dir /tmp
```

Das Ausgabeverzeichnis darf vorher nicht existieren. Ergebnis liegt unter
`reports/summary.json` und den benannten Logs. Mit dem aktuellen Größenbudget wird
Exit 1 erwartet, kein `approved/`. Vor einer lokalen Wiederholung vorhandene
Entwicklungsartefakte bei Bedarf außerhalb des Build-Verzeichnisses sichern;
die frische Development-Ausgabe wird beim ausdrücklich beauftragten Neubau ersetzt.
Keine Geräte-/Installationsskripte benutzen. Das Budget und der Original-APK-Pfad
bleiben unverändert.

## Später ausdrücklich freizugebende Schritte

M0- und M1-Diff sowie CI lokal prüfen. Commit/Push/PR/Merge bleiben getrennte,
noch nicht erteilte Freigaben. Vor einer Übernahme M0-Abhängigkeit berücksichtigen.
Actions muss im eigenen Fork zugelassen sein; hier werden keine GitHub-Einstellungen
geändert. Dass derzeit keine Läufe vorliegen, beweist weder Aktivierung noch
Deaktivierung; die Einstellung ist mit dem verfügbaren Lesezugriff nicht feststellbar.
Ein späterer Push/PR kann die vorbereitete Pipeline automatisch auslösen. Ein manueller
Lauf ist hier weder eingerichtet noch gestartet. Die Größenentscheidung muss vor einem
gewünschten erfolgreichen APK-Upload geklärt werden. Produktionsveröffentlichung und
reale Fire-TV-Tests benötigen jeweils einen eigenen Auftrag.

## M1.2: gepflegte Testquellen und strenge JUnit-Ergebnisse

`scripts/ci-jvm-scope.json` klassifiziert 228 aktuelle Kotlin-Quellen: 225 Test-/Hilfsquellen
und drei ausgeschlossene Baseline-/Benchmarkquellen. A = verbindliche M1-Regressionsklasse,
B = getrennte gerätefreie Funktions-/Legacy-/Visualregression (einschließlich der zwei
verbindlichen Originalklassen), C = geräteabhängig ausgeschlossen. Jede Quelle hat einen
Grund; jede statisch erkannte Testklasse eine eigene Rolle und Variante oder ausdrücklich
keine Ausführung. Hilfsdeklarationen ohne Testmethoden werden über Quelle und Deklarationsliste
erfasst. Eine Quelldatei kann Klassen verschiedener Rollen enthalten.

Die Kontrolle läuft verbindlich vor M0-/JVM-Build/Schlüsselerzeugung und ist eine eigene
Pflichtquittung. Neue, entfernte, verschobene oder veränderte Quellen verlangen eine
bewusste Inventarprüfung. Auch neue Quellen innerhalb net/search bleiben ohne
Klassifikation gesperrt. Nach Klassifikation werden neue Klassen dieser Paketgruppen
als erwartete JUnit-Ergebnisse berücksichtigt. Kein automatischer Inventar-Refresh im CI.

Erkennungsgrenze: Der kleine Scanner entfernt Kommentare (auch verschachtelte), gewöhnliche
und rohe Strings und erfasst benannte Deklarationen sowie direkt annotierte JUnit-4-
@Test-Funktionen in Top-Level-Testklassen. Er ist **kein Kotlin-Compiler und keine JUnit-
Discovery**. Vererbung, Meta-Annotationen, Custom Runner und verschachtelte Testklassen
sind nicht vollständig modelliert; mehrdeutige Pflichtfälle werden abgewiesen oder
spätestens als unerwartete/fehlende JUnit-Klasse blockiert. B/C-Quellen haben keine
behaupteten Laufzahlen. Änderungen selbst außerhalb der Erkennung werden durch den
vollständigen Quellenhash erkannt und müssen explizit überprüft werden. Neue Java-Quellen
sind konservativ nicht freigabefähig, bis der Scanner bewusst erweitert wird.

Source-Sets werden unter den ausdrücklich freigegebenen Settings-Projekten als test*,
androidTest sowie baselineprofile/src/main erfasst. Die Modulstruktur ist seit M1.2.1
an Settings-Hash und gepflegte Projektliste gebunden (siehe unten).
Der konfigurierte Roborazzi-Preview-Generator hat eine eigene B-Zuordnung. Sein
Kontrollhash umfasst konservativ die **gesamte app/build.gradle.kts**, daher erzwingen
auch andere Änderungen dieser Datei eine erneute Generatorprüfung. Generierte Build-
Dateien sind keine vertrauenswürdige Inventarbasis; Parameter entstehen erst zur Laufzeit.

JUnit-XML muss frisch und vollständig sein: genau die anhand der freigegebenen Quellen
und Filter erwarteten Klassen je Variante, passende testcase-classnames, eindeutige
Fallnamen, konsistente Summen und tatsächlich vorhandene Fälle. Fehler-/Skip-Zähler und
Kindknoten werden geprüft. Alle statisch belegten Pflichtmethoden müssen erscheinen;
Parameter-Suffixe [..] sind erlaubt, eine eingefrorene Gesamtfallzahl nicht. Zusätzliche
Methoden sind nach expliziter Quellen-/Inventarprüfung erlaubt. Kritische Kernklassen
behalten ihre bisherigen Mindestzahlen. Kein doppelter M1-/Originalnachweis.

Gerätefreie Einzelvalidierung ohne APK-Neubau oder kompletten Pipeline-Lauf:

```bash
python3 -B scripts/ci-development.py inventory
python3 -B scripts/ci-development.py python-tests --pattern test_ci_development.py \
  --expected 56 --result /tmp/m1-ci-selftests.json
# Nur die vereinbarten JVM-Aufgaben/Filter benutzen; kein assemble/connected/record.
# started-ns vor dem Gradle-Aufruf erfassen, nicht danach:
python3 -c 'import time; print(time.time_ns())'
python3 -B scripts/ci-development.py jvm-results --started-ns ZUVOR_ERFASSTER_WERT \
  --reports /tmp/m1-junit-evidence
```

Reale lokale JVM-Ergebnisse und simulierte Prozess-Negativtests sind im separaten
M1.2-Abschlussbericht getrennt auszuweisen. Größenbudget, Wiederholungsdiagnosen,
Signierung und App-Sicherheitsgrenzen werden hier nicht verändert.

## M1.2.1: Settings-/Modulstruktur verbindlich prüfen

Das bisherige feste MODULES-Tupel konnte ein zusätzliches Gradle-Projekt ignorieren.
`module_structure` in `scripts/ci-jvm-scope.json` enthält nun den festen Pfad
settings.gradle.kts, den vollständigen SHA-256 seiner Bytes, eine explizit gepflegte,
sortierte Projektliste und einen Reviewgrund. Aktuell sind addon-api, addon-sample,
app, baselineprofile und browser genehmigt. Die 228 Quellen bleiben unverändert.

Jede Settings-Änderung (auch Kommentare/Reihenfolge) scheitert zunächst am Hash.
Zusätzlich muss die kleine statische Prüfung die expliziten Projekte widerspruchsfrei
ermitteln können: nur flache literale include(":module")-Aufrufe auf oberster Ebene
mit eindeutigen Namen werden unterstützt. Auch mehrere literale Argumente sind möglich.
Variablen, verschachtelte/beschränkte Einbindungen, includeBuild/includeFlat,
project-/projectDir-Overrides und apply/evaluate werden konservativ zurückgewiesen.
Fehlende Settings, fehlende Modulverzeichnisse, Symlinks, doppelte Module oder
ungültige Metadaten liefern einen verständlichen Fehler. Gradle wird dafür nie evaluiert.

Dies ist bewusst kein vollständiger Kotlin-/Gradle-Parser. Der genehmigte vollständige
Settings-Hash bleibt die Reviewgrenze für sonstigen ausführbaren Settings-Code;
die Literalprüfung ersetzt keine fachliche Prüfung von Plugins/indirekter Settings-Logik.
Andere Einbindungsmodelle und verschachtelte Projekte benötigen einen getrennten,
bewusst freizugebenden Prüfer-Ausbau und werden nicht automatisch akzeptiert.

Bei einer bewusst aufgenommenen neuen flachen Modulbindung: zuerst Settings-Code und
Modulinhalt fachlich prüfen, anschließend Hash UND Projektliste aktualisieren und
sämtliche neuen Test-/Hilfsquellen und Testklassen klassifizieren. Nur den Hash zu
ersetzen oder nur die Projektliste zu ergänzen genügt nicht. Die tatsächliche
Quellensuche folgt erst der so bestätigten Projektliste; eine zweite versteckte
Fünf-Modul-Liste gibt es nicht mehr. Kein automatischer Hash-/Inventar-Refresh im CI.

Die Kontrolle bleibt Bestandteil der Inventar-Pflichtquittung vor Gradle, Signierung
und Freigabe. Die acht neuen Selbsttestfälle verwenden ausschließlich isolierte
Settings-/Quell-Fixtures. Der Pipeline-Negativnachweis führt echte Settings-Prüfung
bei simulierten externen Prozessen aus; er ist kein Actions- oder Gradle-Lauf.
Python-Sollzahl nun 48; No-Skip-/Vollständigkeitskontrolle unverändert streng.
Die M1.2-JVM-Filter und JUnit-Auswertung bleiben unverändert. Historische 91/27-JVM-
Ergebnisse sind kein neuer Ausführungsnachweis dieser kleinen Korrektur.

## M1.3: aktuelle Diagnosen statt wiederverwendeter Ordner

Der Workflow reserviert mit mktemp -d für jeden verify-Aufruf einen privaten neuen
Elternordner unter RUNNER_TEMP und nutzt dessen noch nicht vorhandenen Unterordner
output. Der Helfer reserviert diesen exklusiv; fremde vorhandene Ordner werden weder
übernommen noch gelöscht. Das Verhalten ist auch bei gleichem Commit oder erneutem
GitHub-Laufversuch eindeutig. Artefaktnamen tragen jetzt Commit, github.run_id und
github.run_attempt. Der APK-Dateiname selbst bleibt unverändert.

Vor jeder Verzeichnisprüfung setzt der Helfer seine Step-Outputs apk_ready und
diagnostics_ready auf false und die Pfade auf leer. Nach exklusiver Erzeugung von
reports/run.json mit UUID, Startzeit und GitHub-Lauf-/Versuchs-/Job-/Commit-Metadaten
werden nur die frischen Pfade und diagnostics_ready=true ausgegeben. summary.json
trägt dieselbe diagnostic_run-Kennung. Ein Abbruch vor run.json bietet keine Diagnose
an; bei späterem Abbruch bleibt zumindest der aktuelle, ausdrücklich als started
gekennzeichnete Laufdatensatz erhalten. Er behauptet keine bestandenen Prüfungen.

Diagnoseupload: always() UND diagnostics_ready=true, ausschließlich der dynamische
reports_dir dieses Steps. Bei fehlenden aktuellen Dateien bleibt if-no-files-found:error
verbindlich. Es gibt keinen Ersatzpfad und keinen Rückfall auf alte Dateien. Reuse-
Ablehnung setzt keine neuen Diagnosepfade; alte Daten bleiben unverändert liegen und
werden nicht erneut angeboten. Bestehende Dateien werden nicht durch Cleanup entsorgt.

APK-Upload: weiterhin success() UND apk_ready=true; output_dir/approved ist der
zugehörige dynamische Pfad. Die Freigabefunktion und sämtliche Pflichtquittungen,
Signatur-/Manifest-/Größenprüfungen sind unverändert. Fehlgeschlagene oder vorzeitig
abgebrochene Läufe können keine erfolgreiche APK-Freigabe erzeugen.

56 ausdrücklich erwartete Python-Selbsttests (48 erhalten plus acht neue) prüfen
frische/erneute Ordner, frühe/späte Fehler, fehlenden Marker, eindeutige Kennungen,
Fremddatenerhaltung und Workflow-Bedingungen. Externe Pipeline-Prozesse in den
Negativtests sind simuliert; es wird kein tatsächlicher Actions-Erfolg behauptet.
Eine harte Beendigung kann den aktuellen started-Datensatz ohne abschließende Summary
hinterlassen; dessen Status/UUID bleiben eindeutig. Ausfälle von GitHub oder Dateisystem
können die Diagnoseübertragung verhindern, führen aber nicht zu einem Ersatz durch
historische Reports. Kein echter Actions-/Upload-Lauf wurde hier ausgeführt.
