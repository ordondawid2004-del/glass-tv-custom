#!/usr/bin/env python3
"""Read-only APK identity gate. Uses only the Python standard library and Android SDK."""

import argparse
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile


ORIGINAL_ID = "dev.glasslauncher"
ANDROID = "{http://schemas.android.com/apk/res/android}"
PACKAGE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+")
RESERVED_PERMISSIONS = ("dev.glasslauncher.", "com.glasslauncher.", "com.phairplay.")


class InspectionError(Exception):
    """Missing, invalid or ambiguous evidence: never accept the APK."""


@dataclass(frozen=True)
class Manifest:
    package: str
    version_name: str
    version_code: int
    debuggable: bool
    test_only: bool
    declared: tuple[tuple[str, str], ...]
    requested: tuple[str, ...]


@dataclass(frozen=True)
class Signer:
    dn: str
    sha256: str

    @property
    def debug(self):
        # Conventional Android debug certificates. A different DN is NOT proof of a release key.
        return re.search(r'(?:^|,)\s*CN\s*=\s*"?Android Debug"?\s*(?:,|$)', self.dn, re.I) is not None


@dataclass(frozen=True)
class SdkTools:
    analyzer: Path
    signer: Path


def fingerprint(value):
    normalized = value.replace(":", "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", normalized):
        raise ValueError("Zertifikatsfingerabdruck muss 64 SHA-256-Hexzeichen enthalten")
    return normalized


def parse_manifest(xml):
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise InspectionError("Manifest ist kein lesbares XML") from exc
    if root.tag != "manifest" or root.get("split") is not None or root.find("uses-split") is not None:
        raise InspectionError("Nur eigenständige APKs mit vollständigem Manifest werden unterstützt")
    if root.find("permission-tree") is not None:
        raise InspectionError("permission-tree-Deklarationen werden nicht unterstützt; manuelle Prüfung erforderlich")
    package = root.get("package", "")
    version = root.get(ANDROID + "versionName", "")
    code = root.get(ANDROID + "versionCode", "")
    major = root.get(ANDROID + "versionCodeMajor", "0")
    if not PACKAGE.fullmatch(package) or not version.strip() or version.startswith(("@", "?")):
        raise InspectionError("Application ID oder Versionsname fehlt/ist nicht aufgelöst")
    if not re.fullmatch(r"[0-9]+", code) or not re.fullmatch(r"[0-9]+", major):
        raise InspectionError("Versionscode ist nicht eindeutig lesbar")
    if not 0 < int(code) <= 0x7fffffff or not 0 <= int(major) <= 0x7fffffff:
        raise InspectionError("Versionscode liegt außerhalb des unterstützten Bereichs")
    apps = root.findall("application")
    if len(apps) != 1:
        raise InspectionError("Manifest muss genau ein application-Element enthalten")

    def flag(name):
        value = apps[0].get(ANDROID + name, "false")
        if value not in ("true", "false"):
            raise InspectionError(f"Manifest-Flag {name} ist nicht aufgelöst")
        return value == "true"

    declared = []
    requested = set()
    for child in root:
        if child.tag not in ("permission", "uses-permission", "uses-permission-sdk-23", "uses-permission-sdk-m"):
            continue
        name = child.get(ANDROID + "name", "")
        if not PACKAGE.fullmatch(name):
            raise InspectionError("Berechtigungsname fehlt/ist nicht eindeutig lesbar")
        if child.tag == "permission":
            if any(p[0] == name for p in declared):
                raise InspectionError(f"Doppelte Berechtigungsdeklaration: {name}")
            declared.append((name, child.get(ANDROID + "protectionLevel", "normal")))
        else:
            requested.add(name)
    return Manifest(package, version, (int(major) << 32) | int(code), flag("debuggable"),
                    flag("testOnly"), tuple(sorted(declared)), tuple(sorted(requested)))


def parse_signers(output):
    # Newer Build Tools (37) label single signers by scheme rather than "Signer #1".
    # Keep numbered and unnumbered formats mutually exclusive; mixed/rotated outputs fail closed.
    schemes = set(re.findall(r"^(V1|V2|V3\.0) Signer: certificate ", output, re.M))
    if schemes:
        if len(schemes) != 1 or re.search(r"^Signer #", output, re.M):
            raise InspectionError("Gemischte Signiererformate sind nicht eindeutig prüfbar")
        output = output.replace(next(iter(schemes)) + " Signer: certificate ", "Signer #1 certificate ")
    count = re.findall(r"^Number of signers: ([0-9]+)$", output, re.M)
    dns = re.findall(r"^Signer #([0-9]+) certificate DN: (.+)$", output, re.M)
    hashes = re.findall(r"^Signer #([0-9]+) certificate SHA-256 digest: ([0-9a-fA-F]{64})$", output, re.M)
    certificate_names = [line for line in output.splitlines() if " certificate DN:" in line
                         and not line.startswith("Source Stamp Signer certificate DN:")]
    if len(count) != 1 or not 1 <= int(count[0]) <= 32:
        raise InspectionError("Signiereranzahl von apksigner nicht eindeutig lesbar")
    expected = {str(i) for i in range(1, int(count[0]) + 1)}
    if (len(certificate_names) != len(dns) or len(dns) != len(expected) or len(hashes) != len(expected)
            or {i for i, _ in dns} != expected or {i for i, _ in hashes} != expected):
        raise InspectionError("Zertifikatsdaten von apksigner unvollständig oder mehrdeutig")
    names, digests = dict(dns), dict(hashes)
    return tuple(Signer(names[str(i)], digests[str(i)].lower()) for i in range(1, int(count[0]) + 1))


def sdk_tools(sdk):
    if sdk is None:
        raise InspectionError("Android SDK fehlt: --sdk oder ANDROID_HOME/ANDROID_SDK_ROOT setzen")
    sdk = Path(sdk).expanduser().resolve()
    # Stable build-tools only; do not silently choose a preview SDK tool.
    versions = [p for p in (sdk / "build-tools").glob("*") if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", p.name)]
    for directory in sorted(versions, key=lambda p: tuple(map(int, p.name.split("."))), reverse=True):
        signer = directory / "apksigner"
        if signer.is_file() and os.access(signer, os.X_OK):
            break
    else:
        raise InspectionError("Kein ausführbares apksigner in stabilen SDK build-tools gefunden")
    candidates = [sdk / "cmdline-tools/latest/bin/apkanalyzer"]
    candidates += sorted((sdk / "cmdline-tools").glob("*/bin/apkanalyzer"), reverse=True)
    analyzer = next((p for p in candidates if p.is_file() and os.access(p, os.X_OK)), None)
    if analyzer is None:
        raise InspectionError("Kein ausführbares apkanalyzer in SDK cmdline-tools gefunden")
    return SdkTools(analyzer, signer)


def run_tool(command):
    try:
        result = subprocess.run([str(c) for c in command], capture_output=True, text=True,
                                encoding="utf-8", timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired, UnicodeError) as exc:
        raise InspectionError(f"SDK-Werkzeug nicht zuverlässig ausführbar: {type(exc).__name__}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[:1200]
        raise InspectionError(f"{Path(command[0]).name} fehlgeschlagen (Exit {result.returncode}): {detail!r}")
    return result.stdout


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_apk(path, tools):
    if not path.is_file():
        raise InspectionError("APK-Datei fehlt oder ist keine reguläre Datei")
    before = file_hash(path)
    try:
        with zipfile.ZipFile(path) as archive:
            if archive.namelist().count("AndroidManifest.xml") != 1:
                raise InspectionError("APK muss genau ein AndroidManifest.xml enthalten")
    except zipfile.BadZipFile as exc:
        raise InspectionError("Datei ist kein gültiges APK/ZIP-Archiv") from exc
    manifest = parse_manifest(run_tool([tools.analyzer, "manifest", "print", path]))
    # A parsed certificate is not sufficient: verification must itself exit successfully.
    signers = parse_signers(run_tool([tools.signer, "verify", "--verbose", "--print-certs", path]))
    if file_hash(path) != before:
        raise InspectionError("APK wurde während der Prüfung verändert")
    return manifest, signers, before


def policy_errors(manifest, signers, expected_id, mode, trusted_certs):
    errors = []
    if manifest.package == ORIGINAL_ID:
        errors.append("Original-Glass-Paket: nicht für den Parallelbetrieb freigegeben")
    if manifest.package != expected_id:
        errors.append(f"Application ID stimmt nicht mit dem Sollwert {expected_id!r} überein")
    for name, _ in manifest.declared:
        if name.startswith(RESERVED_PERMISSIONS):
            errors.append(f"Kollidierende Original-/Begleitapp-Berechtigung deklariert: {name}")
        elif not name.startswith(expected_id + "."):
            errors.append(f"Deklarierte Berechtigung außerhalb des eigenen Namensraums: {name}")
    actual_certs = {s.sha256 for s in signers}
    if trusted_certs and actual_certs != set(trusted_certs):
        errors.append("Aktuelle Signierer entsprechen nicht exakt den erwarteten Zertifikaten")
    if mode == "production":
        if not trusted_certs:
            errors.append("Produktionsprüfung verlangt ausdrücklich erwartete Zertifikatsfingerabdrücke")
        if any(s.debug for s in signers):
            errors.append("Android-Debug-Zertifikat: keine Produktionsfreigabe")
        if manifest.debuggable or manifest.test_only:
            errors.append("debuggable oder testOnly ist gesetzt: keine Produktionsfreigabe")
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description="APK-Isolationsprüfung ohne Gerät oder Installation (Python 3.10+).")
    parser.add_argument("apk", type=Path, help="zu prüfende, eigenständige APK")
    parser.add_argument("--expected-id", required=True, help="ausdrücklich erwartete eigene Application ID")
    parser.add_argument("--mode", choices=("development", "production"), default="production")
    parser.add_argument("--expected-cert-sha256", action="append", type=fingerprint, default=[],
                        help="erwarteter aktueller Signierer; bei mehreren Signierern wiederholen")
    parser.add_argument("--sdk", type=Path, help="Android SDK; sonst ANDROID_HOME oder ANDROID_SDK_ROOT")
    args = parser.parse_args(argv)
    if not PACKAGE.fullmatch(args.expected_id) or args.expected_id == ORIGINAL_ID:
        parser.error("--expected-id muss eine gültige eigene ID sein und darf nicht dev.glasslauncher sein")
    print(f"APK-Isolationsprüfung — Modus: {args.mode}")
    print(f"Datei: {str(args.apk)!r}")
    try:
        tools = sdk_tools(args.sdk or os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT"))
        manifest, signers, digest = inspect_apk(args.apk.expanduser().resolve(), tools)
    except (InspectionError, OSError, ValueError) as exc:
        print(f"FEHLER: Prüfung nicht zuverlässig möglich: {str(exc)!r}")
        print("Ergebnis: ABGELEHNT (Exit 2)")
        return 2
    print(f"SDK-Analyse: {str(tools.analyzer)!r}")
    print(f"SDK-Signaturprüfung: {str(tools.signer)!r}")
    print(f"Application ID: {manifest.package}")
    print(f"Version: {manifest.version_name!r}; Versionscode: {manifest.version_code}")
    print(f"APK-SHA-256: {digest}")
    print(f"Manifest: debuggable={manifest.debuggable}, testOnly={manifest.test_only}")
    print("Signatur: kryptografisch durch apksigner verifiziert")
    for i, signer in enumerate(signers, 1):
        kind = "Android-Debug-Zertifikat" if signer.debug else "kein Standard-Debug-Name (allein kein Produktionsnachweis)"
        print(f"  Signierer {i}: {signer.dn!r}; {kind}")
        print(f"  Zertifikat-SHA-256: {signer.sha256}")
    print("Selbst deklarierte Berechtigungen (<permission>):")
    for name, protection in manifest.declared:
        print(f"  {name} — protectionLevel={protection!r}")
    if not manifest.declared:
        print("  keine")
    print("Angeforderte Berechtigungen (<uses-permission*>; keine eigenen Deklarationen):")
    for name in manifest.requested:
        print(f"  {name}")
    if not manifest.requested:
        print("  keine")
    errors = policy_errors(manifest, signers, args.expected_id, args.mode, args.expected_cert_sha256)
    for error in errors:
        print(f"ABGELEHNT: {error}")
    if errors:
        print("Ergebnis: ABGELEHNT (Exit 1)")
        return 1
    if args.mode == "development":
        print("Ergebnis: Entwicklungs-Isolationsprüfung bestanden; KEINE Produktionsfreigabe (Exit 0)")
        if not args.expected_cert_sha256:
            print("Hinweis: Entwicklungszertifikat nicht an einen erwarteten Fingerabdruck gebunden.")
    else:
        print("Ergebnis: Produktions-Identitätsprüfung bestanden (Exit 0)")
    print("Geltungsbereich: APK-Identität; keine allgemeine Sicherheits- oder Installationsfreigabe.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
