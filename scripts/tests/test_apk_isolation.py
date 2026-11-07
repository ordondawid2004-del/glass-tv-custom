"""Unit tests; opt-in SDK integration tests build/sign disposable APKs, never the launcher."""

from contextlib import redirect_stdout
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile


SCRIPT = Path(__file__).resolve().parents[1] / "check-apk-isolation.py"
SPEC = importlib.util.spec_from_file_location("apk_isolation", SCRIPT)
gate = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = gate
SPEC.loader.exec_module(gate)

APP = "io.github.example.tvhome"
CERT = "a" * 64
OTHER_CERT = "b" * 64


def manifest_xml(package=APP, declarations=(), requested=(), app_flags="", version="1.0", extra=""):
    return f'''<manifest xmlns:android="http://schemas.android.com/apk/res/android"
        package="{package}" android:versionCode="7" android:versionName="{version}" {extra}>
        <uses-sdk android:minSdkVersion="28" android:targetSdkVersion="30"/>
        {''.join(f'<permission android:name="{p}" android:protectionLevel="signature"/>' for p in declarations)}
        {''.join(f'<uses-permission android:name="{p}"/>' for p in requested)}
        <application android:hasCode="false" android:label="Isolation test" {app_flags}/>
    </manifest>'''


def signer_output(dn="CN=Fixture Release", cert=CERT):
    return f"Number of signers: 1\nSigner #1 certificate DN: {dn}\nSigner #1 certificate SHA-256 digest: {cert}\n"


class PolicyTests(unittest.TestCase):
    def errors(self, xml=None, signers=None, mode="production", trusted=(CERT,)):
        return gate.policy_errors(gate.parse_manifest(xml or manifest_xml()),
                                  signers or (gate.Signer("CN=Fixture Release", CERT),), APP, mode, trusted)

    def test_own_production_identity_with_pinned_certificate(self):
        self.assertEqual([], self.errors(manifest_xml(declarations=(APP + ".permission.BROWSER",))))

    def test_original_identity_is_rejected_even_in_development(self):
        self.assertTrue(any("Original-Glass" in e for e in self.errors(manifest_xml(package=gate.ORIGINAL_ID), mode="development")))

    def test_arbitrary_other_package_does_not_count_as_our_app(self):
        self.assertTrue(self.errors(manifest_xml(package="io.example.other")))

    def test_all_known_original_permissions_are_rejected(self):
        permissions = ("dev.glasslauncher.permission.BROWSER", "dev.glasslauncher.permission.BIND_ADDON",
                       "dev.glasslauncher.DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION", "com.phairplay.permission.CONTROL",
                       "com.glasslauncher.anything.NEW")
        for permission in permissions:
            with self.subTest(permission=permission):
                self.assertTrue(any("Kollidierende" in e for e in self.errors(manifest_xml(declarations=(permission,)))))

    def test_requesting_foreign_permissions_is_not_a_declaration(self):
        xml = manifest_xml(requested=("dev.glasslauncher.permission.BROWSER", "android.permission.INTERNET"))
        self.assertEqual((), gate.parse_manifest(xml).declared)
        self.assertEqual([], self.errors(xml))

    def test_foreign_permission_namespace_and_prefix_lookalike_rejected(self):
        for name in ("android.permission.NEW", APP + "evil.permission.CONTROL"):
            with self.subTest(name=name):
                self.assertTrue(self.errors(manifest_xml(declarations=(name,))))

    def test_debug_certificate_rejected_even_when_pinned_and_not_debuggable(self):
        for dn in ("C=US, O=Android, CN=Android Debug", 'CN="Android Debug", O=Android'):
            with self.subTest(dn=dn):
                self.assertTrue(any("Debug-Zertifikat" in e for e in self.errors(signers=(gate.Signer(dn, CERT),))))

    def test_debug_certificate_allowed_only_as_development(self):
        self.assertEqual([], self.errors(signers=(gate.Signer("CN=Android Debug", CERT),), mode="development", trusted=()))

    def test_debuggable_and_test_only_are_rejected_in_production(self):
        for flag in ("debuggable", "testOnly"):
            with self.subTest(flag=flag):
                self.assertTrue(self.errors(manifest_xml(app_flags=f'android:{flag}="true"')))

    def test_production_requires_pin_and_rejects_wrong_pin(self):
        self.assertTrue(self.errors(trusted=()))
        self.assertTrue(self.errors(trusted=(OTHER_CERT,)))

    def test_development_also_enforces_a_supplied_pin(self):
        self.assertTrue(self.errors(mode="development", trusted=(OTHER_CERT,)))

    def test_multiple_signers_require_exact_set(self):
        signers = (gate.Signer("CN=A", CERT), gate.Signer("CN=B", OTHER_CERT))
        self.assertEqual([], self.errors(signers=signers, trusted=(CERT, OTHER_CERT)))
        self.assertTrue(self.errors(signers=signers))


class ParserTests(unittest.TestCase):
    def test_manifest_versions_flags_and_permission_lists(self):
        parsed = gate.parse_manifest(manifest_xml(declarations=(APP + ".permission.API",),
                                                 requested=("android.permission.INTERNET",),
                                                 extra='android:versionCodeMajor="2"'))
        self.assertEqual((2 << 32) | 7, parsed.version_code)
        self.assertEqual("1.0", parsed.version_name)
        self.assertFalse(parsed.debuggable)
        self.assertFalse(parsed.test_only)
        self.assertEqual(((APP + ".permission.API", "signature"),), parsed.declared)
        self.assertEqual(("android.permission.INTERNET",), parsed.requested)

    def test_sdk_specific_requested_permission_is_not_declared(self):
        xml = manifest_xml(requested=("android.permission.CAMERA",)).replace("uses-permission", "uses-permission-sdk-23")
        self.assertEqual(("android.permission.CAMERA",), gate.parse_manifest(xml).requested)
        self.assertEqual((), gate.parse_manifest(xml).declared)

    def test_invalid_or_incomplete_manifest_fails_closed(self):
        cases = ("", "not XML", "<notmanifest/>", manifest_xml(version=""), manifest_xml(version="@string/version"),
                 manifest_xml().replace('versionCode="7"', 'versionCode="unknown"'),
                 manifest_xml().replace('versionCode="7"', 'versionCode="0"'),
                 manifest_xml().replace('versionCode="7"', 'versionCode="2147483648"'),
                 manifest_xml(app_flags='android:debuggable="@bool/debug"'),
                 manifest_xml(extra='split="feature"'), manifest_xml().replace("application", "missing"),
                 manifest_xml(declarations=(APP + ".permission.API", APP + ".permission.API")),
                 manifest_xml(requested=("@string/permission",)))
        for xml in cases:
            with self.subTest(xml=xml):
                with self.assertRaises(gate.InspectionError):
                    gate.parse_manifest(xml)

    def test_realistic_signer_output(self):
        self.assertEqual((gate.Signer("CN=Fixture Release", CERT),), gate.parse_signers(signer_output()))

    def test_build_tools_37_scheme_signer_formats(self):
        for scheme in ("V1", "V2", "V3.0"):
            with self.subTest(scheme=scheme):
                output = signer_output().replace("Signer #1 certificate ", scheme + " Signer: certificate ")
                self.assertEqual((gate.Signer("CN=Fixture Release", CERT),), gate.parse_signers(output))
                with self.assertRaises(gate.InspectionError):
                    gate.parse_signers(output + signer_output())

    def test_permission_tree_cannot_bypass_permission_policy(self):
        xml = manifest_xml().replace("<application", '<permission-tree android:name="dev.glasslauncher.tree"/><application')
        with self.assertRaises(gate.InspectionError):
            gate.parse_manifest(xml)

    def test_missing_truncated_duplicate_or_unknown_signer_format_fails_closed(self):
        for output in ("", "Verifies", signer_output().replace("Number of signers: 1", "Number of signers: 2"),
                       signer_output().replace(CERT, "invalid"), signer_output() + signer_output(),
                       signer_output().replace("Signer #1 certificate DN:", "Unknown signer DN:"),
                       signer_output() + "Signer (minSdkVersion=33) certificate DN: CN=Rotated\n"):
            with self.subTest(output=output):
                with self.assertRaises(gate.InspectionError):
                    gate.parse_signers(output)

    def test_fingerprint_normalization_and_invalid_input(self):
        self.assertEqual(CERT, gate.fingerprint(":".join(["AA"] * 32)))
        for value in ("", "xyz", "a" * 63):
            with self.assertRaises(ValueError):
                gate.fingerprint(value)


class FailureTests(unittest.TestCase):
    def test_missing_sdk_or_sdk_tools_fails_closed(self):
        with self.assertRaises(gate.InspectionError):
            gate.sdk_tools(None)
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(gate.InspectionError):
                gate.sdk_tools(temp)

    def test_tool_nonzero_missing_and_timeout_fail_closed(self):
        outcomes = (subprocess.CompletedProcess(["tool"], 1, "", "verification failed"),
                    FileNotFoundError("missing"), subprocess.TimeoutExpired(["tool"], 30))
        for outcome in outcomes:
            with self.subTest(outcome=outcome):
                kwargs = {"side_effect": outcome} if isinstance(outcome, Exception) else {"return_value": outcome}
                with patch.object(gate.subprocess, "run", **kwargs), self.assertRaises(gate.InspectionError):
                    gate.run_tool([Path("/sdk/tool"), "verify"])

    def test_missing_invalid_and_manifestless_apk_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "input.apk"
            tools = gate.SdkTools(Path("analyzer"), Path("signer"))
            with self.assertRaises(gate.InspectionError):
                gate.inspect_apk(path, tools)
            path.write_text("not an APK")
            with self.assertRaises(gate.InspectionError):
                gate.inspect_apk(path, tools)
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("other.txt", "not a manifest")
            with self.assertRaises(gate.InspectionError):
                gate.inspect_apk(path, tools)

    def test_apk_change_during_inspection_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "input.apk"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("AndroidManifest.xml", "fixture")
            with (patch.object(gate, "run_tool", side_effect=[manifest_xml(), signer_output()]),
                  patch.object(gate, "file_hash", side_effect=[CERT, OTHER_CERT]),
                  self.assertRaises(gate.InspectionError)):
                gate.inspect_apk(path, gate.SdkTools(Path("analyzer"), Path("signer")))

    def test_unreadable_evidence_returns_exit_two_and_report(self):
        output = io.StringIO()
        with patch.object(gate, "sdk_tools", side_effect=gate.InspectionError("fixture")), redirect_stdout(output):
            code = gate.main(["missing.apk", "--expected-id", APP])
        self.assertEqual(2, code)
        self.assertIn("nicht zuverlässig", output.getvalue())
        self.assertIn("Exit 2", output.getvalue())


@unittest.skipUnless(os.environ.get("APK_ISOLATION_INTEGRATION") == "1", "SDK integration is opt-in; see docs/apk-isolation.md")
class SdkIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="apk-isolation-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        cls.sdk = Path(os.environ.get("ANDROID_HOME") or os.environ["ANDROID_SDK_ROOT"])
        cls.tools = gate.sdk_tools(cls.sdk)
        cls.aapt = cls.tools.signer.parent / "aapt2"
        platforms = sorted((cls.sdk / "platforms").glob("*/android.jar"))
        if not platforms:
            raise RuntimeError("Integration tests require an installed SDK platform")
        cls.android_jar = platforms[-1]
        cls.keytool = Path(os.environ["JAVA_HOME"]) / "bin/keytool" if os.environ.get("JAVA_HOME") else Path("keytool")
        for alias, dn in (("release", "CN=Isolation Fixture Release"), ("debug", "CN=Android Debug,O=Android,C=US")):
            cls.command([cls.keytool, "-genkeypair", "-keystore", cls.root / (alias + ".jks"),
                         "-storepass", "fixture-only", "-keypass", "fixture-only", "-alias", alias,
                         "-keyalg", "RSA", "-keysize", "2048", "-validity", "2", "-dname", dn, "-noprompt"])
        cls.apks = {}
        cases = {
            "own": (manifest_xml(declarations=(APP + ".permission.API",)), "release"),
            "original": (manifest_xml(package=gate.ORIGINAL_ID, declarations=("dev.glasslauncher.permission.BROWSER",)), "debug"),
            "collision": (manifest_xml(declarations=("com.phairplay.permission.CONTROL",)), "release"),
            "requested": (manifest_xml(requested=("dev.glasslauncher.permission.BROWSER",)), "release"),
            "debug": (manifest_xml(), "debug"),
            "debuggable": (manifest_xml(app_flags='android:debuggable="true"'), "release"),
            "testonly": (manifest_xml(app_flags='android:testOnly="true"'), "release"),
            "v2": (manifest_xml(), "release"),
        }
        for name, (xml, alias) in cases.items():
            source = cls.root / (name + ".xml")
            source.write_text(xml, encoding="utf-8")
            unsigned = cls.root / (name + "-unsigned.apk")
            cls.command([cls.aapt, "link", "-o", unsigned, "--manifest", source, "-I", cls.android_jar])
            apk = cls.root / (name + ".apk")
            scheme_args = ["--v3-signing-enabled", "false"] if name == "v2" else []
            cls.command([cls.tools.signer, "sign", *scheme_args, "--ks", cls.root / (alias + ".jks"),
                         "--ks-key-alias", alias, "--ks-pass", "pass:fixture-only", "--key-pass", "pass:fixture-only",
                         "--out", apk, unsigned])
            cls.apks[name] = apk
        cls.release_cert = gate.parse_signers(gate.run_tool([cls.tools.signer, "verify", "--verbose", "--print-certs", cls.apks["own"]]))[0].sha256

    @staticmethod
    def command(args):
        result = subprocess.run([str(a) for a in args], capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise RuntimeError(f"Fixture tool failed: {result.stdout} {result.stderr}")

    def check(self, name, expected, *args):
        apk = self.apks.get(name, self.root / name)
        before = gate.file_hash(apk) if apk.is_file() else None
        result = subprocess.run([sys.executable, str(SCRIPT), str(apk), "--sdk", str(self.sdk),
                                 "--expected-id", APP, *args], capture_output=True, text=True, timeout=60)
        self.assertEqual(expected, result.returncode, result.stdout + result.stderr)
        self.assertIn("Ergebnis:", result.stdout)
        if before is not None:
            self.assertEqual(before, gate.file_hash(apk), "Gate must never modify an APK")
        return result.stdout

    def test_real_production_apk_passes_with_pinned_certificate(self):
        self.check("own", 0, "--expected-cert-sha256", self.release_cert)

    def test_real_v2_signed_apk_passes(self):
        self.check("v2", 0, "--expected-cert-sha256", self.release_cert)

    def test_original_apk_fails_in_development(self):
        text = self.check("original", 1, "--mode", "development")
        self.assertIn("Original-Glass", text)
        self.assertIn("Kollidierende", text)

    def test_permission_collision_fails(self):
        self.check("collision", 1, "--mode", "development")

    def test_requested_original_permission_does_not_collide(self):
        self.check("requested", 0, "--expected-cert-sha256", self.release_cert)

    def test_debug_apk_passes_development_without_production_label(self):
        text = self.check("debug", 0, "--mode", "development")
        self.assertIn("Android-Debug-Zertifikat", text)
        self.assertIn("KEINE Produktionsfreigabe", text)

    def test_debug_certificate_fails_production_even_when_pinned(self):
        cert = gate.parse_signers(gate.run_tool([self.tools.signer, "verify", "--verbose", "--print-certs", self.apks["debug"]]))[0].sha256
        self.check("debug", 1, "--expected-cert-sha256", cert)

    def test_debuggable_and_testonly_fail_production(self):
        for name in ("debuggable", "testonly"):
            with self.subTest(name=name):
                self.check(name, 1, "--expected-cert-sha256", self.release_cert)

    def test_missing_and_wrong_certificate_pin_fail(self):
        self.check("own", 1)
        self.check("own", 1, "--expected-cert-sha256", OTHER_CERT)

    def test_missing_and_invalid_files_fail(self):
        self.check("missing.apk", 2, "--mode", "development")
        (self.root / "invalid.apk").write_text("invalid")
        self.check("invalid.apk", 2, "--mode", "development")

    def test_unsigned_apk_fails(self):
        self.check("own-unsigned.apk", 2, "--mode", "development")

    def test_tampered_apk_fails_signature_verification(self):
        apk = self.root / "tampered.apk"
        apk.write_bytes(self.apks["own"].read_bytes())
        with zipfile.ZipFile(apk, "a") as archive:
            archive.writestr("tampered.txt", "signature must no longer match")
        self.check("tampered.apk", 2, "--mode", "development")


if __name__ == "__main__":
    unittest.main()
