# M1: isolierte Entwicklungsvariante

M1 ist eine **manuell startbare Entwicklungs-App**, keine Produktionsfreigabe und kein Ersatz der eingerichteten Home-App. Fire OS 8 wurde hier nicht auf einem Gerät geprüft.

## Ausgangspunkt

Lokaler Branch `feature/m1-isolated-development`, Basis `b5ef4d4ac0cfc23d19348c5556193629c49f971c` (M0). Bei Beginn stand der eigene `master` noch auf `69653ca7517517254e1db7dfc75acf27ebeddfa6`; M0 war nicht übernommen. M1 hängt deshalb von [M0, Draft-PR #1 im eigenen Fork](https://github.com/ordondawid2004-del/glass-tv-custom/pull/1) ab. M1 wurde weder committed noch gepusht.

| Merkmal | Entwicklungsvariante |
| --- | --- |
| Gradle-Variante | `developmentDebug` |
| Application ID | `io.github.ordondawid2004del.tvhome.dev` |
| Anzeigename | `TV Home – Entwicklung` |
| Kotlin-/Java-Namespace | `dev.glasslauncher` |
| Einstiegsklasse | `dev.glasslauncher.MainActivity` |
| Version | `0.3.0-m1-dev`; versionCode weiterhin Minuten seit 2026-01-01 |
| Später reserviert | `io.github.ordondawid2004del.tvhome` – noch keine Produktionsvariante |

`originalDebug` behält die ursprüngliche Identität. Es ist kein Artefakt für unseren Paralleltest. Die neuen Varianten schreiben in eigene Unterordner und überschreiben das vorhandene `app/build/outputs/apk/release/app-release.apk` nicht. Kein `clean` ausführen, wenn dieses bisherige Build-Artefakt erhalten bleiben soll.

## Schutzgrenzen

- Ein unveränderliches `BuildConfig.ISOLATED_DEVELOPMENT` steuert die Grenzen, unabhängig von Preferences, Berechtigungsgrants und importierten Einstellungen. Start/Resume überspringen die historische Systeminitialisierung. Zusätzlich verweigern Root-Probe und Prozessgrenze, Secure-Settings-Schreiber, Home-Rollenanforderung, App-Verwaltung, Installer und andere Systemaktionen die Ausführung.
- Nur MAIN mit LAUNCHER und LEANBACK_LAUNCHER; weder HOME noch Fire-OS-HOME. Keine Accessibility-, Notification-Listener-, Download-, Capture- oder Dream-Dienste und keine App-Receiver. Auch die exportierten historischen Debug-Komponenten und deren feste Authority sind entfernt. Zurück auf der obersten Startseite beendet die Entwicklungs-Activity.
- Die verbleibenden Anforderungen sind INTERNET, ACCESS_NETWORK_STATE und die eigene AndroidX-Berechtigung `io.github.ordondawid2004del.tvhome.dev.DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION`. Diese wird als `signature` deklariert. Die einzige Authority gehört zum nicht exportierten AndroidX-Startup-Provider; kein `sharedUserId`.
- Der Selbst-Updater beendet Release-Abfragen vor HTTP und weist Downloads/Installationen vor Datei- oder PackageInstaller-Zugriff zurück. `UPDATE_REPO` ist zusätzlich leer. Kein Upstream-Fallback.
- Browser-, Add-on- und PhairPlay-Protokolle bleiben im gemeinsamen Originalcode erhalten, sind in M1 aber an Discovery-/Transportgrenzen gesperrt. Sonos-Capture, Bluetooth-Pairing, Fernbedienungsdienste und die Telefoneingabe sind ebenfalls gesperrt.
- Die Oberfläche nutzt weiterhin Home, App-Kacheln, Ordner und App-Start. Das Entwicklungsmenü bietet ausschließlich eigene Darstellungseinstellungen. Gesperrte Integrations-, System-, Update-, Backup-, Installations- und Deinstallationsbedienelemente werden nicht angeboten. Die allgemeine deutsche Lokalisierung ist ein späterer Meilenstein.
- DataStore, Preferences, Bild-/Feed-Caches und mögliche Downloadverzeichnisse liegen in der eigenen Sandbox. Backup-Import/-Export und Konfigurationsimport sind gesperrt; Bilddateipfade werden auf eigenen privaten Speicher begrenzt. Es gibt keine automatische Migration aus Glass. Film-/Bildquellen können weiterhin normale Netzwerkzugriffe auslösen; M1 ist keine Offline-Variante.
- Application ID und Klassenname sind ausdrücklich getrennt. Komponenten verwenden `ComponentName(context, MainActivity::class.java)` beziehungsweise `packageName/dev.glasslauncher.MainActivity`. Die alte Kurzform `packageName/.MainActivity` ist korrigiert.

## Entwicklungsschlüssel und Build

Voraussetzungen: Linux, Java 21, Android SDK mit Plattform 37 und Build Tools, die Projektabhängigkeiten. Diese Cloud nutzt die vorhandene `/workspace/setup/glass-env.sh`; sie gehört nicht zum Git-Repository. Auf anderen Rechnern JAVA_HOME und ANDROID_HOME passend setzen.

M1 verwendet einen **eigenen Android-Debug-Schlüssel**, nicht den alten Glass-Debug-Schlüssel und keinen M0-Fixture-Schlüssel. Die Datei liegt außerhalb von Git. Der Standardpfad ist `$ANDROID_USER_HOME/tvhome-development.keystore`, sonst `~/.android/tvhome-development.keystore`. Alternativ den absoluten Pfad mit `-PtvhomeDevKeystore=…` angeben. Der Build erzeugt keinen Schlüssel automatisch.

Einmalig, nur wenn noch kein solcher Entwicklungsschlüssel existiert:

```bash
test ! -e "$ANDROID_USER_HOME/tvhome-development.keystore" && \
keytool -genkeypair \
  -keystore "$ANDROID_USER_HOME/tvhome-development.keystore" \
  -storepass android -keypass android -alias androiddebugkey \
  -keyalg RSA -keysize 2048 -validity 10950 \
  -dname 'CN=Android Debug,O=TV Home Development,C=DE' -noprompt
chmod 600 "$ANDROID_USER_HOME/tvhome-development.keystore"
```

`android` ist hier das bewusst öffentliche Debug-Kennwort, kein Produktionsgeheimnis. Den Schlüssel privat außerhalb des Repositories behalten: Ein anderer Schlüssel verhindert spätere Updates derselben Entwicklungsinstallation. Ein neu erzeugter Schlüssel hat einen anderen Fingerabdruck. Niemals private Schlüssel oder Keystore-Dateien in einen Diff aufnehmen.

Der hier erzeugte Schlüssel liegt nur in der Cloud-Umgebung und ist nicht Teil des Review-Archivs. Für spätere Entwicklungsupdates muss derselbe Schlüssel separat sicher erhalten bleiben; seine dauerhafte externe Sicherung ist kein Bestandteil dieses Auftrags.

```bash
./gradlew :app:assembleDevelopmentDebug --console=plain
```

Ergebnis: `app/build/outputs/apk/development/debug/app-development-debug.apk`. Vor Weitergabe eine Kopie in einen getrennten Artefaktordner legen. Nicht mit `scripts/build`, Geräte-, Installations- oder Baseline-Profile-Skripten arbeiten; diese sind für M1 nicht freigegeben.

Die Release-Varianten sind ohne vollständige externe `RELEASE_KEYSTORE`-/Passwort-/Alias-Konfiguration deaktiviert. Die Entwicklungs-Release-Variante bleibt auch mit solchen Angaben deaktiviert. So können weder Original- noch erzeugte Benchmark-Release-Varianten auf einen Debug-Schlüssel zurückfallen. Kein Produktionsschlüssel wurde erstellt.

## Gerätefreie Prüfungen

Die folgenden Befehle nutzen SDK-Werkzeuge und JVM/Robolectric, keine Geräte oder Emulatoren:

```bash
APK_ISOLATION_INTEGRATION=1 PYTHONDONTWRITEBYTECODE=1 \
  python3 -B -m unittest discover -s scripts/tests -p 'test_apk_isolation.py' -v
python3 -B -m unittest discover -s scripts/tests -p 'test_development_manifest.py' -v
./gradlew :app:testDevelopmentDebugUnitTest \
  --tests '*DevelopmentIsolationTest' --tests '*DevelopmentUiTest' \
  :app:lintDevelopmentDebug --console=plain
git diff --check
```

Die M1-Tests beobachten Prozessversuche über einen vor `Application.onCreate` gesetzten Fake, tatsächliche Robolectric-Settings, gestartete Activities/Dienste, Broadcasts, Installer-Sessions und HTTP-/Add-on-Aufrufe. Sie aktivieren problematische Konfigurationswerte ausdrücklich. Sie prüfen außerdem die auflösbare Klasse, das zusammengeführte Manifest und die Compose-Einstellungen mit simulierten TV-Apps. Die Original-Testfälle für gewollte Root-/Installer-Funktionen müssen gegen `originalDebug` laufen, nicht gegen die absichtlich eingeschränkte Entwicklungsversion.

Zusätzlich das **fertige APK** prüfen. Den erwarteten Zertifikatsfingerabdruck unabhängig aus dem vorgesehenen Entwicklungsschlüssel übernehmen und mit der tatsächlichen Signatur vergleichen:

```bash
APK=app/build/outputs/apk/development/debug/app-development-debug.apk
CERT=6c3ac4ffc476b2749fcb135f5965c4ca03a3f1dc7ecda4e66c3a565719edcbdc
python3 -B scripts/check-apk-isolation.py "$APK" \
  --expected-id io.github.ordondawid2004del.tvhome.dev \
  --mode development --expected-cert-sha256 "$CERT"
```

Erwartung: Exit 0, ausdrücklich keine Produktionsfreigabe. Mit `--mode production` muss dieses APK wegen Debug-Zertifikat und Debug-Flag Exit 1 liefern. Der oben angegebene Pin gehört ausschließlich zum in dieser Cloud erzeugten M1-Entwicklungsschlüssel.

```bash
mkdir -p work/m1-artifacts
apkanalyzer manifest print "$APK" > work/m1-artifacts/manifest.xml
python3 -B scripts/check-development-manifest.py work/m1-artifacts/manifest.xml
sha256sum "$APK"
```

Der zusätzliche Manifestprüfer kontrolliert eine enge Allowlist für Komponenten, Authorities, deklarierte und angeforderte Berechtigungen sowie Startfilter. Exit 0 = M1-Manifest entspricht der Allowlist, 1 = abgelehnt, 2 = Eingabe nicht prüfbar. Er prüft keine APK-Signatur; dafür ist M0 erforderlich. Die Klassenauflösung wird zusätzlich durch die JVM-Tests geprüft. Der konkrete Prüfbericht zur gelieferten APK enthält die tatsächlich gelaufenen Tests, Fehlerkorrekturen und Hashes.

## Späterer manueller Paralleltest – hier nicht ausgeführt

1. Erst den lokalen Diff und die beiden APK-Prüfberichte prüfen. Fingerabdruck und APK-SHA-256 mit dem gelieferten Bericht vergleichen. Ausschließlich die Entwicklungs-APK mit der oben genannten ID auswählen.
2. Vor einem separat freizugebenden Gerätetest den funktionierenden Zustand von Glass, Home on Fire und Amazon festhalten. Keine Home-Zuordnung, Dienste, Sonderberechtigungen oder Geräteparameter ändern.
3. Die Entwicklungs-App nach gesonderter Freigabe über den gewählten manuellen Installationsweg hinzufügen. Beide Apps müssen getrennt in der App-Verwaltung sichtbar sein. Die bisherige Home-Taste und der automatische Start müssen unverändert funktionieren.
4. „TV Home – Entwicklung“ manuell öffnen. D-Pad, Select, Zurück, App-Start und Rückkehr testen; lokale Layout-/Darstellungsänderungen dürfen Glass nicht beeinflussen. Kaltstart und Wiederaufnahme prüfen. Es dürfen keine Root-, Home-, Accessibility- oder Updateaufforderungen erscheinen.
5. Netflix, Disney+, Prime Video, YouTube und Kodi einzeln öffnen, soweit installiert. Diese Tests bestätigen nur den jeweiligen Gerätetest, nicht pauschale Kompatibilität.
6. Für das Entfernen ausschließlich **TV Home – Entwicklung**, Paket `io.github.ordondawid2004del.tvhome.dev`, in der System-App-Verwaltung auswählen. Keine andere App deinstallieren und keine Daten von Glass, Home on Fire oder Amazon löschen. Anschließend deren bisherige Funktion erneut kontrollieren.

Offen bleiben Fire-OS-Verhalten, reale Fernbedienungs-/App-Kompatibilität, Leistung, Darstellung und Speicherverbrauch auf dem Stick. Debug-Builds sind nicht für Leistungs- oder Produktionsfreigaben geeignet. Der erhaltene Originalcode enthält weiterhin seine historischen Systemfunktionen; neue Funktionen in M1 müssen die Laufzeit- und Manifestgrenzen mitprüfen.

Bestehende Release-/Geräteskripte und GitHub-Workflows wurden nicht auf die neuen Variantennamen und Ausgabepfade umgestellt und nicht ausgeführt. Vor einer späteren Veröffentlichung muss die eigene CI ausdrücklich auf die gewünschte Variante und die beiden APK-Prüfungen ausgerichtet werden.
