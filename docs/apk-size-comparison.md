# M1.4.3 – APK-Größenprofile sicher vergleichen

`compare-apk-size-profile.py` liest zwei ausdrücklich angegebene Profile des
unveränderten M1.4.1-Profilers (`tvhome.apk-size-profile/1`). Benötigt wird nur
Python 3.10+ auf Linux/POSIX, kein SDK, keine APK, kein Schlüssel, kein Netzwerk
und kein zusätzliches Python-Paket. Es wird nichts gebaut oder installiert.

## Aufruf und Rückgabewerte

```bash
python3 -B scripts/compare-apk-size-profile.py \
  --reference /workspace/work/m1-4-2/profile-1.json \
  --candidate /workspace/work/m1-4-2/profile-2.json \
  --reference-sha256 d84cf67276ca268f7c82041d56911c8f76a0e478a17bdd4f5383524d64082745 \
  --output /workspace/work/m1-4-3/NEUER-vergleich.json
```

Der erwartete SHA-256 muss aus einer gesondert geprüften Quelle stammen. Ihn im
selben unbeaufsichtigten Ablauf aus einer beliebigen Referenzdatei zu berechnen
schafft keinen Vertrauensanker. Der obige Hash identifiziert die vorhandene
M1.4.2-Datei; **er genehmigt sie nicht als Baseline**. Pfade sind lokale Beispiele,
keine Garantie der Verfügbarkeit in anderen Arbeitsumgebungen. Der Ausgabeordner
muss existieren, der konkrete Ausgabepfad muss neu sein.

- **Exit 3:** gültiger Analysebericht erstellt, ausdrücklich **kein bestandener
  CI-Gate**. Das gilt auch bei identischen Profilen, vollständigen Claims und
  unveränderter Größe. Deshalb erscheint dieser manuelle Befehl im Terminal als
  nicht erfolgreich; das ist beabsichtigt. Die Vergleichbarkeitsgründe stehen im
  JSON und in der verständlichen Abschlussmeldung.
- **Exit 2:** ungültige/fehlende/widersprüchliche Eingabe, falscher Referenzhash,
  während der Verarbeitung veränderte Eingabe oder nicht sichere Ausgabe.
  Kein neuer gültiger Bericht. Eine bestehende Ausgabe bleibt unverändert und
  darf nicht mit einem Ergebnis des fehlgeschlagenen Aufrufs verwechselt werden.

Für Vergleichsaufrufe gibt es bewusst **keinen Exit 0**. `--help` ist eine reine
Hilfsabfrage. Das Werkzeug schreibt weder `GITHUB_OUTPUT` noch ein `apk_ready`
oder sonst ein Freigabesignal. Nicht mit `|| true` in ein Freigabe-Gate umdeuten.
Eine spätere CI-Anbindung benötigt einen eigenen überprüften Auftrag.

## Validierung und Grenzen des Schemas

Das vorhandene Format hat keine separate JSON-Schema-Datei. Das neue Werkzeug
implementiert die vom tatsächlichen Produzenten ausgegebene, geschlossene
Struktur in `validate`: alle Pflichtfelder und Zustandsvarianten, keine unbekannten
Felder. Eine zukünftige Formaterweiterung muss bewusst geprüft werden.

Geprüft werden insbesondere:

- striktes UTF-8-JSON ohne doppelte Schlüssel, NaN oder Infinity;
- Pflichtfelder, Typen und kleingeschriebene SHA-256-Werte;
- positive APK-Größe, nichtnegative ganzzahlige Größen und Dateizahlen;
  boolesche Werte gelten nicht als Integer; Zahlen maximal `2**63-1`;
- exakte Gruppen-/Container-, Entpackt- und Native-/ABI-Summen; höchstens
  100.000 Dateien und 2 GiB entpackte Daten entsprechend dem Produzenten;
- Gruppen-/ABI-/Containerprozente, entsprechend der Produzentenrundung auf
  acht Nachkommastellen (Abweichung höchstens 0,00000001 Prozentpunkte);
- vollständige sechs Projektkonfigurationshashes einschließlich nachgerechneter
  Gesamtreferenz, sofern Projektbeobachtungen vorhanden sind;
- APK-Hash-/ID-/Versionsbindung der externen Claims;
- eindeutige Signierer und die Übereinstimmung der Debug-Kennzeichnung mit dem
  Android-Debug-DN-Muster des M0-Prüfers; widersprüchliche Metadaten für denselben
  Zertifikat-Pin oder denselben APK-Hash werden abgelehnt;
- die bestehenden unbekannten `comparison_context`-Werte und ausbleibende
  Freigaben. Behauptete neue Vertrauenszustände werden nicht akzeptiert.

Eine Nullreferenz ist keine gültige APK-Größe und wird vor der Division mit
Exit 2 abgelehnt. Für gültige Profile gilt:
`delta_percent_of_reference = 100 * (candidate_bytes - reference_bytes) / reference_bytes`.
Die Prozentzahl ist eine informative Differenz, keine Wachstumstoleranz.

Eingaben müssen reguläre Dateien mit höchstens 2 MiB sein; direkte Symlinks
werden abgelehnt. Dateiidentität und SHA-256 werden vor/nach dem Vergleich und
nach Ausgabe geprüft. Die Ausgabe wird exklusiv mit Modus 0600 erzeugt; Dateien,
Hardlinks oder Symlinks an einem bestehenden Ausgabepfad werden nie überschrieben.
Bei einem nachträglich erkannten Fehler wird nur die von diesem Aufruf erzeugte,
noch anhand ihrer Dateiidentität erkennbare Ausgabe entfernt. Das ist kein
Dateisystem-Locking gegen einen beliebig privilegierten gleichzeitigen Angreifer.

## Vergleichbarkeit ist keine Herkunftsverifizierung

Die Statuswerte im Bericht bedeuten:

| `comparability.status` | Aussage |
| --- | --- |
| `matching_recipe_same_source_claim` | Erforderliche Angaben vollständig; dokumentiertes Rezept, beobachtete Konfiguration, Signierer und eingebettete AGP-Angaben stimmen überein; gleiche behauptete Quellreferenz. |
| `matching_recipe_changed_source_claim` | Gleiche begrenzte Rezeptbewertung, unterschiedliche identifizierte Quellreferenzen; sinnvoller informativer Größenregressionsvergleich. |
| `insufficient_documentation` | Kein festgestellter Rezeptwiderspruch, aber notwendige Angaben fehlen oder sind ausdrücklich unbekannt. |
| `incompatible_documentation` | Konkrete Unterschiede/Identitäts- oder Variantenprobleme verhindern eine ausreichend belegte technische Vergleichbarkeit. |

**Alle vier Zustände bleiben ohne verifizierte Buildherkunft und ohne Freigabe.**
Sie enden bei gültigen Profilen mit Exit 3. Ein Bericht mit Größenwerten ist kein
stillschweigendes Akzeptieren seiner Referenz als vertrauenswürdige Baseline.

Verglichen wird ausschließlich `developmentDebug` mit der isolierten ID
`io.github.ordondawid2004del.tvhome.dev`, `debuggable=true`, `testOnly=false`.
Andere Identitäten oder Varianten werden im Vergleich als ungeeignet ausgewiesen.
Widersprüche innerhalb eines Profils sind dagegen Eingabefehler (Exit 2).

Rezept-, Toolchain- und Packaging-Claims werden als dokumentierte Zeichenketten
verglichen, **nicht ausgeführt und nicht semantisch authentifiziert**. Fehlende
Variante, Quelle, Rezept, Build-Toolchain, Packaging- oder Dependencyreferenz
bleiben ausdrücklich fehlend. Die SDK-Werkzeuge unter `analysis_tools` sind
Analysewerkzeuge und werden niemals als Build-Toolchain eingesetzt.
Abweichende Produzenten-/Inspektorhashes, Konfigurationshashes, Signierer,
ABI-Mengen, Dependencyreferenzen, VersionNames oder eingebettete AGP-Versionen
verlangen eine eigene Vergleichbarkeitsprüfung. Geänderte Dependencyreferenzen
werden samt Größenwerten ausgegeben, aber konservativ als nicht ausreichend
vergleichbar behandelt. Unterschiede reiner Analysepfade begründen keinen
Wechsel der Compiler-Toolchain. Alle Angaben bleiben anhand der gehashten
Eingabeprofile nachprüfbar.

Eine andere Quellreferenz ist ausdrücklich **kein** Ablehnungsgrund. Die Quelle
muss identifiziert sein; sie muss für eine spätere Funktionsregression nicht
identisch bleiben. Gleiche externe Quellenangaben allein beweisen keine
APK-Quellassoziation. Unterschiedliche VersionCodes werden als variable Metadaten
behandelt und sind allein kein Rezeptfehler. Aus aggregierten Größenprofilen
lässt sich ihre konkrete Bytewirkung nicht beweisen; dazu wären die separat
untersuchten APK-Inhalte erforderlich. Ein solcher Effekt wird hier nicht erfunden.

Der SHA-Vertrauensanker bindet nur die tatsächlich gelesenen Referenzprofilbytes.
Der Kandidatenhash wird beobachtet, nicht extern authentifiziert. APK-Signaturen,
Quellstände, Toolchains und Dependency-Artefaktbytes werden **nicht erneut geprüft**.
Auch `apk_observed` ist für diesen Leser zunächst eine Aussage des Eingabeprofils.
Ein Angreifer kann ein intern konsistentes Kandidatenprofil erfinden; dieses
Werkzeug erkennt keine solche Fälschung ohne gesondert vertrauenswürdige Erzeugung.
Die Zustandsausgabe `provenance_verification` hält diese Grenze ausdrücklich fest.

## Berichtsinhalt und M1.4.2-Beispiele

`tvhome.apk-size-comparison/1` enthält Profil-/APK-Hashes, IDs/Variantenclaims,
Gesamtwerte und Byte-/Prozentdifferenz, ZIP-/Entpackt-/Dateizahldeltas jeder Gruppe,
Containerrest, ABI-Veränderungen, VersionCodes, Signierer, Quell- und
Dependencyreferenzen, Konfigurationsänderungen, Vergleichbarkeitsgründe und
Herkunftsgrenzen. Unter `restrictions` bleiben alle Freigaben false und die
Wachstumstoleranz null; kein Budget oder Profil wird geändert.

Die vorhandenen echten M1.4.2-Profile liefern:

| Vergleich | Gesamt | DEX ZIP | Other ZIP | Containerrest | VersionCode |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 → 2 | 0 Bytes | +1 | −1 | 0 | +2 |
| 1 → 3 | 0 Bytes | 0 | −1 | +1 | +3 |

Alle anderen Gruppen-, ABI-, Entpackt- und Dateizahldeltas sind null. Beide
Vergleiche enden als `insufficient_documentation`: In den Eingabeprofilen fehlen
`dependencies_reference`-Claims. Die separate M1.4.2-Zusammenfassung dokumentiert
einen Graphen, ist aber **keine Eingabe** dieses Werkzeugs; er wird nicht automatisch
hineinkopiert oder zur verifizierten Herkunft hochgestuft. Die Originalprofile
werden dafür nicht verändert. Die lokalen Messungen belegen keine GitHub-Actions-
Größe oder Fire-OS-Kompatibilität.

## Tests und unveränderte Sicherheitsregeln

```bash
python3 -B -m unittest discover -s scripts/tests \
  -p test_compare_apk_size_profile.py -v
```

Die eigenständigen Fixtures benötigen kein SDK und behaupten keine realen
APK-Prüfungen. Die verbindliche Testanzahl der bestehenden CI-Selbsttests wird
nicht erweitert. Bestehende M0-/M1-/Manifest-/Signaturprüfer, Workflow, Upload-
Bedingungen, Produktionssperren und das Release-Budget von **3.563.000 Bytes**
bleiben unverändert. Keine automatische Baseline-Aktualisierung oder CI-Regel.
