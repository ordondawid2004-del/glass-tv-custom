# M1.4.4 – Vertrag für die spätere Development-Größenprüfung

Stand 08.11.2026. **Entscheidungsvorlage, keine aktivierte Regel.** Die Wörter
MUSS und DARF NICHT beschreiben den vorgesehenen Implementierungsvertrag.
Dieser Text genehmigt weder eine Baseline noch einen Upload oder Grenzwert.

## 1. Entscheidung und Sicherheitsgrenze

Vorgesehen ist ein eigenes Development-Regressions-Gate mit separat freigegebener
CI-Referenz. `perf-budget.json` und `scripts/apk-size-check` bleiben für Release
unverändert streng bei **3.563.000 Bytes**. Ein späterer Größenprüfer darf die
heutige Prüfung für `developmentDebug` erst nach ausdrücklicher Genehmigung der
Ersatzregel ersetzen. Alle übrigen M0-/M1-/Test-/Manifest-/Signaturgates bleiben
zusätzliche zwingende Bedingungen. Heute bleibt die bisherige Blockade aktiv.

**Standardempfehlung zur einzigen offenen Größenentscheidung:** Zunächst keine
positive Größenzunahme gegenüber einer ausdrücklich genehmigten CI-Referenz
automatisch zulassen: `candidate.file_bytes <= reference.file_bytes`. Kein
Prozentpuffer, kein Technikbonus, kein selbst wachsender Ratchet. Dies ist eine
vorgeschlagene Qualitätsregel, keine aus drei lokalen Stichproben abgeleitete
Variationstoleranz und heute noch nicht beschlossen oder implementiert.

Auswirkungen: Auch legitime Funktionszunahmen und technisch bedingte Mehrbytes
können blockieren. Bei gewünschtem Wachstum muss der Inhaber eine neue konkrete
Referenz nach demselben Freigabeverfahren billigen. Ein erklärter Bytezuwachs
führt nicht von selbst zur Ausnahme. Damit kann die erste sichere Implementierung
ohne einen erfundenen positiven Toleranzwert erfolgen.

Vier Fälle sind getrennt zu behandeln:

| Fall | Vorgesehenes Verhalten |
| --- | --- |
| Gleiche Inputs, variable Versions-/Signaturmetadaten | Rohgröße messen und Unterschiede dokumentieren; kein Abziehen vermeintlicher Metadatenbytes. Auch technische Mehrbytes fallen unter die vorgeschlagene strenge Regel. |
| Neue Funktion, gleiche kontrollierte Buildumgebung | Andere Quellen sind zulässig; die Rohgröße muss die freigegebene Vergleichsbedingung erfüllen. |
| Neue Dependency oder anderes Werkzeug-/Packagingrezept | Zunächst nicht ausreichend vergleichbar; kontrollierte neue Referenz und explizite Aktualisierung der vertrauenswürdigen Eingaben erforderlich. |
| Bewusst gewünschtes Produktwachstum | Getrennte manuelle Referenzfreigabe nach Prüfung des konkreten Zuwachses; kein Automatismus im Kandidatenlauf. |

## 2. Verifizierter lokaler Ausgangsstand

Repository `ordondawid2004-del/glass-tv-custom`, Branch
`feature/m1-isolated-development`, HEAD/M0-Basis
`b5ef4d4ac0cfc23d19348c5556193629c49f971c`. Die 826 vor M1.4.4 vorhandenen
Quelldateien stimmen mit der M1.4.2-Inventur plus den drei M1.4.3-Ergänzungen
überein. Der Entwicklungsstand ist weiterhin uncommittet. Ein zukünftiger
Actions-Checkout dieses M0-HEAD enthält diese Änderungen deshalb nicht automatisch.
Eine Referenz verlangt einen vollständig gesicherten, geprüften Quellcommit;
dessen Veröffentlichung ist kein Bestandteil dieses Auftrags.

Vorhandene Nachweise wurden gelesen und an ihre Dateihashes gebunden:

- M1.4.2: drei lokale APKs je 20.238.741 Bytes, VersionCodes 448575/448577/448578,
  unterschiedliche APK-Hashes, gleiche dokumentierte Quellinventur und stabiler
  lokaler Entwicklungssignierer. Warm-Cache-Assemblierungen: jeweils 32 Aufgaben
  ausgeführt, 24 aus dem Cache. Kein Nachweis einer identischen Actions-Größe.
- M1.4.2 enthält Logs, drei Profile, Quellinventur, Inhalts-/DEX-Vergleiche und eine
  Zusammenfassung. Deren Dependency-Graphhash ist kein Artefaktbyte-Nachweis.
- M1.4.3: vorhandenes Ergebnisprotokoll über 60 Tests ohne Fehler/Skips und zwei
  reale Vergleiche mit 0 Bytes Differenz. Beide Berichte melden
  `insufficient_documentation`, weil ihre Profile keine Dependencyreferenz
  enthalten. Diese historischen Tests/Vergleiche wurden hier nicht neu ausgeführt.

Die lokalen Profile bleiben unverändert und werden **nicht** nachträglich zu
CI-Referenzen umetikettiert oder mit angeblich schon verifizierter Herkunft ergänzt.

## 3. Tatsächliche CI-/Buildregeln und ihre Konsequenzen

| Fundstelle im aktuellen Quellstand | Tatsächliche Regel | Konsequenz für die spätere Integration |
| --- | --- | --- |
| `.github/workflows/build.yml:3–15` | Push auf master/Featurebranch, PR gegen master, Tags gesondert blockiert | Events getrennt behandeln; PR-/Featurelauf darf keine autoritative APK-Freigabe erzeugen. |
| `build.yml:16–34` | ubuntu-latest; Java 21, Python 3.12; Actions über bewegliche Major-Tags; cmdline-tools latest; SDK 37 | Noch keine unveränderliche Runner-/Toolchainbindung. Action-Commits, exakte Versionen und Dateihashes müssen vor Aktivierung feststehen. |
| `build.yml:19–21,32–34` | Checkout ohne persistierte Credentials; Gradle-Cache nur lesend | Beibehalten. Für die erste Referenzklasse keine wiederverwendeten Taskoutputs verwenden. |
| `build.yml:35–55` | Neuer mktemp-Ausgabeort; Erfolgsupload nur success UND apk_ready; Diagnostik separat, nur wenn bereit | M1.3-Schutz bleibt: keine alten Ausgabeorte, kein APK in Diagnoseartefakten, keine veralteten Berichte. |
| `build.yml:57–64` | Tagjob endet mit Fehler | Unverändert; Größenfreigabe bedeutet niemals Release. |
| `scripts/ci-development.py:23,74–100,411–423` | 56 CI-Selbsttests; 37 M0 einschließlich SDK-Opt-in; 11 Manifesttests; exakte Fallzahlen, keine Skips | Vollständig erhalten; normale unittest-Exitcodes genügen nicht. |
| `ci-development.py:103–324` | Gepflegte Modul-/Kotlin-Inventur; Development-Filter plus zwei Originalklassen; frische, vollständige und widerspruchsfreie JUnit-Fälle | Keine pauschale Volltest-/Screenshot-Erweiterung; keine Umdeutung statischer Methodenanzahlen zu Laufzahlen. |
| `ci-development.py:362–410` | Neue Reports, Run-ID/-Attempt, SHA-Kontrolle, RELEASE_-Variablen entfernt, SDK-/Java-Prüfung | Kontrollkontext um Referenz-/Toolchain-/Dependencybelege erweitern; keine alten Belege übernehmen. |
| `ci-development.py:424–445` | Temporärer RSA-2048-Debugschlüssel, 30 Tage, fester CI-DN; unabhängiger Zertifikat-Pin vor Build; rerun-tasks, kein Konfigurationscache | Pinbindung pro Lauf erhalten; wechselnde Pins nur nach zusätzlicher separater Vergleichsregel zulassen. |
| `ci-development.py:437–451` | AssembleDevelopmentDebug, Lint, gefilterte Development-/Original-JVM-Tests und JUnit-/Lintauswertung | Für Referenz und Kandidat gleich ausführen; keine Geräteaufgaben. |
| `ci-development.py:55–71,452–471` | Genau eine frische APK, Output-Metadaten, Name, M0-Development, erwartete Production-Ablehnung, finales Manifest, Größe | Keine APK-Suche mit Fallback; dieselben APK-Bytes in allen Schritten. |
| `ci-development.py:326–350,470–488` | Größencheck heute auch auf Debug; approved erst nach exakt allen Quittungen; erneute Hashprüfung; Bereitschaft erst nach Schlüsselbereinigung | Nur die Development-Größenpflicht darf nach Genehmigung ausdrücklich ersetzt werden; kein Überspringen bei fehlender Referenz. |
| `scripts/compare-apk-size-profile.py:262–359,362–389` | Strenge Datenprüfung, unbestätigte Claims; abweichende Pins nicht vergleichbar; gültige Analyse Exit 3 | Kein Gate. Unverändert als Diagnose verwenden; neuer enger Freigabeprüfer braucht eigene Belege und Bedingungen. |
| `scripts/apk-size-report.py:81–148` | Beobachtete Konfigurationen und externe Claims getrennt; kein authentifizierter Buildnachweis | Profil allein genügt nie als Buildattestation. |
| `app/build.gradle.kts:27–85,171–187` | Isolierte Development-ID, Debugsignierung; Release geschrumpft/ARM; DEX komprimiert; Minuten-VersionCode; Development-Release aus | App-ID, Buildtyp, Packaging und Produktionssperren unverändert binden. |
| `gradle/wrapper/gradle-wrapper.properties`, Katalog | Gradle 9.8.0, AGP 9.4.1, Projekt-Kotlin 2.4.20; keine distributionSha256Sum | Wrapper-/Distributionsprüfung ergänzen. Gradles eingebettetes Kotlin 2.4.10 ist eine andere Angabe. |
| `settings.gradle.kts:1–16` | google/mavenCentral/pluginPortal, FAIL_ON_PROJECT_REPOS | Repositories beschränkt, aber keine Prüfung tatsächlicher Artefaktbytes. Keine vorhandene verification-metadata.xml oder Dependencylocks gefunden. |
| `perf-budget.json:14–15`, `scripts/apk-size-check:2–10` | Release-Ratchet 3.563.000 Bytes | Nicht auf 20 MB anheben und nicht als passenden Development-Maßstab ausgeben. |

Dies sind statisch bestätigte Regeln, keine heute bestandenen CI-Läufe.

## 4. Vertrauensmodell: Inhaber, geschützter Master, separate Referenz

### 4.1 Autorität und Voraussetzungen

Nur der Projektinhaber `ordondawid2004-del` darf eine Referenz aktivieren oder
ersetzen. Die Implementierung darf weder aus PR-Inhalten noch aus einem
`approved:true` in JSON eine solche Autorität ableiten. Ein etwaiges späteres
Delegieren braucht eine explizite Änderung dieses Betreibervertrags.

Vor Aktivierung sind im **eigenen Fork** folgende Betreiberkonfigurationen nötig:

1. Geschützter master: kein Force-Push/Löschen; Änderungen an Workflow, Prüfcode,
   Gradle-/Verifikationsdaten und Vertragsdaten nur nach Inhaberreview.
   `.github/CODEOWNERS` benennt den Inhaber; Required-Codeowner-Review und eine
   eng auf den Inhaber begrenzte, protokollierte administrative Ausnahme für
   dessen eigene Änderungen werden eingerichtet. Ungeprüfte Mitarbeiter dürfen
   keinen autoritativen Masterstand herstellen.
2. Geschützte Referenzbranch `size-references`: kein Force-Push/Löschen; nur der
   Inhaber darf geprüfte neue Referenzcommits hinzufügen. Die konkreten
   Commitobjekte und ein separat archiviertes Bundle werden aufbewahrt.
3. Repositoryvariable **TVHOME_SIZE_TRUST_ROOT** wird nur durch den Inhaber in
   Settings gesetzt. Sie ist kein Secret. Kein Workflow erhält Rechte, sie zu
   schreiben. Ihre Herkunft ist die Betreiberkonfiguration, niemals eine PR-Datei,
   ein Artefakt, ein CLI-Wert des Kandidaten oder ein automatisch berechneter Hash.

Diese Einstellungen wurden nicht remote untersucht und werden hier nicht geändert.
Ihre Erfüllung muss vor Aktivierung überprüfbar protokolliert werden. Die
Referenzaufnahme verlangt zusätzlich workflow_dispatch auf master und den
plattformauthentifizierten Inhaber als Auslöser; im Implementierungscode ist
dessen echte numerische Benutzer-ID zu binden. Keine zusätzliche Environment
ist für die Autorisierung nötig: Erst das manuelle Setzen des Root-Pointers
durch den Inhaber aktiviert eine konkrete Referenz.

### 4.2 Ereignisregel und Grenzen untrusted PRs

Autoritative Development-Freigaben sind zunächst ausschließlich möglich für
`repository == ordondawid2004-del/glass-tv-custom`, Event `push`, Ref
`refs/heads/master`, überprüften Commit und vollständig aktiven Vertrag.
PRs und Featurebranch-Pushes führen weiterhin Geräte-freie Prüfungen und
Diagnostik aus, erzeugen aber **keinen anerkannten approved-APK-Upload**.
Tags bleiben Fehler. Referenzaufnahme ist ein eigener manueller Workflow, kein
normaler Kandidatenlauf und keine Ausnahme, die eine APK freigeben könnte.

Der PR-Checkout kann Prüfskripte/YAML manipulieren und einen grünen Namen oder
ein beliebig benanntes Artefakt vortäuschen. Read-only contents-Berechtigung
verhindert nicht jedes Actions-Artefakt. Deshalb wird eine PR-eigene Aussage
niemals zur Freigabeautorität: Referenz-/Kandidatenherkunft verlangt das erlaubte
Event, den tatsächlichen Repository-/Run-/Attempt-/Commitkontext und den
Inhaber-geprüften Master-/Enginezustand. Eine nachgelagerte privilegierte
`pull_request_target`- oder `workflow_run`-Pipeline, die PR-Code ausführt oder
PR-Berichte als Erfolg übernimmt, ist ausdrücklich nicht Teil dieser Architektur.

Die Vertrauenswurzel ist der Inhaber-geprüfte Master samt festgelegter Engine,
der geschützte Rootwert und die vertrauenswürdige Actions-Ausführung. Ein
kompromittierter Inhaberaccount, Runner oder absichtlich bösartiger bereits
freigegebener Buildcode ist damit nicht kryptografisch ausgeschlossen. Kein
JSON-Hash und kein Dateimodus wird als Sandbox gegen Gradlecode desselben Users
verkauft. Ungeprüfter PR-Code bekommt gerade deshalb keine Freigabeautorität,
Produktionssecrets oder privilegierte Fortsetzung.

### 4.3 Atomarer Root-Pointer

TVHOME_SIZE_TRUST_ROOT ist ein striktes, höchstens 8-KiB-JSON ohne doppelte Felder.
Es enthält genau die folgenden versionierten Angaben:

| Feld | Bindung |
| --- | --- |
| schema | `tvhome.size-trust-root/1` |
| enabled | Bool; nur ausdrücklich true erlaubt den Ersatzpfad |
| repository, repository_id | Exakter Forkname und von GitHub verifizierte numerische Repository-ID |
| engine_commit, engine_manifest_sha256 | Vollständiger geprüfter Gitcommit und SHA-256 der Engine-Dateiinventur |
| reference_commit, reference_manifest_path, reference_manifest_sha256 | Vollständiger Commit auf size-references, fester sicherer relativer Dateipfad, exakte Manifestbytes |
| policy_id | `development-no-positive-delta-v1`, erst nach Inhaberentscheidung zulässig |

Keine Werte werden in diesem Dokument erfunden. Für diesen lokalen Stand liegt kein
verifizierter aktiver Rootwert und keine hier freigegebene Referenz vor; die
Remote-Konfiguration wurde nicht untersucht. Fehlender, falscher,
unbekannter oder teilweiser Root führt im Ersatzpfad zu Ablehnung; er aktiviert
keinen „Lernen“- oder „Neue-Baseline“-Modus. Ohne aktivierten Ersatzpfad bleibt der
bisherige Development-Größencheck maßgeblich und blockiert weiterhin große APKs.

Vor dem ersten Engine-Aufruf prüft der bereits reviewte Master-Workflow den
Rootwert mit fester Parser-/Hashlogik, lädt die Engine per vollständigem Commit
und berechnet ihre vollständige Commit-Dateiinventur (Pfade, Modi, Größe, SHA-256)
gegen engine_manifest_sha256. Der zu prüfende Enginecode darf diese
Bootstrapprüfung nicht selbst ersetzen. Keine Ausführung vor dieser Bindung.

Die Engine ist ebenfalls ein expliziter, abrufbarer Commit im eigenen Fork,
nicht der aktuelle PR. Der Workflow lädt ihn getrennt vom Quellcheckout und
prüft seine Inventur gegen den Root. Ausgeführt werden nur diese Prüfskripte.
Für den App-Build wird der separat gebundene Kandidatencheckout verwendet;
`ci-development.py` benötigt dafür die unten spezifizierte Projektroot-Trennung.
Enginewechsel können separat ohne automatische Größenreferenz-Erhöhung geprüft
werden; sie benötigen einen neuen manuell gesetzten Root. Alte Rootwerte werden
als Auditnachweis aufbewahrt, nicht per Kandidat rückwärts ausgewählt.

### 4.4 Unveränderliches Referenzbundle

Gitcommit-SHA plus Manifest-SHA identifizieren den unveränderlichen Inhalt.
Branchname oder „latest“ allein sind kein Anker. Ein gelöschtes/nicht abrufbares
Commitobjekt führt zum Fehler. Der Hash ist Integrität, keine Verfügbarkeitsgarantie.
Das archivierte Bundle dient nur zur Wiederherstellung derselben Bytes; ein
anderer Inhalt braucht eine neue Freigabe.

Unter `references/<reference-id>/` enthält ein Bundle genau:

1. `reference-manifest.json` – Schema `tvhome.development-size-reference/1`.
2. `profile.json` – unveränderter Profileroutput des gewählten CI-Builds.
3. `source-manifest.json` – Gitcommit/-tree und vollständige effektive Inputhashes.
4. `dependency-artifacts.json` – tatsächliche externe Laufzeit-/Build-/Testartefakte.
5. `verification-metadata.xml` – separat geprüfte SHA-256-Vertrauensliste.
6. `toolchain-manifest.json` – tatsächliche Toolchain-/Runnerdateien und Versionen.
7. `recipe.json` – Taskrezept, Flags, Konfiguration, Packaging-/ABI-/Signierungsregel.
8. `build-evidence.json` – Ablauf, Prüfquittungen, APK-/Profilhash und Herkunft.
9. `measurement-1.json`, `measurement-2.json` – beide kontrollierten CI-Aufnahmen.
10. `approval.json` – konkrete Entscheidung, Inhaber, Zeitpunkt, Plattformbeleg.

Das Referenzmanifest bindet jedes dieser Dokumente (außer sich selbst) durch
relativen Pfad, Größe und SHA-256; zusätzlich schema, reference_id, policy_id,
Repository-ID, Sourcecommit, Enginecommit, Referenz-Run-ID/-Attempt/-Job,
Variant/ID, APK-/Profilhash, Rohgröße, recipe-/toolchain-/dependency-/source-Digests
und Referenz-Auswahlregel. Die externe Rootvariable bindet das Manifest selbst.
Es gibt weder freie URL-Felder noch einen aus dem Bundle übernommenen Rootwert.
Keine APK und kein privater Schlüssel muss in diesem Bundle stehen.

`approval.json` allein ist kein Vertrauensanker; sein Name, Datum und Actor sind
zunächst Dokumentation. Entscheidend ist der durch den Inhaber extern gesetzte
Manifest-Pin unter den geprüften Settings-/Branchrechten. Die Plattformherkunft
der referenzierten Workflowläufe wird separat über GitHub-Metadaten geprüft.
Es wird keine nicht verfügbare persönliche GitHub-Auditschnittstelle erfunden.
Referenz-Run/-Attempt müssen abgeschlossen sein und **vor** dem Kandidatenlauf
liegen. Kandidat und Referenz dürfen nicht dieselbe Run-ID/-Attempt sein. Eine
Referenz aus einem ungeprüften heutigen Build bleibt selbst bei gleichem Hash
unzulässig. Gleiches Sourcecommit in verschiedenen kontrollierten Läufen ist für
Reproduzierbarkeitsaufnahmen zulässig; das ist kein Baseline-Selbsterhöhungsrecht.

## 5. Identifikation von Quellen, Dependencies und Werkzeugen

### 5.1 Vollständige effektive Inputs

Für Referenz und Kandidat: Repository-ID/URL, tatsächlicher Commit, Git-tree,
Branch/Event und über GitHub verifizierte Run-ID/-Attempt/-Job/Workflowidentität;
kein vom Artefakt selbst behaupteter Event. Gitcheckout muss dem angekündigten
Commit entsprechen. Submodules/LFS/zusätzliche Quellinputs werden entweder mit
unveränderlichen Hashes materialisiert und inventarisiert oder explizit abgelehnt;
sie werden nicht still als vorhanden angenommen.

Vor/nach Build und Gate müssen sämtliche Eingabebytes identisch sein. Manifest:
sortierte relative Pfade, SHA-256, Dateigröße, relevanter Modus; keine ungebundenen
Symlinks. Das Gitcommit muss sämtliche App-/Test-/Ressourcen-/Buildquellen enthalten.
Kein unsicherer M0-HEAD-Verweis für einen schmutzigen M1-Baum.

Einziger zusätzlicher Sourceinput für Dependencyverification ist die aus dem
freigegebenen Bundle injizierte `gradle/verification-metadata.xml`, falls sie
nicht bereits identisch im Commit vorhanden ist. Sie wird in einer separaten
Buildkopie materialisiert, als externer kontrollierter Input mit Hash ausgewiesen
und nicht als heimliche Sourceänderung behandelt. Gradle-Initskript, Engine und
SDK/JDK sind ebenfalls explizite gebundene Inputs. Keine Änderung des originalen
Checkouts, keine sonstigen akzeptierten Dirty-Dateien.

### 5.2 Tatsächliche Artefaktbytes statt Textgraph

Die erste Version verlangt eine feste, positiv freigegebene Dependencyklasse:
Referenz und Kandidat müssen dieselben aufgelösten externen Artefaktidentitäten
und Bytes nutzen. Neue/geänderte Artefakte sind kein still erlaubtes Wachstum,
sondern verlangen einen bewusst geprüften neuen Vertrauens-/Referenzstand.

Vorgesehene Umsetzung mit vorhandenen Gradle-Fähigkeiten:

- Native Gradle Dependency Verification im Modus **strict**, Metadata-Prüfung
  aktiv, SHA-256-Werte; keine trust-all-/ignore-/lenient-Ausnahmen. Die XML-Datei
  kommt aus dem gehashten Bundle, nicht aus einem PR-eigenen Vertrauensangebot.
- Jeder tatsächlich verwendete externe JAR/AAR/POM/module-/Plugin-/Marker-/
  Testartefakt-Input muss vom Verifikationsmechanismus abgedeckt sein. Plugins,
  Buildscript- und transitive Dependencies werden nicht auf Runtime-Koordinaten
  reduziert. Dynamische Versionen, SNAPSHOTs und changing Modules sind unzulässig.
- `gradle/size-gate.init.gradle.kts` registriert die spätere Aufgabe
  `:app:writeDevelopmentSizeInputs` und protokolliert tatsächlich aufgelöste
  Projekt-/Buildscript-Konfigurationen. Sie hasht reale Artefaktdateien und
  bindet Komponenten-ID, Variante/Classifier/Typ, Rolle, Dateigröße, SHA-256 und
  Ursprungskonfigurationen in kanonischem UTF-8-JSON. Plugin-/Settings-Auflösung
  muss zusätzlich durch strikte native Verifikation und feste Pluginversionen
  nachgewiesen werden; der Collector darf seine Abdeckung nicht nur behaupten.
- Das Gate kontrolliert konkrete verifizierte Dateien erneut gegen die
  freigegebenen Bytes, den vollständigen Artefaktmanifest-Digest und den
  Verifikationsmetadaten-Pin. Ein erfolgreicher `dependencies`-Textbericht reicht
  nicht. Lokale Projektmodule werden über vollständige Quellhashes gebunden;
  ihre neu kompilierten Outputs sind keine vorab freigegebene Maven-Dependency.
- Leeres GRADLE_USER_HOME ohne Nutzer-Initskripte und frische Projektoutputs;
  kein geteilter Taskoutputcache. `--no-build-cache --rerun-tasks
  --no-configuration-cache --dependency-verification=strict` sind Bestandteil
  beider Rezepte. Ein Downloadcache wäre nur als erneut gehashte Quelle nutzbar;
  die erste Version verzichtet auf dessen Übernahme. Keine Cache-Schlüssel als
  Integritätsbeweis, kein Export lokaler Buildcaches.

Der komplette Plugin-/Artefakt-Erfassungsweg muss mit realen Gradle-Inputs in der
unten beschriebenen kontrollierten CI-Aufnahme bewiesen werden. Kann der Collector
unter Gradle 9.8.0 eine benötigte Artefaktklasse nicht nachweisen, bleibt der Vertrag
inaktiv; es gibt keinen Fallback auf Koordinaten. Das ist ein konkretes
Abnahmekriterium der Implementierung, kein freier zukünftiger Konzeptauftrag.

### 5.3 Toolchain und Packaging

Zielrunner `ubuntu-24.04` statt ubuntu-latest. Der Tag allein ist weiterhin nicht
unveränderlich: ImageOS, ImageVersion und Architektur müssen erfasst und gegen
die Referenz gebunden werden. Ein Imagewechsel blockiert zunächst und braucht
einen explizit geprüften Referenz-/Toolchainwechsel. Keine Behauptung, alte
GitHub-Runnerimages ließen sich beliebig erneut anfordern. Wenn die erforderliche
Klasse nicht mehr angeboten wird, gibt es keine Freigabe mit ungeprüftem Ersatz.

Alle Actions werden auf echte vollständige Commit-SHAs gepinnt; diese sind vor
Implementierungsabschluss aus der tatsächlich gewählten Upstreamversion zu
prüfen, nicht in diesem Dokument zu erfinden. JDK-Patch/-Build, Python, Gradle,
Projekt-Kotlin, AGP, D8/R8, SDK Commandline-Tools-Revision, Build-Tools, Plattform
und deren tatsächlich verwendete Archiv-/Dateihashes werden gebunden. Für die
erste Klasse wird der bekannte JDK-Stand 21.0.13+9 mit unabhängig geprüftem
Vendorarchivhash vorgesehen; die tatsächliche Actions-Verfügbarkeit ist noch
nicht bewiesen. Gradle 9.8.0 bekommt die echte offizielle distributionSha256Sum;
Wrapper-JAR und Launcherskripte werden ebenfalls geprüft. Keine bewegliche
latest-Quelle darf ohne den genehmigten konkreten Archiv-/Dateihash durchkommen.

`toolchain-manifest.json` enthält versionsgetreue Ausgaben und eine kanonische
Hashinventur der verwendeten JDK-/Gradle-/SDK-Werkzeuge, android.jar und relevanten
Buildwerkzeugbibliotheken; nicht nur einen apksigner-Shelllauncher. SDK-Pfade sind
nur Beobachtungen, nicht die Toolchainidentität. Fehlende Bytes/Versionen sind Fehler.

`recipe.json` bindet die normierte Task-/Argumentliste, Engine-/Initskripthashes,
Konfigurationshashes (einschließlich aller Modul-Builddateien, nicht nur der sechs
Profilerfelder), Repositories, Pluginversionen, compile/min/target SDK,
Minify-/Shrink-Werte, DEX-/Native-Kompression, Ausschlüsse, ABI-Regeln und
Debug-Abhängigkeiten. ABI-Menge der tatsächlich erzeugten APK muss zusätzlich
passen; gleiche App-Gradle-Datei ist kein alleiniger Packagingbeweis.

## 6. Zeitabhängige Version und ephemere CI-Signierung

VersionName zunächst exakt `0.3.0-m1-dev`, ID
`io.github.ordondawid2004del.tvhome.dev`, Variante developmentDebug,
`debuggable=true`, `testOnly=false`. Namespace ist kein App-ID-Ersatz.

VersionCode darf variieren, muss aber die bestehende BuildMinute-Regel erfüllen:
positive Ganzzahl, innerhalb der Minuten des protokollierten Konfigurations-/
Buildfensters seit 01.01.2026 UTC, konsistent in Outputmetadata, Manifest und
BuildConfig. Uhr und Buildregel werden nicht fixiert; kein Umpacken oder
Umsignieren, um Größen/Hashes gleich zu machen. Eine inkonsistente oder außerhalb
des echten Laufzeitfensters liegende Version wird abgelehnt. Die zulässige
Metadatenvariation gewährt **keine** automatische Größenüberschreitung.

Pro CI-Lauf bleibt der vorhandene temporäre RSA-2048-Debugschlüssel im privaten
Tempverzeichnis. Pin vor dem Build aus separat erzeugtem öffentlichem Zertifikat
bestimmen, **nicht** aus der zu akzeptierenden APK. APK gegen exakt diesen Pin
mit M0 prüfen; Produktion muss wie heute abgelehnt werden. Dazu tatsächlichen
Zertifikattyp, Keylänge, DN `CN=Android Debug,O=TV Home CI Development,C=DE`,
Laufzeitprofil 30 Tage, Signaturverfahren und verwendete Signaturschemata erfassen.
Welche Schemakombination die konkrete AGP-/SDK-Klasse erzeugt, wird in der
kontrollierten Referenzaufnahme beobachtet und anschließend im Bundle fixiert;
kein erfundener Schemakatalog wird als bereits gemessen ausgegeben.

Referenz und Kandidat dürfen unterschiedliche Pins haben, **nur** wenn jeder
separat an seinen unabhängigen Run-Pin gebunden ist und das genehmigte
CI-Ephemeral-Zertifikat-/Signaturprofil erfüllt. Unterschiedliche Pins bleiben
im Bericht unterschiedlich. Gleichheit von DN/Keylänge allein beweist keinen
vertrauenswürdigen Signierer. Keine Freigabe für beliebige fremde Debugschlüssel,
den lokalen stabilen Entwicklungsschlüssel oder einen Produktionsschlüssel.

Privatschlüssel nie in Quellkopien, Logs, Caches oder Artefakten; Debugpasswort ist
kein Produktionssecret. Kein Shelltracing mit sensitiven Argumenten. Vor
Bereitschaftssignal muss die Bereinigung tatsächlich beendet sein. Nur
öffentliche Zertifikate/Pins dürfen als Nachweise erhalten bleiben. Diese
Signierung ist kein Updatepfad einer dauerhaft signierten Produktversion.

## 7. Minimaler fehlender CI-Nachweis und Bootstrap

Keine weitere lokale Warm-Cache-Runde. Die lokale APK mit stabilem Schlüssel ist
kein Kandidat für die neue CI-Referenzklasse.

Der einmalige Bootstrap ist ein vorab festgelegter Bedienablauf, keine neue
Konzeptphase und kein heutiger Auftrag zur Actions-Auslösung:

1. Vollständigen Entwicklungs- und Engine-Stand nach gesonderter Genehmigung als
   reviewed Commit im eigenen Fork verfügbar machen. Operatorbedingungen aus
   Abschnitt 4 prüfen. Exakte Toolchain-/Actionarchive verifizieren und festhalten.
2. Falls freigegebene Verifikationsmetadaten fehlen: eine **Resolution-only**-
   Aufnahme aus diesem Source-/Rezeptstand darf mittels
   `:app:writeDevelopmentSizeInputs --write-verification-metadata sha256` eine
   ausdrücklich **ungeprüfte** Metadatenvorlage samt Artefaktinventur erzeugen.
   Dies ist kein APK-Build, kein strikter Verifikationsnachweis und keine
   Freigabe. Hashes über vertrauenswürdige Hersteller-/Repositoryquellen prüfen;
   Inhaber billigt die konkrete Liste. Ein blindes „write → trust“ ist verboten.
   Die geprüften Metadaten werden als separat gehashter Bootstrapinput fixiert. Die Genesis-Aufnahme benötigt noch keinen aktiven Größenroot: Der
   Inhaber übergibt nach Prüfung über workflow_dispatch vollständigen Source-/
   Enginecommit sowie Commit/Pfad/SHA der Bootstrap-Verifikations- und
   Toolchainmanifeste auf size-references. Alle Eingaben sind an den
   authentifizierten Owner-Dispatch gebunden und werden streng validiert. Sie
   erlauben nur Evidence-Aufnahme, keinen Kandidaten- oder Uploadmodus.
3. **Genau zwei neue kontrollierte CI-APK-Builds**, zwei getrennte Jobs derselben
   manuell gestarteten Referenzaufnahme mit demselben vollständigen Sourcecommit,
   Engine, Rezept, genehmigter Metadatenliste und Toolchainklasse. Beide erzeugen
   unabhängig einen temporären Schlüssel. Keine Wiederverwendung fertiger APKs
   oder Taskoutputs; Hashinventur und strikte Verification in beiden Jobs.
4. Beide müssen Build, Lint, Python-/JVM-/Inventur-, M0-/M1- und Signaturgates
   erfüllen. Aufnahme bestätigt die vollständige Artefakt-/Plugin-Abdeckung,
   tatsächliche Zertifikats-/Signaturprofile, ImageVersion und Profilkonsistenz.
   Unterschiedliche Runnerimages sind keine zwei vergleichbaren Proben. Bei
   Fehlschlag/Abweichung stoppt Aufnahme; keine automatische Wiederholungsserie.
5. Größenergebnisse und Unterschiede offen ausweisen. Größere der beiden validen
   APK-Größen liefert den **Vorschlag** für die konkrete Referenz; bei Gleichstand
   deterministisch Messung 1. Niemals die Summe/Prozentschwelle selbst erhöhen.
   Diese Auswahl behauptet weder eine statistische Obergrenze der Variation noch
   eine garantierte technische Toleranz für alle zukünftigen Builds.
6. Beide können als Referenzaufnahme die alte Release-Ratchet überschreiten.
   Das wird als fehlgeschlagene Release-Budget-Beobachtung dokumentiert. Der
   getrennte Befehl `reference-capture` ist ausdrücklich keine vollständig
   bestandene Kandidatenpipeline und erzeugt weder approved/ noch apk_ready.
   Er darf nur Profile und bereinigte Nachweise ausgeben; keine APK hochladen.
7. Inhaber sieht das vollständige Bundle und billigt **exakte Manifest-/Profil-
   hashes und Größe**; schreibt es nach size-references und setzt den atomaren
   Rootpointer. Nur dieser explizite Schritt aktiviert eine konkrete Referenz.
   Das geschieht nicht durch die beiden Buildjobs. Bis dahin keine Uploadfreigabe.

Zwei unterschiedliche ephemere Schlüssel sind der minimale gezielte Nachweis
gegenüber der lokalen stabilen Signierung. Sie beweisen keine maximale
Zufallsvariation. Die empfohlene Fail-closed-Regel akzeptiert spätere technische
Fehlalarme; genau deshalb wird kein dritter Messblock oder willkürlicher Puffer
verlangt. Aufnahme, Speicherung und Betreiberkonfiguration sind spätere explizite
Aktivierungsschritte; sie werden in M1.4.4 nicht ausgeführt.

## 8. Konkrete Integrationsbefehle und Datenfluss

**Alle neuen Subcommands/Flags in diesem Abschnitt sind zu implementierende
Schnittstellen, heute keine vorhandenen ausführbaren Funktionen.** Variablen wie
ENGINE/SOURCE/RUN werden von vertrauenswürdigem Workflowcode gesetzt, niemals
per eval aus JSON oder PR-Titeln. Aufrufe erfolgen als Argumentlisten. Der Run-
Ordner wird per mktemp neu reserviert. Alle JSONs sind strikt und exklusiv.
CHECKOUT bezeichnet den unveränderten Gitcheckout; prepare erzeugt die getrennte
Buildkopie `$RUN/source` und das private Tempverzeichnis mit Modus 0700. Erst dann
bezeichnet SOURCE genau diese Buildkopie; keine App-Datei des Originalcheckouts
wird geschrieben. Variable Pfadargumente (Run-/Keystorepfad) werden nur nach einer
expliziten Rezeptregel typisiert normalisiert; die tatsächlichen Argumente und
zugehörigen Inputs bleiben separat gebunden. Keine beliebigen Flags entfernen.

### 8.1 Festgelegte Reihenfolge

| Schritt / späterer Befehl | Eingaben und Vertrauensanker | Ausgaben und Erfolgskriterium | Fehler / Freigabewirkung |
| --- | --- | --- | --- |
| `python3 -B "$ENGINE/scripts/ci-size-gate.py" preflight --source "$CHECKOUT" --output "$RUN/context.json"` | Root aus TVHOME_SIZE_TRUST_ROOT; tatsächlicher Actionskontext; getrennt geladenes gehashtes Enginecommit; feste Fork-ID | Kontext enthält verifizierten Rootdigest, Referenzcommit/-manifest, Engine, Event, Sourcecommit, Run/-Attempt/-Job, erlaubte Eingaben | Kein Root-/Event-/Hashnachweis: Ersatzpfad stoppt; niemals approved. Ein PR kann Diagnostik durchführen, aber keine Freigabe erhalten. |
| Im preflight: feste Commitobjekte laden, konkrete Blobs lesen | HTTPS nur zum eigenen Fork, explizite volle SHAs; keine untrusted URL/Branch-Suche | Referenzdateien exakt gegen Rootmanifest geprüft; ausschließlich erlaubte relative Pfade/Größen, keine Symlinks, Zip-Slip oder freie Archivextraktion | Fehlende/mehrdeutige/manipulierte Referenz, gleiche Runherkunft oder untrusted Approval: Fehler. |
| `python3 -B "$ENGINE/scripts/ci-size-gate.py" prepare --context "$RUN/context.json" --source "$CHECKOUT" --output "$RUN/input.json"` | Gebundene Toolchain und Verifikationsliste; unveränderte Inputs; frische Buildkopie | Materialisierte erlaubte Zusatzinputs, effektive Sourceinventur, Task-/Umgebungskontext; Toolchainhashes passend | Toolchain-/Packaging-/Repositoryabweichung: nicht vergleichbar, keine Freigabe. Keine Installation ungeprüfter Tools aus PR-Daten. |
| `python3 -B "$ENGINE/scripts/ci-development.py" run --project-root "$SOURCE" --output "$RUN/output" --temp-dir "$RUN/private" --size-contract-context "$RUN/context.json"` | Vertrauenswürdiger Enginecode, gebundene Sourcekopie; unveränderte bisherige Pflichtprüfungen | Frische Test-/Lint-/APK-/Signaturquittungen; noch kein Staging vor finaler Größe | Jeder Fehler/Skip/fehlende Fall/alte APK oder XML verhindert Freigabe. |
| Darin Gradle: bestehende Tasks/Filter, zusätzlich `:app:writeDevelopmentSizeInputs`, `--init-script "$ENGINE/gradle/size-gate.init.gradle.kts" --dependency-verification=strict --no-build-cache --rerun-tasks --no-configuration-cache --console=plain` | Eigenes Keystore-Argument wie bisher; pinned Verifikations-/Rezeptinput | Neue geprüfte APK und tatsächlich gehashte externe Artefaktinputs; vollständige originale JVM-Gruppen | Verbotene Taskgraphen, fehlende/veränderte Artefakte, anderer effektiver Taskplan oder unterdrückte Verification: Fehler. |
| Bestehende M0-/M1-Aufrufe aus der Engine | Exakt selektierte APK, separat vorher bekannter Run-Pin, frisches finales Manifest | Developmentprüfung erfolgreich, Produktionsmodus genau erwartete Ablehnung, finales Manifest akzeptiert | Kein Skip aufgrund erwartbar unterschiedlicher Referenzsignatur. |
| `python3 -B "$ENGINE/scripts/apk-size-report.py" "$APK" --project-root "$SOURCE" --provenance "$RUN/claims.json" --output "$RUN/output/reports/candidate-profile.json"` | Frische endgültige APK; Claims aus Liveablauf, Dependencyreferenz = Manifestdigest; SDK gebunden | Unverändertes Schema /1, Exit 0, SHA entspricht allen bisherigen APK-Quittungen | Profilerfehler/Veränderung: keine Freigabe. Claims bleiben im Profil ausdrücklich unbestätigt. |
| `python3 -B "$ENGINE/scripts/compare-apk-size-profile.py" --reference "$REF/profile.json" --candidate "$RUN/output/reports/candidate-profile.json" --reference-sha256 "$TRUSTED_PROFILE_SHA" --output "$RUN/output/reports/size-comparison.json"` | Referenzprofil-Pin ausschließlich aus gehashtem freigegebenem Manifest; Candidateprofilhash frisch | Exakt Exit 3 plus valider Report mit richtigen Inputhashes und nachgerechneten Summen | Exit 0/2/anderer Wert, fehlender Report oder falsche Inputbindung: Fehler. Kein `|| true`. |
| `python3 -B "$ENGINE/scripts/ci-size-gate.py" evaluate --context "$RUN/context.json" --apk "$APK" --evidence "$RUN/output/reports/build-evidence.json" --comparison "$RUN/output/reports/size-comparison.json" --output "$RUN/output/reports/size-verdict.json"` | Aktiver Root, verifizierte Referenz, Livebelege, Roh-APK; kein beliebiger PR-Bericht | Nur nach Abschnitt 8.2: Exit 0 und exakt gebundene positive Development-Größenquittung | Fehlende/negative Prüfung: Exit 1; ungültige Eingabe: Exit 2; keine positive Quittung. |
| Bestehende finale approve-Grenze im Helper | Alle bisherigen Checks plus zusätzliche Root-/Quell-/Artefakt-/Profil-/Vergleich-/Größenquittungen | Exakter abschließender APK-Hash, nur diese APK und passende Berichte in neuem approved/; Schlüssel bereinigt | Jede Mutation/Staging-/Cleanupabweichung entfernt eigenen neuen approved-Ordner und lässt Bereitschaft false. |
| Upload nur erlaubter Masterpush, success UND frisches apk_ready | Gebundene Run-/Attempt-/Commitmetadaten, neu erzeugtes approved/ | Ein unverwechselbar benanntes Developmentartefakt; niemals Release | PR/Feature/Tag, alte Ausgaben oder fehlende Quittung: kein anerkannter APK-Upload. Diagnosepfad bleibt APK-/Key-frei. |

Das Sourcecheckout ist für Build-/JVM-Quellen zuständig; Engine liefert unveränderliche
Prüfer, Tests ihrer Sicherheitslogik und Initskript. Der Helper MUSS diese Pfade
trennen. Ein aus dem Engineverzeichnis aufgerufener Helper darf nicht versehentlich
die Engine-App statt des Kandidaten bauen. JVM-Inventur und erwartete Testmethoden
müssen an den ausdrücklich reviewed Inventurstand gebunden sein; kein Kandidat
kann seine Sollmenge ungeprüft verkleinern. Normale lokale Aufrufe ohne aktivierten
Kontext behalten die bisherigen Sicherheits-/Release-Größenregeln.

Für reference-capture ist der Befehlsweg getrennt und vollständig festgelegt:

```sh
python3 -B "$ENGINE/scripts/ci-size-gate.py" preflight --reference-capture \
  --bootstrap "$RUN/bootstrap.json" --source "$CHECKOUT" \
  --output "$RUN/reference-context.json"
python3 -B "$ENGINE/scripts/ci-size-gate.py" prepare \
  --context "$RUN/reference-context.json" --source "$CHECKOUT" \
  --output "$RUN/input.json"
python3 -B "$ENGINE/scripts/ci-development.py" reference-capture \
  --project-root "$SOURCE" --output "$RUN/capture" --temp-dir "$RUN/private" \
  --size-contract-context "$RUN/reference-context.json"
```

bootstrap.json bindet die vom Inhaber übergebenen konkreten Bootstrapinputs
und darf nur beim authentifizierten Owner-Dispatch geladen werden. Ohne
--reference-capture gibt es keinen impliziten Bootstrapfallback. Referenzmodus
führt dieselben nicht-größenbezogenen Pflichtprüfungen durch, erzeugt nach
vollständigem Abschluss einen als measurement_only gekennzeichneten Beleg und
endet mit Exit 3; Fehler liefern 1/2. Die Workflowsteuerung kontrolliert diesen
exakten Exit und die vollständigen gebundenen Aufnahmebelege, bevor sie nur
Profile/Diagnostik hochlädt. Ein erfolgreicher Aufnahmejob heißt ausschließlich
„Messaufnahme abgeschlossen“, niemals „Kandidatenpipeline vollständig bestanden“.
Keine APK wird hochgeladen. evaluate lehnt jeden reference-context ab.

Noch vor finaler candidate-evaluate- oder reference-capture-Quittung muss das
private Keyverzeichnis tatsächlich bereinigt sein. Alle kryptografischen
APK-Prüfungen brauchen danach nur den bereits bekannten öffentlichen Pin.
Der spätere Helper verschiebt deshalb seine finale Größenentscheidung und
approve-Grenze hinter das Ende des Key-Temp-Kontexts. Er schreibt
build-evidence.json erst danach exklusiv und ändert diesen gehashten Beleg
nicht mehr. Das spätere summary.json darf zusätzlich die Größenquittung nennen;
es ersetzt keinen gehashten Vorbeleg. So gibt es weder einen Cleanup-Erfolg
vor der tatsächlichen Löschung noch einen zyklischen Hash auf das eigene Urteil.

### 8.2 Eng begrenzter neuer Freigabeprüfer

`ci-size-gate.py` ist kein weiterer Messprofiler, sondern der notwendige
Vertragsprüfer. Er erteilt nur eine interne **Development-Größenquittung**, niemals
allein apk_ready. evaluate darf exakt dann Exit 0 liefern, wenn gleichzeitig gilt:

1. Aktiver Betreiberroot und Inhaberfreigabe des konkreten Referenzmanifests;
   Engine-/Fork-/Event-/Run-/Attempt-/Sourcebindung gültig und unverändert.
2. Sourceinputinventur vollständig; Quellenänderung gegenüber Referenz erlaubt,
   aber kein ungeprüfter Dirty-Input oder heutiger M0-HEAD-Ersatz für M1.
3. Exaktes freigegebenes Rezept, Toolchain-/Imageklasse, Packaging/ABI,
   Dependency-Artefaktklasse und Verifikationsmetadaten. Keine unbekannten Werte.
4. Build, Lint, JVM-Inventur/-Ergebnisse und sämtliche Python-/M0-/M1-Prüfungen
   nach demselben tatsächlichen Lauf vollständig erfolgreich, kein Skip. Die eigene neue size-Quittung wird hier
   noch nicht vorausgesetzt; die Vorbelegmenge ist die Pflichtmenge ohne size.
5. Exakte Developmentidentität/-Flags; konsistente Version; Pin unabhängig vor
   Build etabliert, APK kryptografisch an ihn gebunden; Produktionsprüfung lehnt ab.
6. Profilerreport mit tatsächlicher APK identisch; unveränderliche Referenzbytes
   und Vergleichsreport korrekt gebunden; Differenzen selbst nachgerechnet.
7. Komparator Exit 3 ist als **Diagnosestatus** bestätigt, aber nicht als Erfolg
   umgedeutet. Sein Status wird unverändert gespeichert. Die einzige zulässige
   technische Abweichung gegenüber seinen vollständigen matching_recipe-Status
   ist `Different signer fingerprints; no comparable signing policy supplied`,
   und nur wenn das Gate unabhängig die explizite Ephemeral-Regel aus Abschnitt 6
   vollständig belegt. Keine anderen Gründe oder missing_information zulassen.
   Keine Pin-Umschreibung, keine manipulierte „normalisierte“ Profilkopie.
8. Vorgeschlagene und erst noch zu genehmigende Rohgrößenregel erfüllt, ohne
   Zuschlag, Rundung auf MB, Abzug von Signaturen oder Metadatenkorrekturen.
9. Frische, sichere Ausgabepfade und unveränderte Eingabe-/Artefaktbytes auch
   nach der Entscheidung. Positive Quittung bindet Context-, Root-, Ref-,
   Source-, APK-, Profil-, Vergleichs- und Belegdigest sowie Run-ID/-Attempt.

Ein beliebiges size-verdict.json mit passed=true ist keine Livequittung. Der
Helper erhält die Entscheidung nur vom gehashten Engineprüfer im selben
kontrollierten Lauf, kontrolliert Exit und alle Bindungen und prüft den APK-Hash
vor Staging erneut. Die Quittung ist nicht portabel auf einen anderen Run.

Die Quittungsmengen sind phasen- und modusgebunden, ohne Kreisbezug:

- Candidate-Vorbelege = bisherige REQUIRED ohne size plus reference-contract,
  source-inputs, dependency-artifacts, size-profile, size-comparison.
- Candidate-Finalmenge = Candidate-Vorbelege plus size; ausschließlich diese
  Menge darf zur approve-Grenze gelangen.
- Reference-Capture-Vorbelege = bisherige REQUIRED ohne size plus
  bootstrap-contract, source-inputs, dependency-artifacts, size-profile.
  Sie brauchen keine noch nicht existierende freigegebene Referenz und keinen
  darauf basierenden Komparatorbericht. size wird ausdrücklich als
  not_applied_reference_capture berichtet. Diese Menge ist niemals zur
  Kandidatenfreigabe hinreichend; evaluate und approve lehnen den Modus ab.

`REQUIRED` bleibt im Kandidatenmodus eine exakt kontrollierte Menge: die bisherigen Pflichtnamen
werden nicht entfernt; `size` wird **nur im aktivierten genehmigten
Development-Ersatzpfad** durch den neuen Prüfer erfüllt. Neue Quittungen wie
reference-contract, source-inputs, dependency-artifacts, size-profile und
size-comparison werden zusätzlich Pflicht. Ein fehlender Ersatz ist kein Grund,
size auszulassen. Release führt weiterhin unverändert apk-size-check aus;
M1-Release-/Tag-Sperren werden dabei nicht freigeschaltet.

### 8.3 Strenge Testkontrolle der späteren Implementierung

Die vorhandenen 56 CI-, 37 M0- einschließlich 12 SDK-Integrations- und 11
Manifesttests bleiben erhalten und laufen mit exakter Erfolgskontrolle ohne
Skips. Der Vergleich mit Exit 3 darf nicht mit einem unittest-Prozess verwechselt
werden: Testprozesse brauchen erfolgreichen Exit **und** vollständige erwartete
Ergebnisdaten. Fehlende/unlesbare/widersprüchliche Ergebnisdateien sind Fehler.

Zusätzlich verbindlich: vorhandene 19 Profiler- und 60 Komparator-Tests sowie
**28 ausdrücklich gepflegte neue Testfälle** des Vertragsprüfers. Sollmenge/Sollzahl
stammt aus Enginecode, nicht aus der aktuellen Discovery. Das wären nach
Implementierung 211 Python-Fälle; dies ist **kein heute ausgeführtes Testergebnis**.
Die bestehende CI-Sollzahl 56 bleibt bestehen, solange nur ihre Fixtures angepasst
und keine neuen Testmethoden in dieser Datei ergänzt werden. Die 28 neuen Fälle
liegen separat; eine bewusst geänderte Anzahl erfordert dokumentierte Anpassung.

JVM: bestehende Development-Filter und zwei Originalklassen unverändert erhalten;
Class-/Methodenidentitäten aus der reviewed Modul-/Kotlin-Inventur, eindeutige
frische JUnit-XMLs, vollständige Methodenmenge, konsistente Zähler, keine Skips.
Parametrisierte JUnit-Fälle sind Laufdaten, keine aus @Test-Zeilen erfundene Zahl.
Kein verbundenes Testen, Screenshot-Recording oder Devicegate.

## 9. Normative Datenbindung des neuen Vertragsprüfers

Diese Formate sind Spezifikation der späteren Implementierung, noch keine
vorhandenen freigegebenen Datensätze. Bestehende Profil-/Vergleichsschemata
bleiben unverändert. Alle neuen Parser verwerfen doppelte/unbekannte Felder,
fehlende Werte, NaN/Infinity, Bool als Zahl, unsichere Pfade und freie URLs.
SHA-256: genau 64 kleine Hexzeichen; Git-SHA dieses Repositories: genau 40.
IDs und Größen sind positive beziehungsweise ausdrücklich nichtnegative Integer;
keine aus Strings konvertierten Erfolgswerte. Statuswerte sind geschlossene Enums.

Neue Manifeste werden kanonisch serialisiert: UTF-8, `ensure_ascii=False`,
`sort_keys=True`, `separators=(',', ':')`, `allow_nan=False`, abschließend genau ein
LF. Hash umfasst sämtliche Dateibytes einschließlich LF. Arrays sind nach der
jeweils eindeutigen logischen Identität sortiert, ohne Duplikate. Bereits vorhandene
Profiler-/Komparator-Dateien werden dagegen **roh gehasht, nicht neu serialisiert**.
Die alte M1.4.2-Quellinventur benutzte für ihren Inhaltsdigest ein anderes,
explizit dokumentiertes Verfahren; deren Digest wird nicht einfach umbenannt.
Je JSON maximal 16 MiB, Root maximal 8 KiB, höchstens 100.000 Dateieinträge;
Überschreitung ist Fehler und führt nicht zu Teilprüfung. Quittungen und
Kontextdateien dürfen nicht nach vorhandenen Dateinamen „wiedergefunden“ werden.

Folgende Pflichtfelder sind pro neuem Format festgelegt; Unterobjekte werden in
der Implementierung ebenfalls geschlossen validiert:

| Schema | Pflichtfelder neben schema |
| --- | --- |
| tvhome.size-source-inputs/1 | repository_id, commit, git_tree, files, injected_inputs, effective_inputs_sha256 |
| tvhome.size-dependency-artifacts/1 | artifacts, verification_metadata_sha256, strict_verification, coverage |
| tvhome.size-toolchain/1 | runner, versions, distributions, files, actions |
| tvhome.size-recipe/1 | variant, application_id, tasks, gradle_arguments, engine_manifest_sha256, init_script_sha256, configuration_files, environment, sdk_levels, packaging, signing_policy, version_policy |
| tvhome.size-build-evidence/1 | mode, platform, source_sha256, recipe_sha256, toolchain_sha256, dependencies_sha256, apk_sha256, apk_bytes, profile_sha256, started_at, finished_at, version_code, signing, checks |
| tvhome.size-bootstrap/1 | repository_id, owner_id, source_commit, engine_commit, engine_manifest_sha256, verification_commit, verification_path, verification_sha256, toolchain_commit, toolchain_path, toolchain_sha256 |
| tvhome.size-reference-context/1 | bootstrap_sha256, repository_id, owner_id, engine_commit, source_commit, platform, private_run_nonce |
| tvhome.size-gate-context/1 | root_sha256, repository_id, engine_commit, source_commit, platform, reference_commit, reference_manifest_sha256, policy_id, private_run_nonce |
| tvhome.size-gate-verdict/1 | status, reason_codes, context_sha256, root_sha256, reference_manifest_sha256, source_sha256, evidence_sha256, apk_sha256, profile_sha256, comparison_sha256, platform, reference_bytes, candidate_bytes, delta_bytes, policy_id |
| tvhome.development-size-reference/1 | reference_id, policy_id, repository_id, source_commit, engine_commit, platform, variant, application_id, apk_sha256, profile_sha256, file_bytes, source_sha256, recipe_sha256, toolchain_sha256, dependencies_sha256, selection, files |
| tvhome.size-reference-measurement/1 | evidence_sha256, profile_sha256, apk_sha256, file_bytes, source_sha256, recipe_sha256, toolchain_sha256, dependencies_sha256, platform, release_budget_observation |
| tvhome.size-reference-approval/1 | reference_id, reference_profile_sha256, reference_apk_sha256, file_bytes, policy_id, approved_by, approved_at, operator_record |

Ein Referenzmanifest behauptet keinen Hash seiner eigenen Bytes: Sein Hash kommt
nur aus dem Betreiberroot. Die selbstbezugsfreie source effective_inputs_sha256
wird über die kanonische Projektion `{files, injected_inputs}` gebildet; das äußere
Manifest hat zusätzlich seinen eigenen Dateihash. Zeiteinträge in Laufbelegen
sind UTC-ISO-8601 und müssen mit echter Runreihenfolge konsistent sein.
`private_run_nonce` ist ein Zufallswert zur lokalen Laufbindung, kein Secret und
kein Ersatz für die GitHub-Herkunft. Er verleiht fremden JSONs keine Autorität.

- `platform` enthält repository_id, event, ref, workflow_path, workflow_commit,
  run_id, run_attempt, job_id und source_commit. Sie stammen vor dem Build aus
  dem kontrollierten Actionskontext; Referenzläufe werden über tatsächliche
  GitHub-Runmetadaten überprüft. JSON-Eigenangaben genügen nicht.
- `files`/`configuration_files` binden sicheren relativen path, mode, bytes und
  sha256. Die Sourceinventur enthält alle tatsächlichen Eingaben; zulässige neue
  Buildoutputs sind nicht nachträglich zu Inputdateien zu erklären. Unerwartete
  Sourceänderungen bleiben Fehler. Die Referenz-files-Liste bindet genau die
  zehn anderen Bundledateien aus Abschnitt 4.4, nicht das äußere Manifest selbst.
- `artifacts` bindet je Artefakt component, artifact (Name/Classifier/Extension),
  roles, configurations, bytes, sha256. roles/configurations sind sortierte
  eindeutige Listen. Identische Bytes verschiedener Identitäten werden nicht
  versehentlich als dasselbe Modul gezählt. Absolute Downloadcachepfade sind
  Laufbeobachtungen, keine portable Dependencyidentität. Der Collector führt
  zusätzlich eine gebundene Livepfadzuordnung für erneute Dateiprüfung; keine
  beliebigen vom PR übergebenen Pfade außerhalb der erlaubten Auflösungsorte.
- `strict_verification` muss true sein; coverage muss runtime, compile, tests,
  buildscript, plugins und descriptors jeweils als vollständig nachweisen.
  Ein solches Bool aus einer beliebigen Datei ist kein Beweis: Engine prüft
  tatsächliche Gradle-Verifikation, XML-Pin und erfasste reale Artefaktbytes.
- `runner` bindet os, image_os, image_version und architecture; `versions`
  Java inklusive Vendor/Build, Python, Gradle, Kotlin-Plugin, AGP, D8/R8,
  SDK-Tools/Build-Tools/Platform. distributions und actions binden wirkliche
  Archivhashes beziehungsweise vollständige Actioncommits. Tooldateipfade sind
  relativ zur jeweiligen expliziten Distribution und werden mit Inhalten gehasht.
- `signing` in Laufbelegen enthält expected_certificate_sha256 (vorab),
  observed_certificate_sha256, public_certificate_der_base64, certificate_profile,
  signature_schemes und private_key_cleanup. Öffentliche DER-Bytes werden gegen
  ihren Pin geprüft und mit vorhandenen SDK-/keytool-Werkzeugen ausgewertet;
  keine privaten Schlüssel. Das Referenz-signing_policy enthält Profil/Schemata,
  **keinen über beide Läufe als identisch behaupteten Pin**.
- `environment` ist eine feste nicht geheime Allowlist von Buildwerten; neue
  Gradle/JVM-Injektionswerte wie JAVA_TOOL_OPTIONS, JDK_JAVA_OPTIONS, GRADLE_OPTS,
  fremde Initdateien oder ungebundene Nutzer-Gradleproperties sind unzulässig.
  Source-/Keystore-/Runpfade sind nur nach der festen Regel normalisierbar.
  Tokens und Actions-Schreibkanäle (GITHUB_OUTPUT/GITHUB_ENV/GITHUB_PATH,
  Runtime-Uploadtoken) werden nicht an Gradle-/App-Subprozesse weitergereicht.
  Der kontrollierende Helper behält seinen eigenen, vorher gebundenen
  Ausgabekanal; er übernimmt keine fremden Bereitschaftszeilen.
- `checks` im unveränderlichen Kandidaten-build-evidence enthält genau die
  gepflegte **Vorbelegmenge ohne size**; alle Einträge binden status,
  receipt_sha256 und Runherkunft und müssen passed sein. Erst der danach
  erzeugte size-verdict vervollständigt die finale REQUIRED-Menge im Helper.
  reference-capture verwendet exakt die separat festgelegte Bootstrap-Vorbelegmenge
  aus Abschnitt 8.2 plus size mit dem ausschließlich dort erlaubten Status
  not_applied_reference_capture. Seine separate
  release_budget_observation dokumentiert auch einen fehlgeschlagenen alten
  Releasecheck; keine dieser Angaben kann ein Kandidaten-verdict begründen.
  Pipeline-JSONs dürfen nicht allein ihre eigene Glaubwürdigkeit setzen.
- `selection` ist exakt `larger_of_two_measurements_tie_first`; beide
  Messungen müssen in allen Vergleichsinputs außer erlaubter Source-unabhängiger
  Laufmetadaten-/Zertifikatvariation gleich sein. Die externe Ownerfreigabe billigt
  anschließend genau den gewählten Vorschlag; Auswahl ist nicht Freigabe.
- verdict.status ist passed oder rejected; nur evaluate mit passed und vollständigen
  Bindungen kann eine neue Größenquittung erzeugen. Fehlerausgaben/alte verdicts
  werden nie als positive Quittung angenommen. Kein Feld production_approved=true,
  keine Uploadautorität im neuen Prüfer.

Der Implementierungsauftrag soll diese geschlossenen Unterstrukturen direkt als
kleine Standardbibliotheksvalidatoren und Fixtures abbilden; keine automatische
Schema-Erweiterung und keine freie Vertrauensquelle. Tatsächliche Versionsnummern,
Hashes, Runnerwerte oder Keyschemata sind keine auszufüllenden Fantasiewerte.
Solange ein Pflichtbeleg fehlt, existiert kein aktivierbarer Vertrag.

## 10. Konkrete Testmatrix für den Folgeauftrag

Die 28 Zeilen bilden 28 unittest-Testfälle; Varianten einer Zeile können Subtests
sein und dürfen die Fallzahl nicht künstlich erhöhen. Temporäre Fixtures sind
explizit simulierte Vertragsbelege, keine APK-/Actions-Erfolge.

| Nr. / vorgeschlagener Testfall | Nachweis / erwartetes Verhalten |
| --- | --- |
| 01 complete_anchored_candidate | Vollständige gültige Livekontext-/Referenz-/Artefaktquittungen; zulässige Größe: nur interne Development-Größenquittung. Genesis-Aufnahme dagegen nur measurement_only, nie Freigabe. |
| 02 missing_reference | Kein Bundle/Profil/Manifest: keine Freigabe. |
| 03 reference_tampered | Geänderte Referenzbytes einschließlich Whitespace: Root-/Profilhashfehler. |
| 04 untrusted_root | Root aus PR-Datei/CLI oder manipulierter erwarteter Hash statt Betreiberroot: Ablehnung. |
| 05 same_run_reference | Referenz aus Kandidaten-Run/-Attempt oder nicht früher abgeschlossenem Run: Ablehnung. |
| 06 baseline_self_update | Kandidat schreibt/erhöht Root/Baseline oder liefert eigene Approvalbehauptung: Ablehnung. |
| 07 missing_dependency_bytes | Pflichtartefakt/Plugin/POM/Verifikationsliste fehlt: Ablehnung. |
| 08 changed_dependency_bytes | Gleiche Koordinate, andere SHA/Dateibytes, auch aus Cache: Ablehnung. |
| 09 coordinates_only | Nur Textgraph, fehlende tatsächliche Artefakt-/Verificationabdeckung: Ablehnung. |
| 10 unknown_toolchain | Fehlender JDK-/SDK-/Image-/Gradle-Pin oder unverifizierter latest-Ersatz: Ablehnung. |
| 11 changed_toolchain | Geänderte Toolchainbytes/Runnerklasse bei gleicher Versionsbehauptung: Ablehnung. |
| 12 changed_packaging | Andere ABI-Menge, Kompression/Minify/Shrink/Ausschlüsse oder widersprüchliche Konfiguration: Ablehnung. |
| 13 wrong_identity | Original-ID, falsche Development-ID/-Variante oder TestOnly/Debuggable-Verwechslung: Ablehnung. |
| 14 wrong_run_signer | APK-Pin stimmt nicht mit vorab unabhängig erzeugtem Run-Pin überein: Ablehnung. |
| 15 legitimate_ephemeral_signers | Zwei verschiedene korrekte Run-Pins mit vollständig gleichem freigegebenem Zertifikats-/Signaturprofil: separat vergleichbar; beide Pins unverändert berichten. |
| 16 prohibited_signature | Produktions-/fremde Debugsignatur, fehlender Debugstatus, falsches Key-/DN-/Schemaprofil: Ablehnung. |
| 17 variable_version_metadata | Zulässige andere Minute/VersionCode: keine Identitätsverwechslung; kein automatischer Byteabzug, ein dadurch positiver Rohdelta bleibt blockiert. |
| 18 invalid_version_metadata | Widersprüchlicher oder außerhalb des tatsächlichen Buildfensters liegender Code: Ablehnung. |
| 19 growth_not_authorized | Ein Byte über konkreter freigegebener Referenz: Ablehnung, keine neue Baseline. |
| 20 smaller_candidate | Kleinerer Kandidat bei komplett gültiger Klasse: Größenbedingung erfüllt; andere Gates bleiben erforderlich. |
| 21 missing_mandatory_receipt | Jede fehlende/übersprungene Pflichtquittung einschließlich M0/Inventur: keine Freigabe. |
| 22 failed_m0_m1 | M0-/M1-/Production-Rejection-Erwartung fehlschlägt oder Beleg aus anderem Lauf: keine Freigabe. |
| 23 python_accounting_failure | Testfehler, Skip, kleiner/leerer/unerwarteter Bestand oder ungültiger Ergebnisdatensatz: keine Freigabe. |
| 24 jvm_accounting_failure | Fehlende Klasse/Methode, doppelte/falsche/alte XMLs, parametrischer Fallfehler oder Skip: keine Freigabe. |
| 25 build_lint_apk_failure | Build/Lint fehlgeschlagen; APK fehlt/ist alt/mehrdeutig oder mutiert vor Staging: keine Freigabe. |
| 26 comparison_not_valid | Komparator Exit 0/2/unerwartet, fehlender Bericht, andere Inputhashes, weitere Ablehnungsgründe oder missing_information: keine Freigabe; kein pauschales Exit-Ignorieren. |
| 27 manipulated_pr | PR ändert Workflow/Prüfer/Root oder erfindet grüne JSONs/Artefaktnamen: niemals autoritative Masterfreigabe; keine privilegierte Fortsetzung. |
| 28 release_or_untrusted_event | Tag, fremder Fork, Featurepush, Reference-Capture-Kontext, falscher Job/Run/Attempt oder unzulässiger Uploadpfad: keine Development-/Releasefreigabe. |

Die bestehenden Upload-/M1.3-Negativtests bleiben ebenfalls verbindlich: neue
Runordner, alte Dateien nicht übernehmen, Diagnose-/Approved-Trennung, Bereinigung
bei Fehlern, kein Secret/Keystore im Export. Fixture-Anpassungen dürfen diese
Tests nicht durch simulierte Erfolgscodes bedeutungslos machen.

## 11. Exakte spätere Dateiliste und Umsetzung

Heute wird ausschließlich dieses Dokument hinzugefügt. **Folgender eine
Implementierungsauftrag M1.4.5** darf nach gesonderter Freigabe genau die zehn
unten genannten Repositorypfade bearbeiten. Kein weiteres Profilerprojekt.

| Datei | Später notwendige Änderung |
| --- | --- |
| `.github/workflows/build.yml` | Action-SHAs, exakte Toolchain-/Runnerbindung; getrennte Engine-/Sourcecheckouts; kontrollierte Rootzufuhr; Master-only-Freigabe; unveränderte Tagblockade und frische getrennte Diagnose-/Approved-Artefakte. |
| `.github/workflows/size-reference.yml` (neu) | Manueller, mastergebundener Resolution-/Zweijob-Referenzaufnahmeablauf, Evidence-only; Inhaber-Dispatchprüfung; keine automatische Root-/Branchänderung, kein APK-Upload. |
| `.github/CODEOWNERS` (neu) | Inhaber als Reviewer für alle Änderungen, insbesondere Workflow/Engine/Gradle/Vertrauensdaten; Schutzkonfiguration bleibt Betreiberaufgabe. |
| `scripts/ci-development.py` | Source-/Engineroot trennen; streng aktivierter Kontext; Livebelege/Profil/Komparator/Vertragsprüfer; bestehende Checks behalten; strikt getrenntes reference-capture ohne Freigabe; Legacygröße bleibt ohne aktivierten Vertrag maßgeblich. |
| `scripts/ci-size-gate.py` (neu) | Genau preflight/prepare/evaluate und Referenzaufnahmeunterstützung nach diesem Vertrag; Standardbibliothek, Git/SDK; kein zweiter Messprofiler, kein automatischer Baselinewriter. |
| `scripts/tests/test_ci_development.py` | Vorhandene 56 Fälle erhalten; Fixtures und Orchestrationsprüfungen auf zusätzliche Gates/Runbindung erweitern, keinen Schutz entfernen. |
| `scripts/tests/test_ci_size_gate.py` (neu) | 28 konkrete SDK-freie Vertrags-/Negativfälle nach Abschnitt 10. |
| `gradle/size-gate.init.gradle.kts` (neu) | Tatsächliche Artefaktbyte-Inventur und registrierte Dokumentationsaufgabe; keine App-/Manifest-/Packagingänderung und keine Gerätetasks. |
| `gradle/wrapper/gradle-wrapper.properties` | Echte separat geprüfte distributionSha256Sum für vorhandenes Gradle 9.8.0 ergänzen, keine Gradle-Aktualisierung. |
| `docs/m1-development-size-gate-contract.md` | Tatsächliche Implementierungs-/Validierungsdaten und noch deaktivierte Aktivierungsvoraussetzungen nachtragen. |

Keine Änderung an `perf-budget.json`, `scripts/apk-size-check`, den beiden
vorhandenen Profil-/Vergleichswerkzeugen, M0-/M1-Prüfern, App-Gradle, Kotlin oder
Manifesten. Referenzbundle-Dateien entstehen später auf der separaten
size-references-Branch nach Inhaberfreigabe; sie sind kein verdeckter elfter
Implementierungspfad im jetzigen Arbeitsbaum. Secrets, temporäre Keystores und
SDK-/Cacheverzeichnisse sind nie Gitdateien.

### Konkrete Abnahme von M1.4.5

1. Alle bisherigen 56 und neuen 28 Sicherheits-/Orchestrationstests sowie die
   bestehenden 19/60 Profiler-/Komparatortests tatsächlich ohne Skips ausführen.
   SDK-abhängige 37 M0- und 11 Manifesttests vollständig ausführen, sofern im
   Folgeauftrag freigegeben/verfügbar; Ausfall als nicht vollständig validiert
   ausweisen und Aktivierung sperren, nicht Testzahlen erfinden.
2. Workflow-/Kontext-/Ereignismatrix und alle 28 Negativfälle prüfen; ohne
   aktivierten Betreiberroot bleibt der bisherige Größenfehler reproduzierbar.
   Vollständiger Patch einschließlich neuer ungetrackter Dateien und
   `git diff --check`; exakt zehn freigegebene Pfade, App/Releasebudget unverändert.
3. Code kann lokal mit Fixtures umgesetzt werden, ohne eine Actions-Auslösung
   zu behaupten. Anschließend sind nur die **fest definierten** Bootstrap- und
   zwei kontrollierten CI-Builds aus Abschnitt 7 vor Aktivierung nötig. Ein
   vollständiger End-to-End-Erfolg darf erst nach diesem realen Nachweis und
   konkreter Referenzfreigabe berichtet werden. Kein lokales Testresultat ersetzt
   diese Plattformbelege. Keine neue offene Konzept-/Warm-Cache-Messphase.
4. Die konkrete Toolchain-, Gradle-Collector- und Workflowverträglichkeit wird
   in diesen CI-Aufnahmen geprüft; fehlende Abdeckung blockiert. Ein positiver
   Fixturelauf darf weder Bootstrapdaten freigeben noch Gradle-/SDK-Verfügbarkeit
   vortäuschen. Keine automatische Actions-, Publish- oder Installationsaktion.

## 12. Offene Nachweise und eine Inhaberentscheidung

| Stand | Nachweisstatus |
| --- | --- |
| Vorhandener lokaler Code, frühere Berichte/JSONs und statische Regeln | Hier gelesen und hashgeprüft; keine Tests/Builds neu ausgeführt. |
| 60 M1.4.3-Tests, drei M1.4.2-Builds und zwei Profilvergleiche | Vorhandene historische Ergebnisdateien; kein heutiger Actions-Lauf. |
| Geschützter Master/Referenzbranch, Rechte und Rootvariable | Nicht remote geprüft/eingerichtet; verpflichtende Aktivierungsvoraussetzungen. |
| Reviewed vollständiger Source-/Enginecommit mit allen Entwicklungsänderungen | Im lokalen M0-HEAD nicht enthalten; später gesondert sichern/veröffentlichen. |
| Verifizierte tatsächliche Dependency-/Pluginartefakte und freigegebene XML | Noch nicht verfügbar; keine Verifikation aus Textgraphen behauptet. |
| Exakte Actions-SHAs, SDK-/JDK-/Gradlearchivhashes und Runnerklasse | Noch nicht als freigegebener CI-Satz vorhanden; konkrete Bootstrapinputs erforderlich. |
| Zwei vergleichbare CI-Aufnahmen mit unabhängigen Ephemeralschlüsseln | Nicht ausgeführt; minimaler Plattformnachweis vor Referenzaktivierung. |
| Numerischer Referenzwert und Freigabe | Nicht vorhanden; lokale 20.238.741 Bytes werden nicht eingesetzt. |

**Einzige jetzt benötigte Inhaberentscheidung für den nächsten Auftrag:** Soll
die heute auf Development angewandte Release-Ratchet später ausschließlich für
Development durch die hier beschriebene, vertrauenswürdig gebundene CI-Referenzregel
**ohne automatisch zugelassene positive Größenzunahme** ersetzt werden, während
Release unverändert bei 3.563.000 Bytes bleibt? Empfehlung: ja, mit bewusst
akzeptierten technischen Fehlalarmen und separater Referenzfreigabe.

Dies genehmigt noch keinen Referenzwert. Die spätere konkrete Referenzprüfung
und ihre manuelle Aktivierung sind das bereits festgelegte Betriebsverfahren,
keine weitere offene Architekturentscheidung. Ohne Zustimmung zur Ersatzregel
wird sie nicht implementiert; die heutige Blockade bleibt erhalten.

M1.4.4 endet mit diesem Vertrag und dem separaten Prüfbericht. Keine Baseline,
kein Budget, kein Upload und kein Produktions-/Fire-OS-Kompatibilitätsnachweis.

## 13. M1.4.5 – lokal implementiert, nicht aktiviert

Der Inhaber hat das Prinzip `candidate.file_bytes <= reference.file_bytes`
für die lokale Implementierung genehmigt. Das ist keine Freigabe eines
Referenzwerts, einer Actions-Ausführung oder der derzeitigen Größenersetzung.
Die Abschnitte 1–12 bleiben als ursprünglicher Vertrag erhalten; deren damalige
Entscheidungsfrage ist hinsichtlich dieses Prinzips beantwortet.

### Umgesetzte Pfade

Die zehn vereinbarten Dateien enthalten jetzt die lokale Umsetzung. Die
bestehenden Profiler, Komparatoren, M0-/M1-Prüfer, App- und Manifestdateien sowie
perf-budget.json/apk-size-check sind unverändert. Der neue Prüfer hat ausschließlich
GET-Zugriffe auf feste API-Pfade im eigenen Fork; keine Settings-, Ref-, Release-
oder Upload-Schreibschnittstelle. Es gibt keinen Baselinewriter.

- preflight: strikt geschlossener Betreiberroot aus der Workflowvariable,
  volle Commit-/Manifestbindungen, kanonische Rootbytes, eigener Fork, aktive
  zulässige Run-/Jobmetadaten, unabhängige frühere Referenzaufnahme, vollständiges
  Bundle, Engine- und Quellinventur. Beide Referenzmessungen müssen zu den
  tatsächlich erfolgreich abgeschlossenen capture-1/capture-2-Jobs desselben
  Run-/Attemptkontexts gehören. Kein PR-/Feature-/Tag-Kandidatenmodus.
- prepare: neue Buildkopie aus dem tatsächlichen sauberen Sourcecommit, einzige
  kontrollierte XML-Injektion, effektive Inputinventur, eigener frischer
  GRADLE_USER_HOME, separate private Verzeichnisse, Prüfung wirklicher Toolbytes.
- CI-Helper: Engine-/Sourcepfade getrennt; 56/37/11 bestehende Tests und zusätzliche
  28/19/60 Gruppen streng ohne Skips/erwartete Fehlschläge; bestehende JVM-Filter
  und Identitäts-/Ergebnisprüfung unverändert; vollständige bisherige Schutzgates.
- Vertragsmodus: ausgeführt wird die zuvor bytegeprüfte Gradle-9.8-Distribution
  direkt, damit keine zweite ungeprüfte Distribution über einen frischen
  Wrappercache zum tatsächlichen Compiler wird. Der Wrapper bleibt als Sourceinput
  gebunden; seine offizielle distributionSha256Sum ist zusätzlich ergänzt und
  bleibt für den Legacyweg wirksam. Versionswechsel findet nicht statt.
- Dependency-Collector: globaler Listener vor Settings-/Pluginauflösung, reale
  Artefaktdateien und native Maven-Deskriptoren aus dem frischen Cache; rohe
  AAR/JAR-Eingaben statt abgeleiteter Transformdateien. Python prüft die Bytes
  nochmals gegen die unabhängig freigegebene Verifikationsliste.
- Das von den ausgewählten Robolectric-Klassen verwendete SDK 35 wird über die
  zusätzliche Testkonfiguration sizeGateTestSdk durch Gradle verifiziert und für
  die Tests offline materialisiert. Die tatsächlich lokal vorhandene Version ist
  `15-robolectric-13954326-i7`; sie wurde aus dem bestehenden Robolectric-Stand
  und vorhandenen SDK-JAR ermittelt, nicht als neue CI-Messung ausgegeben.
  Keine Implementierungsdependency der APK und keine Änderung der Testauswahl.
  Andere SDK-Anforderungen scheitern ohne ausdrücklich geprüfte Erweiterung;
  es gibt keinen ungeprüften Maven-Nachladeweg für Robolectric-SDKs.
- Temporärer Schlüssel: Pin und öffentlicher DER-Inhalt vor dem Build gebunden;
  private Tempdaten vor finalem Urteil gelöscht. Zusätzliche Prüfung auf Kopien
  des eigenen privaten Schlüsselmaterials in Sourceoutputs, Reports und Cache.
- Ephemerale Signierer: eigene unabhängige Run-Pins bleiben verschieden; exaktes
  genehmigtes Profil und Schemakombination bleiben Pflicht. SHA256withRSA und
  SHA384withRSA sind starke zulässige Zertifikatsalgorithmustypen der Aufnahme;
  der konkrete Typ muss anschließend exakt zum Referenzprofil passen. Keiner
  wird als bereits beobachteter künftiger CI-Standard behauptet. Keytool-Flags
  wurden nicht auf einen erfundenen oder schwächeren Standard umgestellt.
- Bestehender Komparator wird tatsächlich mit kontrolliertem Exit 3 ausgeführt;
  sein Report wird aus den gebundenen Profilen nachgerechnet. Nur der separate
  Prüfer kann eine interne Größenquittung liefern. Der öffentliche evaluate-Weg
  revalidiert Kontext, tatsächliche APK-/Manifest-/Signatur-/JVM-Daten und
  führt den Diagnosevergleich erneut aus. JSONs allein reichen nicht.
- Referenzmodus: eigenständiger Bootstrapkontext, ausschließlich measurement_only,
  nie approve/apk_ready. Resolution-only liefert ausdrücklich ungeprüfte Vorlagen;
  die zwei Aufnahmejobs können keinen Root oder Referenzbranch schreiben.
- Finale Ausgabe: summary wird nach Schlüsselbereinigung und vollständigem Urteil
  vor dem Staging geschrieben; gehashter Vorbeleg und finales Urteil bilden
  keinen Kreisbezug. Das Bereitschaftssignal wird ausschließlich vom Elternprozess
  geschrieben; Buildkinder erhalten dessen Actions-Ausgabekanal nicht. Kein
  vorhandener Ausgabeort wird wiederverwendet.

### Präzisierungen der Dateiformate und Toolauswahl

Neue Vertrags-JSONs werden kanonisch mit abschließendem LF verarbeitet. Auch der
Betreiberroot muss exakt diese Bytes besitzen; bloß neu berechnete Erwartungen
vom Kandidaten sind keine Alternative. Eigene Auflösungs-/Vorbereitungsdateien
wie resolved-inputs.json, input.json und signer-pin.json sind nur lokale
Transportdaten, keine neue Vertrauensquelle. Der unabhängige Pin stammt weiterhin
vor dem Build aus dem erzeugten öffentlichen Zertifikat. Private Schlüsseldigests
bleiben interne Tempdaten und werden nicht in Berichte oder Bundle exportiert.

Toolchain-Dateien erlauben zusätzlich gebundene interne Symlinks mit mode 120000:
bytes/sha256 betreffen dann den UTF-8-Linktext; das Ziel muss innerhalb derselben
geprüften Distribution existieren. Zielinhalte werden ebenfalls inventarisiert.
Dies bildet reale JDK-Lizenzlinks und SDK-Versionsaliases ab, statt sie still zu
ignorieren. Quellinventuren erlauben weiterhin keine Symlinks/Submodules/LFS-
Platzhalter. Ungebundene oder externe Toollinks werden abgelehnt.

Die SDK-Inventur umfasst cmdline-tools, build-tools, platforms und gegebenenfalls
packages.xml; unbenutzte Emulator-/NDK-Installationen sind keine Compilerinputs
unseres unveränderten App-Rezepts. Alle vorhandenen Revisionen in diesen
Compilerbereichen und deren Aliases werden gebunden. Änderungen daran bleiben
Ablehnungsgründe. JAVA_HOME, ANDROID_HOME sowie TVHOME_GRADLE_HOME beziehungsweise
GRADLE_HOME/PATH lokalisieren nur vorhandene Dateien; sie sind keine Autorität.
Der Prüfer vergleicht wirkliche Bytes, bindet die aufgelösten Toolpfade und führt
Java erst nach deren Hashkontrolle aus. Die neuen Jobs installieren keine
ungeprüften Ersatztools. Eine nicht passende vorhandene Runner-Toolchain sperrt
Vorbereitung/Aufnahme; deren tatsächliche Verfügbarkeit ist weiter offen.

Die CI-Caller müssen aus dem geschützten Workflow stammen. Ein lokaler Aufrufer
kann Umgebungsvariablen nachstellen; ein lokales Urteil ist deshalb keine
plattformauthentifizierte, portable Uploadfreigabe. API-Metadaten werden geprüft,
aber keine OIDC-/Hardwareattestation behauptet. PR-eigene Artefaktnamen sind
weiterhin keine autoritative Freigabe. Die Betreiberbedingungen aus Abschnitt 4
sind unverzichtbar und noch nicht remote nachgewiesen.

### Deaktivierungszustand und nötige Aktivierungsnachweise

Der bisherige Helperweg führt weiterhin apk-size-check aus. Ohne vollständigen
aktivierten Vertragskontext fehlt zusätzlich die neue Vertragsbestätigung, sodass
selbst eine kleine Legacy-APK nicht zur Freigabe gelangt. Im bereitgestellten
Workflow ist der bestehende APK-Upload ausdrücklich false-gesperrt. Der neue
Vertragsjob und der Referenzworkflow haben ebenfalls einen false-Interlock.
Eine bloße Settingsvariable kann keinen dieser Jobs aktivieren. Referenz-Evidence
wird nur nach einer später gesondert freigegebenen Entsperrung und kontrollierter
Aufnahme über eine Dateiliste ohne APKs, Schlüssel oder Caches exportiert.

Offen bleiben: unabhängiges Review dieser Implementierung; vollständig gesicherter
und reviewter Source-/Enginecommit; Branch-/CODEOWNERS-/Rechteregeln; verifizierter
konkreter Toolchainsatz samt tatsächlicher Runnerklasse; geprüfte Dependency-
Verifikationsliste und vollständige Android-/Plugin-Abdeckung; die zwei
kontrollierten realen CI-Aufnahmen und die konkrete Inhaberfreigabe des Bundles.
Erst danach darf separat über das gezielte Entsperren und Aktivieren entschieden
werden. Es wurde kein solcher externer Schritt ausgeführt.

### Lokale Validierung und ihre Grenze

Die Python-Gruppen wurden über den strengen CI-TestRunner mit ausdrücklich
gepflegter Sollzahl ausgeführt: 28 Vertrag, 56 bestehende CI, 19 Profiler,
60 Komparator, 37 M0 einschließlich aller SDK-Integrationsfälle und 11 Manifest.
Alle 211 Fälle bestanden ohne Fehler oder Skips. M0 erzeugte ausschließlich
kurzlebige SDK-Test-APKs; kein Launcher-Neubau, keine Installation.

Reale gerätefreie Gradle-9.8-Proben erfolgten nur in separaten minimalen
Java-/Maven-Fixtures: Settings- und Projekt-Plugin, tatsächliche Erfassung aller
sechs Rollen, native strikte Verifikation und erneute Python-Datei-/XML-Prüfung.
Deren Artefakte einschließlich des SDK-namigen JARs sind ausdrücklich synthetisch;
kein echter Android-/Robolectric-App-Test wird daraus abgeleitet. Eine manipulierte
Fixture-Dependency wurde von Gradle mit Exit 1 abgelehnt und danach restauriert.
Die lokale file-Maven-Vorprobe hatte unvollständige Descriptorabdeckung und wurde
nicht als vollständiger Nachweis akzeptiert; das HTTP-Fixture lieferte die echte
Cache-/Descriptorprüfung.

Die reale Kotlin-/Modulinventur ist gültig: 15 Development- und 2 Originalklassen
mit statisch 91 beziehungsweise 27 gepflegten Methoden. Diese JVM-Fälle wurden
hier nicht neu ausgeführt. Keine vollständige Android-Pipeline, kein Actions-Lauf
und kein Fire-OS-Nachweis. actionlint war nicht installiert; die vorhandene
PyYAML-Prüfung von Syntax, Events, Pinformen und deaktivierten Uploadpfaden ist
kein Ersatz für einen späteren actionlint-/Actions-Lauf.

Alle Mess-/Prüfprotokolle, Quellen-/APK-/Schlüsselintegrität und der vollständige
separate M1.4.5-Patch werden außerhalb des Repositorys unter work/m1-4-5 gesichert.
Die nächste einzelne empfohlene Aktion ist ein unabhängiger lesender Audit dieses
lokalen Patches, bevor irgendein Aktivierungs- oder Veröffentlichungsauftrag folgt.

## 14. M1.4.5.1 – beide Referenzmessungen vollständig binden

Dieser Abschnitt verschärft die Referenzaufnahme aus M1.4.5. Die ursprünglichen
28 Vertragsfälle bleiben erhalten; zwölf zusätzliche Regressionen erhöhen die
gepflegte Sollzahl auf **40 Vertragstests**. Beide produktiven Aufrufer verlangen
40, nicht die aktuelle Discoverygröße. Die übrigen Sollzahlen bleiben
56/19/60/37/11; die vollständige zukünftige Python-Sollsumme ist **223**.
Die historischen Angaben in Abschnitt 13 dokumentieren den damaligen Lauf.
Keine Skip-/Vollständigkeitskontrolle wird reduziert.

### Zusätzliche zwingende Bundle-Dateien

Neben den bisherigen zehn Dateien und dem sie bindenden Referenzmanifest sind
jetzt genau diese sechs Dateien erforderlich:

- `capture-1-profile.json`, `capture-1-evidence.json`
- `capture-2-profile.json`, `capture-2-evidence.json`
- `capture-1-artifact.zip`, `capture-2-artifact.zip`

Das Manifest bindet weiterhin Pfad, Modus, Länge und SHA-256 jeder Datei. Die
vier JSON-Dateien sind die **unveränderten jeweiligen** `candidate-profile.json`
und `build-evidence.json` aus den beiden erfolgreichen Capturejobs. Beide
measurement-Dateien bleiben ebenfalls unveränderte Jobausgaben. `profile.json`
und `build-evidence.json` müssen byteidentische Aliase der erst anschließend
gewählten Messung sein. Fehlende Belege sind Fehler; ein altes Bundle mit nur
einem vollständigen Profil/Evidence-Paar ist unzulässig. Es wurde keine echte
Referenz erzeugt, übernommen oder freigegeben.

### Plattformbindung der konkreten Bytes

Ein erfolgreicher GitHub-Jobname bestätigt noch keine konkrete JSON-Datei.
Deshalb verlangt der Prüfer zusätzlich die beiden **unverändert von GitHub
bezogenen Capture-Artefaktarchive**. Lokal neu gepackte ZIPs sind keine Ersatz-
nachweise. Das unveränderte Aufnahmeworkflow benennt seine bestehenden Artefakte
bereits eindeutig:

`size-reference-evidence-<run_id>-<run_attempt>-1` beziehungsweise `-2`.

Über den ausschließlich lesenden API-Pfad
`/actions/runs/<run_id>/artifacts?per_page=100` müssen genau die passenden,
nicht abgelaufenen Artefakte auffindbar sein. Unvollständige/paginierte Ergebnisse,
fehlende Digests, Mehrdeutigkeiten oder nicht verfügbare Metadaten blockieren.
Ihre von GitHub gelieferten `digest`-Werte müssen `sha256:<ZIP-Hash>` entsprechen,
`size_in_bytes` muss passen, die Artefakt-IDs müssen verschieden sein. Die
`workflow_run`-Daten binden Fork-ID, Head-Repository, Run-ID, master und Workflow-
commit; Name und tatsächliches Erstellungsfenster ordnen den Attempt und den
jeweiligen erfolgreichen `capture-1`-/`capture-2`-Job zu. Die Jobnamen-/Namensregel
ist nur zusammen mit dem unverändert reviewten Inhaberworkflow vertrauenswürdig.
Kein PR-generierter gleichnamiger Bericht ersetzt diese Prüfung.

Die ZIPs werden nicht extrahiert. Archivgröße, Dateianzahl, entpackte Gesamtgröße,
Pfadsicherheit, Dateitypen, Verschlüsselung, doppelte Einträge und CRC werden
kontrolliert. Die drei Dateien `candidate-profile.json`, `build-evidence.json`
und `measurement.json` müssen exakt den jeweiligen Bundle-Dateien entsprechen.
Hinzu kommen ein passendes `measurement_only`-Summary mit eigenem vor dem Build
gebundenem Signiererpin sowie alle sechs strengen Python-Ergebnisdateien.
Die tatsächlichen Ergebnisbytes müssen auch zu den gehashten Pythonquittungen
passen. Evidence und Archive müssen zeitlich zum tatsächlichen Job passen.
Keine APK oder privater Schlüssel wird dafür benötigt oder in das Bundle gelegt.

Es werden keine Artefakte automatisch heruntergeladen, neu gepackt, gespeichert
oder veröffentlicht; der Prüfer liest lediglich die gepinnten Bundle-Blobs und
GitHub-Metadaten. Ein späterer ausdrücklich genehmigter Inhaberablauf muss die
unveränderten Originalarchive bereitstellen und prüfen. Der Workflow und seine
bestehende Upload-Whitelist bleiben in dieser Korrektur unverändert und gesperrt.

### Prüfung beider Messungen vor Auswahl

Für **jede** Messung kontrolliert der Prüfer separat:

1. Tatsächliche Profil- und Evidencebytes gegen beide Messdigests; zusätzlich
   den Profilhash innerhalb der Evidence. APK-Hash und Rohgröße müssen in
   Messung, Profil, Evidence und Job-Summary konsistent sein.
2. Exakte Source-/Rezept-/Toolchain-/Dependencyklasse, Profil-Claims,
   Developmentidentität/-Flags, ABIs und gültigen VersionCode im Buildfenster.
3. Vollständige Pflichtquittungen mit eigener Job-/Run-/Attemptzuordnung;
   beide Jobs müssen tatsächlich erfolgreich abgeschlossen sein.
4. Eigenen öffentlichen DER-Inhalt, dessen tatsächlichen Zertifikatstyp über
   Keytool, eigenständigen erwarteten Pin aus dem authentifizierten Job-Summary,
   beobachteten Pin im APK-Profil, Debugprofil, Signaturschemata und Bereinigung.
   Beide Captures müssen unterschiedliche Pins besitzen. Gleichartige Profile
   rechtfertigen keine Gleichsetzung der Pins. Test-DERs und simulierte API-
   Antworten bleiben ausdrücklich lokale Fixtures, keine Plattformnachweise.

Erst wenn **beide** vollständigen Captures diese Kontrollen erfüllen, wird
`larger_of_two_measurements_tie_first` angewandt. Die gewählten Aliasdateien und
Referenzfelder müssen genau zur richtigen Messung gehören. Bloßes Neuhashen
widersprüchlicher Belege legitimiert keine Referenz.

### Grenzen und fortbestehende Aktivierungssperre

Die Verbindung zu GitHub ist jetzt als konkrete Digest-/Archivprüfung implementiert.
Sie wurde lokal ausschließlich mit simulierten API-Antworten und synthetischen
ZIPs geprüft. Es gab keinen echten Artefaktabruf, Actions-Lauf oder neuen
Capture-Build. Insbesondere sind Verfügbarkeit/Feldbelegung des API-Digests,
Bytegleichheit des tatsächlich bereitgestellten Downloadarchivs, genaue
Zeitmetadaten und das Ablaufverhalten noch nicht an einer echten Aufnahme
bestätigt. Fehlen diese Nachweise, gibt es keinen Metadaten-only-Fallback und
keine Freigabe. Die vorhandene 14-Tage-Aufbewahrung kann durch Ablauf der
Artefaktdaten zu einer späteren Ablehnung führen; keine automatische Erneuerung
oder Baselineerhöhung kompensiert das.

Dieser Mechanismus authentifiziert unter den bisherigen Betreiberannahmen
konkrete gespeicherte Berichtsbytes, keine Hardware-/OIDC-Attestation und keine
neue kryptografische APK-Prüfung ohne APK. Die ursprünglichen echten SDK-/M0-
Prüfungen bleiben Pflicht jedes späteren Capturejobs. Ein kompromittierter
Inhaber, freigegebener bösartiger Workflow/Enginecode oder kompromittierter
Runner wird weiterhin nicht durch JSON-Hashes ausgeschlossen.

Alle vorhandenen `false`-Sperren bleiben unverändert: Referenzjob, Vertragsjob,
APK-Upload, Produktions-/Tag-Schutz sowie das Release-Limit 3.563.000 Bytes.
Kein aktiver Root und keine konkrete Referenz wurden eingerichtet. Die nächste
**einzelne** empfohlene Aktion ist ein unabhängiger lesender Audit ausschließlich
dieser M1.4.5.1-Korrektur samt der lokalen Herkunftssimulationen; keine Aktivierung.

## 15. M1.5.1 – AGP-Projektartefakte vor Materialisierung filtern

B1 aus dem echten M1.5-Android-Lauf ist lokal korrigiert. Der globale
ResolutionListener verwendet jetzt einen `artifactView` mit
`componentFilter { it is ModuleComponentIdentifier }`, **bevor** er Artefaktdateien
anfordert. Die bisherige untypisierte Gesamtsammlung wird nicht mehr abgefragt.
Die Auswahl betrifft Artefakte; Projektabhängigkeiten bleiben im aufgelösten
Graphen enthalten. Der bestehende Collector läuft weiterhin über alle Projekte.
Es gibt keinen Modulskip, keine leniente Auflösung und keine neue Fehlerausnahme.

Plugin-/Buildscript-Erfassung vor Settings-/Projektauflösung, Rollenzuordnung,
rohe Maven-Bytes einschließlich POM/module-Deskriptoren, SHA-256-Zuordnung und
Ablehnung fehlender, mehrdeutiger oder nicht zuordenbarer Inputs bleiben erhalten.
Datenformat, Root-/Herkunftsvertrag und Freigabegrenzen sind unverändert.

### Dauerhafte Regression und echte Integration

Der bestehende Vertragsfall 09 enthält zusätzlich einen benannten B1-Subtest.
Er verhindert den untypisierten Listenerzugriff, verlangt die externe Filterung
vor Materialisierung und erhält die strikte/native Writer-Trennung sowie die
vollständige Projektiteration. Der Subtest scheitert gegen den alten Code und
besteht gegen die Korrektur. Alle bisherigen Methoden/Assertions bleiben erhalten;
**40 unittest-Fälle**, keine künstliche Zählererhöhung durch Subtests. Die strengen
Aufrufer und die bestehende CI-Sollzahl 56 werden nicht geändert.

Zusätzlich wurde das vollständige tatsächliche Android-Mehrmodulprojekt in einer
separaten Quellkopie mit Gradle 9.8.0 / AGP 9.4.1 ausgeführt. Die Tasks
`:app:writeDevelopmentSizeInputs` und `:app:materializeSizeGateTestSdk` bestanden
mit Exit 0 unter `--dependency-verification=strict --offline`, ohne gleichzeitigen
Metadatenwriter. Der Projektgraph und die App-/Modulabhängigkeiten wurden nicht
vereinfacht. Insbesondere blieb `:addon-api` als Projektabhängigkeit von
`:addon-sample:debugCompileClasspath` nachweisbar; auch App- und Browser-
Konfigurationen wurden erfolgreich aufgelöst.

Erfasst und vom unveränderten Python-Byte-/XML-Prüfer akzeptiert: **861 Dateien**
(191 JAR, 110 AAR, 246 POM, 314 module-Dateien), alle sechs Rollen. Die tatsächliche
Robolectric-SDK-Datei wurde materialisiert und ihre Kopie gegen die erfassten
Bytes geprüft. Fehlende Rollen/Dateibindungen/Einträge, unbekannte Rollen,
veränderte Hashangaben und fehlende strikte Verifikation wurden in gezielten
Validierungsproben abgelehnt. Keine echte Dependencydatei wurde dafür verändert.

### Verifikationsaufnahme bleibt eine ungeprüfte Diagnose

Die Online-Aufnahme einer neuen Metadatenliste war in der lokalen Umgebung durch
403 beim Robolectric-Maven-Mirror und 503 bei Google-Maven-Downloads beeinträchtigt.
Daher wurde für den strikten Integrationsnachweis eine **ausschließlich temporäre
Fixture-Liste aus den vorhandenen M1.5-Diagnosedaten** verwendet. Vor Übernahme in
einen eigenen Testcache wurden Dateien und Metadaten auf konkrete Komponenten,
Länge/Hashes und fehlende trust-/ignore-Ausnahmen kontrolliert. Native Offline-
Verifikation muss bei jeder fehlenden tatsächlichen Anforderung scheitern.
Diese Cache-Vorbereitung verändert weder den aufgelösten Projektgraphen noch
App-Abhängigkeiten. Sie ist kein Ersatz für die spätere Inhaberprüfung der Liste.

Ein erster Versuch mit einem vollständigen kopierten alten Messcache wurde wegen
eines nicht zugeordneten Okio-Artefakts korrekt abgelehnt. Die Metadatenaufnahme
hatte zusätzliche baselineprofile-Release-Konfigurationen erfasst; solche Cache-
Inhalte werden nicht automatisch als gültige Debug-Inputs anerkannt. Die
Ablehnung unklassifizierter Dateien wurde nicht gelockert. Produktions-/Referenz-
läufe behalten ihre vorhandenen Regeln für frische Source-/Cacheverzeichnisse.

Separat bestand eine kontrollierte synthetische Native-Verifikationsprobe mit
Settings-/Projekt-Plugin und allen sechs Rollen. Ein verändertes **synthetisches**
JAR im eigenen Fixture-Cache wurde von Gradle abgelehnt und danach restauriert;
echte Projektdateien und globale Caches wurden nicht manipuliert. Eine explizite
Kombination von `--dependency-verification=strict` und
`--write-verification-metadata sha256` lieferte weiterhin `strict_verification=false`
und wurde vom Python-Prüfer abgelehnt. Metadatenaufnahme wird somit nicht als
strenger Verifikationsnachweis anerkannt.

### Geltungsgrenze und nächster Schritt

Neu ausgeführt: die 40 Vertragstests einschließlich B1-Subtest, die 56 CI-
Selbsttests, echte AGP-Collector-Integration und gezielte Negativproben.
Die vollständigen 223 Python-/118 JVM-Fälle, App-Build, App-Lint und APK-Prüfung
bleiben historische M1.5-Nachweise und wurden hier nicht erneut behauptet.

Weder die Fixture-Liste noch die lokale Auflösung sind eine Inhaberfreigabe,
Baseline oder echte GitHub-Capture-/Artefaktherkunft. Kein vollständiger Actions-
oder Fire-OS-Nachweis. Betreiberroot, Referenz-/Contract-Jobs, APK-Upload und
Produktions-/Tagveröffentlichung bleiben unverändert gesperrt. Das Releasebudget
bleibt 3.563.000 Bytes. Es wurden keine APKs gebaut oder installiert, keine
Signierungs-/Appdateien verändert und keine GitHub- oder Git-Schreibaktionen
vorgenommen. Exporte enthalten keine APKs, privaten Schlüssel oder Keystores.

Genau eine nächste empfohlene Aktion: den vollständigen lokalen M1–M1.5.1-Stand
nach gesonderter Freigabe auf einem eigenen GitHub-Review-Branch sichern; dabei
Workflowstarts vor dem Push ausdrücklich kontrollieren. Keine automatische
Fortsetzung oder Aktivierung.
