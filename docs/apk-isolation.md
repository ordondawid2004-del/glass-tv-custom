# APK-Isolationsprüfung (M0)

`scripts/check-apk-isolation.py` prüft eine **vorhandene eigenständige APK** nur
lesend. Es baut, signiert oder installiert nichts, verwendet kein ADB und ändert
weder APKs noch Geräte. Es ist bewusst noch nicht in Build-, Installations- oder
Release-Skripte eingebunden.

## Voraussetzungen

- Python 3.10 oder neuer; keine zusätzlichen Python-Pakete.
- Android SDK Command-line Tools mit `apkanalyzer` sowie stabile Build Tools mit
  `apksigner` und eine dazu passende Java-Laufzeit.
- SDK-Pfad über `--sdk`, ansonsten `ANDROID_HOME` oder `ANDROID_SDK_ROOT`.
- Unterstützte Ausführungsumgebung: Linux/macOS, einschließlich Codex Cloud.
  Unter Windows kann eine entsprechend eingerichtete WSL-Umgebung verwendet werden;
  native Windows-`.bat`-Werkzeuge werden derzeit nicht unterstützt.

In der eingerichteten Cloud kann zuvor `source /workspace/setup/glass-env.sh`
verwendet werden. Diese Umgebungsdatei ist nicht Bestandteil dieses Repositories.

## Beispiel: vorhandene Original-APK prüfen

Vom Repository-Hauptverzeichnis:

```bash
python3 scripts/check-apk-isolation.py \
  app/build/outputs/apk/release/app-release.apk \
  --expected-id io.github.ordondawid2004del.tvhome \
  --mode development
```

Die erwartete ID ist hier ein **Beispiel für die spätere eigene App**, keine
bereits getroffene Produktentscheidung. Der aktuelle Build mit `dev.glasslauncher`
muss mit Exit **1** abgelehnt werden, einschließlich seiner übernommenen eigenen
Berechtigungsdeklarationen. `--expected-id dev.glasslauncher` ist unzulässig.
Es gibt keinen Schalter zum Übergehen dieser Originalpaket-Sperre.

## Entwicklung und Produktion

`--mode development` erlaubt auch Debug-Zertifikate und ein `debuggable`- oder
`testOnly`-Manifest, verlangt aber die richtige eigene ID und kollisionsfreie
Deklarationen. Der Bericht kennzeichnet ausdrücklich **KEINE Produktionsfreigabe**.
Optional kann auch hier das erwartete Zertifikat festgelegt werden.

Ohne `--mode` gilt der strengere Modus **production**. Beispiel nach M1:

```bash
python3 scripts/check-apk-isolation.py path/to/own-release.apk \
  --sdk /path/to/android-sdk \
  --expected-id io.github.ordondawid2004del.tvhome \
  --expected-cert-sha256 "$EXPECTED_RELEASE_CERT_SHA256"
```

`EXPECTED_RELEASE_CERT_SHA256` muss aus einem unabhängig vertrauenswürdigen
Nachweis des eigenen Release-Zertifikats stammen. Nicht blind den Fingerabdruck
aus derselben unbekannten APK übernehmen. Ein Zertifikatsfingerabdruck ist
öffentlich; private Schlüssel oder Keystore-Passwörter werden nicht benötigt.
64 Hexzeichen mit oder ohne Doppelpunkte werden akzeptiert.

Produktion verlangt:

1. Gültige APK-Signatur, tatsächlich durch `apksigner verify` bestätigt.
2. Exakte Übereinstimmung mit der ausdrücklich erwarteten Application ID.
3. Alle selbst deklarierten Berechtigungen innerhalb von `<expected-id>.`.
   Die Namensräume `dev.glasslauncher.`, `com.glasslauncher.` und `com.phairplay.`
   bleiben wegen Original- und Begleitapps gesperrt, auch einschließlich der
   von AndroidX erzeugten `DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION`.
4. Exakte Übereinstimmung des aktuellen Signierersatzes mit den erwarteten
   SHA-256-Zertifikatsfingerabdrücken. Bei mehreren Signierern den Parameter
   wiederholen. Schlüsselrotation wird nicht automatisch akzeptiert.
5. Kein Standard-Android-Debug-Zertifikat (`CN=Android Debug`) und weder
   `android:debuggable="true"` noch `android:testOnly="true"`.

Ein anderer Zertifikatsname beweist keinen Produktionsschlüssel: benutzerdefinierte
Entwicklungsschlüssel sind nicht allgemein automatisch erkennbar. Deshalb ist die
unabhängig festgelegte Zertifikatsbindung im Produktionsmodus zwingend.

## Bericht und Exit-Codes

Der deutsche Textbericht enthält App-ID, Versionsname, vollständigen Versionscode
(einschließlich `versionCodeMajor`, wenn vorhanden), APK-SHA-256, Manifest-Flags,
Signierer mit Zertifikatsfingerabdrücken und getrennte Berechtigungslisten.
Der APK-Hash wird vor und nach den SDK-Prüfungen verglichen.

| Exit | Bedeutung |
|---|---|
| `0` | Alle Identitätsregeln des **gewählten Modus** erfüllt |
| `1` | APK zuverlässig analysiert, aber mindestens eine Schutzregel verletzt |
| `2` | Prüfung nicht zuverlässig möglich oder Aufruf ungültig: Datei/Werkzeug fehlt, ungültige oder unsignierte APK, fehlgeschlagene Signaturprüfung, Timeout, unlesbare/mehrdeutige Daten |

`<permission>` definiert einen eigenen Berechtigungsnamen und kann mit einer
bereits installierten App kollidieren. `<uses-permission>` und dessen SDK-Varianten
fordern dagegen nur einen vorhandenen Namen an. Angeforderte Fremdberechtigungen
werden aufgelistet, aber nicht als eigene Deklaration abgelehnt. Das ist **keine**
inhaltliche Freigabe ihrer Verwendung.

## Automatisierte Tests

Schnelle Parser-, Regel- und Fehlerpfadtests ohne SDK:

```bash
python3 -B -m unittest discover -s scripts/tests -p 'test_apk_isolation.py' -v
```

Die zusätzlichen Integrationstests erzeugen ausschließlich temporäre minimale
Test-APKs und kurzlebige Testschlüssel. Sie brauchen auch `aapt2`, eine installierte
SDK-Plattform (`android.jar`) und `keytool`. Sie verändern weder Launcher-Code noch
bestehende APKs und installieren nichts. Ohne ausdrückliche Aktivierung werden
diese SDK-Tests als übersprungen gemeldet:

```bash
APK_ISOLATION_INTEGRATION=1 python3 -B -m unittest discover \
  -s scripts/tests -p 'test_apk_isolation.py' -v
```

Abgedeckt sind zulässige eigene Identitäten, Original-/Fremdpakete,
Berechtigungskollisionen versus Anforderungen, Debug-/Produktionsunterscheidung,
Zertifikatsbindung, ungültige/fehlende/unsignierte/manipulierte Dateien,
SDK-Ausfälle und unvollständige Analysedaten. Testschlüssel sind ausschließlich
wegwerfbare Fixtures und niemals für Produktbuilds zu verwenden.

## Tatsächlich ausgeführte Validierung

Am 7. November 2026 auf Ausgangscommit `69653ca`, in der vorhandenen Codex-Cloud:

- **37 Tests bestanden:** 25 Parser-/Regel-/Fehlerpfadtests und 12 SDK-Integrationstests;
  Abschlusslauf ohne Fehler und ohne übersprungene Tests.
- Die Integrationstests verwendeten echte temporäre APKs, `aapt2`, `apkanalyzer`,
  `apksigner` und `keytool`. Zulässige V2-/V3-signierte Test-APKs wurden angenommen;
  unzulässige, unsignierte und manipulierte Fixtures erwartungsgemäß abgelehnt.
- Die vorhandene `app/build/outputs/apk/release/app-release.apk` wurde in Entwicklung
  und Produktion mit **Exit 1** abgelehnt: Original-ID und vier kollidierende
  Deklarationen; Produktion erkannte zusätzlich das Debug-Zertifikat, selbst bei
  ausdrücklich passendem Zertifikatsfingerabdruck. Der APK-Dateihash blieb unverändert.
- Erste Läufe fanden Parserlücken für die schemenbezogenen Signiererbezeichnungen
  der zusätzlich vorhandenen Build Tools 37. Diese wurden korrigiert und durch
  Regressionstests sowie die erneute Original-APK-Prüfung abgesichert.
- Launcher-Code, Build-Konfiguration und bestehende APK wurden nicht verändert.
  **Kein erneuter Launcher-Build, keine Launcher-JVM-/Lint-/Gerätetests.** Die minimalen
  Fixture-Builds sind kein Nachweis eines erfolgreichen Launcher-Builds.

## Grenzen und nächster Einsatz

Dies ist eine konservative **Identitätsprüfung**, kein allgemeiner Security-Scan,
keine Prüfung aller installierten Apps und keine Installationsfreigabe. Unbekannte
deklarierte Fremdnamensräume werden vorsorglich abgelehnt. App-Verhalten,
Home-Übernahme, Provider-Authorities, Updater-Ziele, Datenschutz, Funktionsfähigkeit
und weitere Manifest-Fähigkeiten benötigen in M1 separate Prüfungen.

Split-APKs/App-Bundles, `permission-tree` und nicht auflösbare Manifest-Werte werden
nicht akzeptiert. Mehrere nummerierte Signierer und die einzelnen V1-/V2-/V3.0-Signiererformate
werden unterstützt; komplexe plattformabhängige Rotationsformate bleiben abgelehnt.
Unbekannte SDK-Ausgabeformate führen zu Exit 2 statt zu einer stillen Freigabe.
Ein Exit 0 für Entwicklung darf nicht als Veröffentlichungskriterium verwendet
werden. In einer späteren Release-Pipeline ist ausdrücklich der Produktionsmodus
mit vertrauenswürdig hinterlegtem Release-Fingerabdruck aufzurufen.
