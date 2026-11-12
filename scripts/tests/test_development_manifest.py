"""Regression tests for the additional M1 artifact manifest boundary."""
import importlib.util
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location("development_manifest", Path(__file__).resolve().parents[1] / "check-development-manifest.py")
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)
APP = gate.APP
VALID = f'''<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="{APP}">
<permission android:name="{APP}.DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION" android:protectionLevel="signature"/>
<uses-permission android:name="android.permission.INTERNET"/><uses-permission android:name="android.permission.ACCESS_NETWORK_STATE"/>
<uses-permission android:name="{APP}.DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION"/>
<application android:name="dev.glasslauncher.GlassApp" android:allowBackup="false" android:debuggable="true">
<activity android:name="dev.glasslauncher.MainActivity" android:exported="true"><intent-filter>
<action android:name="android.intent.action.MAIN"/><category android:name="android.intent.category.LAUNCHER"/>
<category android:name="android.intent.category.LEANBACK_LAUNCHER"/></intent-filter></activity>
<provider android:name="androidx.startup.InitializationProvider" android:authorities="{APP}.androidx-startup" android:exported="false"/>
</application></manifest>'''


class DevelopmentManifestTests(unittest.TestCase):
    def test_valid_isolated_manifest(self):
        self.assertEqual([], gate.audit(VALID))

    def test_sdk_numeric_signature_levels(self):
        for value in ("0x2", "0x00000002", "2"):
            with self.subTest(value=value):
                self.assertEqual([], gate.audit(VALID.replace('protectionLevel="signature"', f'protectionLevel="{value}"')))

    def test_original_identity_and_authority_rejected(self):
        self.assertTrue(gate.audit(VALID.replace(APP, "dev.glasslauncher")))

    def test_shared_uid_rejected(self):
        self.assertTrue(gate.audit(VALID.replace('package=', 'android:sharedUserId="legacy" package=')))

    def test_both_home_categories_rejected(self):
        for category in ("android.intent.category.HOME", "fireos.intent.category.MAIN_HOME"):
            with self.subTest(category=category):
                self.assertTrue(gate.audit(VALID.replace('</intent-filter>', f'<category android:name="{category}"/></intent-filter>')))

    def test_privileged_or_new_permission_rejected(self):
        for permission in ("android.permission.WRITE_SECURE_SETTINGS", "android.permission.INSTALL_PACKAGES", "android.permission.WAKE_LOCK"):
            with self.subTest(permission=permission):
                self.assertTrue(gate.audit(VALID.replace('<application ', f'<uses-permission android:name="{permission}"/><application ')))

    def test_new_component_rejected(self):
        for tag in ("service", "receiver", "activity", "activity-alias"):
            with self.subTest(tag=tag):
                self.assertTrue(gate.audit(VALID.replace('</application>', f'<{tag} android:name="unexpected"/></application>')))

    def test_wrong_class_not_confused_with_application_id(self):
        self.assertTrue(gate.audit(VALID.replace('dev.glasslauncher.MainActivity', APP + '.MainActivity')))

    def test_foreign_or_exported_provider_rejected(self):
        self.assertTrue(gate.audit(VALID.replace(APP + '.androidx-startup', 'dev.glasslauncher.debug.art')))
        self.assertTrue(gate.audit(VALID.replace('android:exported="false"', 'android:exported="true"')))

    def test_missing_launcher_or_application_rejected(self):
        self.assertTrue(gate.audit(VALID.replace('android.intent.category.LAUNCHER', 'unexpected')))
        self.assertTrue(gate.audit(f'<manifest package="{APP}"/>'))

    def test_missing_or_unprotected_generated_permission_rejected(self):
        self.assertTrue(gate.audit(VALID.replace('android:protectionLevel="signature"', 'android:protectionLevel="normal"')))
        self.assertTrue(gate.audit(VALID.replace('<permission ', '<permission-tree ')))


if __name__ == '__main__':
    unittest.main()
