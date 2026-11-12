"""Fail-closed upload-boundary tests; no SDK, key generation, devices or network."""
import io
import copy
from contextlib import redirect_stderr
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('ci_development', Path(__file__).resolve().parents[1] / 'ci-development.py')
ci = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ci)


class UploadBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.out = self.root / 'output'
        (self.out / 'reports').mkdir(parents=True)
        self.apk = self.root / 'sample.apk'
        self.apk.write_bytes(b'checked fixture bytes, not an Android APK')
        self.state = {'checks': {name: 'passed' for name in ci.REQUIRED},
                      'application_id': ci.APP, 'variant': ci.VARIANT,
                      'commit': 'b' * 40, 'expected_certificate_sha256': 'c' * 64,
                      'apk_sha256': ci.digest(self.apk), 'size_contract_verified': True}

    def test_all_checks_pass_stages_exact_checked_bytes_and_evidence(self):
        (self.out / 'reports/summary.json').write_text('{}')
        target = ci.approve(self.apk, self.out, self.state)
        apks = list(target.glob('*.apk'))
        self.assertEqual(1, len(apks))
        self.assertIn('development-' + 'b' * 40 + '-developmentDebug', apks[0].name)
        self.assertEqual(self.apk.read_bytes(), apks[0].read_bytes())
        self.assertTrue((target / 'reports/summary.json').is_file())
        self.assertIn(ci.digest(self.apk), (target / 'SHA256SUMS').read_text())

    def test_every_missing_failed_or_skipped_mandatory_check_prevents_staging(self):
        for name in ci.REQUIRED:
            for status in (None, 'failed', 'skipped'):
                with self.subTest(name=name, status=status):
                    state = {**self.state, 'checks': dict(self.state['checks'])}
                    if status is None:
                        del state['checks'][name]
                    else:
                        state['checks'][name] = status
                    with self.assertRaises(ci.Rejected):
                        ci.approve(self.apk, self.out, state)
                    self.assertFalse((self.out / 'approved').exists())

    def test_wrong_identity_or_variant_or_absent_pin_cannot_be_staged(self):
        for field, value in [('application_id', 'dev.glasslauncher'), ('variant', 'originalDebug'),
                             ('expected_certificate_sha256', ''), ('commit', 'bad')]:
            with self.subTest(field=field), self.assertRaises(ci.Rejected):
                ci.approve(self.apk, self.out, {**self.state, field: value})
        self.assertFalse((self.out / 'approved').exists())

    def test_missing_or_changed_apk_cannot_be_staged(self):
        self.apk.unlink()
        with self.assertRaises(ci.Rejected):
            ci.approve(self.apk, self.out, self.state)
        self.apk.write_bytes(b'changed after checks')
        with self.assertRaises(ci.Rejected):
            ci.approve(self.apk, self.out, self.state)
        self.assertFalse((self.out / 'approved').exists())

    def test_copy_failure_removes_partial_approval(self):
        with patch.object(ci.shutil, 'copyfile', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                ci.approve(self.apk, self.out, self.state)
        self.assertFalse((self.out / 'approved').exists())

    def test_existing_output_rejected_without_touching_old_files(self):
        sentinel = self.out / 'reports/sentinel'
        sentinel.write_text('keep')
        with self.assertRaises(ci.Rejected):
            ci.execute(self.out, self.root)
        self.assertEqual('keep', sentinel.read_text())


class ApkSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = self.root / ci.APK_DIR
        self.directory.mkdir(parents=True)
        self.apk = self.directory / ci.APK_NAME
        self.apk.write_bytes(b'fixture')
        self.meta = {'applicationId': ci.APP, 'variantName': ci.VARIANT,
                     'elements': [{'outputFile': ci.APK_NAME, 'versionName': '0.3.0-m1-dev', 'versionCode': 123}]}
        self.save()

    def save(self):
        (self.directory / 'output-metadata.json').write_text(json.dumps(self.meta))

    def test_exact_new_output_selected(self):
        self.assertEqual(self.apk, ci.select_apk(self.root, 0)[0])

    def test_no_fallback_to_original_or_missing_apk(self):
        original = self.root / 'app/build/outputs/apk/release/app-release.apk'
        original.parent.mkdir(parents=True)
        original.write_bytes(b'original')
        self.apk.unlink()
        with self.assertRaises(ci.Rejected):
            ci.select_apk(self.root, 0)
        self.assertEqual(b'original', original.read_bytes())

    def test_ambiguous_and_symlink_apks_rejected(self):
        other = self.directory / 'another.apk'
        other.write_bytes(b'other')
        with self.assertRaises(ci.Rejected):
            ci.select_apk(self.root, 0)
        self.apk.unlink()
        self.apk.symlink_to(other)
        other.rename(self.root / 'other')
        with self.assertRaises(ci.Rejected):
            ci.select_apk(self.root, 0)

    def test_stale_output_rejected(self):
        with self.assertRaises(ci.Rejected):
            ci.select_apk(self.root, time.time_ns() + 1_000_000)

    def test_wrong_identity_variant_version_or_output_rejected(self):
        for field, value in [('applicationId', 'dev.glasslauncher'), ('variantName', 'originalDebug')]:
            with self.subTest(field=field):
                old = self.meta[field]
                self.meta[field] = value
                self.save()
                with self.assertRaises(ci.Rejected):
                    ci.select_apk(self.root, 0)
                self.meta[field] = old
        self.meta['elements'][0]['outputFile'] = '../release/app-release.apk'
        self.save()
        with self.assertRaises(ci.Rejected):
            ci.select_apk(self.root, 0)


class PythonTestAccountingTests(unittest.TestCase):
    def test_skips_even_in_an_otherwise_successful_suite_fail(self):
        result = unittest.TestResult()
        result.testsRun = 37
        result.skipped.append(('integration', 'SDK unavailable'))
        self.assertTrue(result.wasSuccessful())
        with self.assertRaises(ci.Rejected):
            ci.check_test_result(result, 37)

    def test_missing_tests_or_failure_fail(self):
        result = unittest.TestResult()
        result.testsRun = 25
        with self.assertRaises(ci.Rejected):
            ci.check_test_result(result, 37)
        result.testsRun = 37
        result.failures.append(('integration', 'failed'))
        with self.assertRaises(ci.Rejected):
            ci.check_test_result(result, 37)

    def test_all_37_executed_pass(self):
        result = unittest.TestResult()
        result.testsRun = 37
        ci.check_test_result(result, 37)


class PipelineFailureTests(unittest.TestCase):
    """Exercise the actual orchestration with fake processes; inspect APK readiness and output."""
    def run_pipeline(self, failed=None, manifest_bad=False, tag=False, self_test_fixture=None, inventory_error=False, inventory_check=None):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            sdk = root / 'sdk'
            java = root / 'jdk/bin'
            for p in [java / 'java', java / 'keytool', sdk / 'build-tools/37.0.0/apksigner',
                      sdk / 'build-tools/37.0.0/aapt2', sdk / 'cmdline-tools/latest/bin/apkanalyzer']:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text('fake executable')
                p.chmod(0o700)
            platform = sdk / 'platforms/android-37.0/android.jar'
            platform.parent.mkdir(parents=True)
            platform.touch()
            github_output = root / 'github-output'
            seen_keys = []
            def process(command, **kwargs):
                command = [str(x) for x in command]
                env = kwargs.get('env', {})
                self.assertFalse(any(k.startswith('RELEASE_') for k in env))
                code = 0
                if 'test_ci_development.py' in command:
                    result_path = Path(command[command.index('--result') + 1])
                    if self_test_fixture is not None:
                        tests = root / 'scripts/tests'
                        tests.mkdir(parents=True)
                        (tests / 'test_ci_development.py').write_text(self_test_fixture)
                        try:
                            with redirect_stderr(io.StringIO()), patch.dict(sys.modules):
                                sys.modules.pop('test_ci_development', None)
                                ci.python_tests('test_ci_development.py', ci.CI_TEST_COUNT, result_path)
                        except ci.Rejected:
                            return subprocess.CompletedProcess(command, 1)
                    else:
                        result_path.write_text(json.dumps({'tests': ci.CI_TEST_COUNT, 'failures': 0, 'errors': 0, 'skipped': 0}))
                if 'python-tests' in command and '--result' in command and 'test_ci_development.py' not in command:
                    Path(command[command.index('--result') + 1]).write_text(json.dumps({
                        'tests': int(command[command.index('--expected') + 1]), 'failures': 0, 'errors': 0, 'skipped': 0}))
                if command[0].endswith('/java'):
                    return subprocess.CompletedProcess(command, 0, '', 'openjdk version "21.0.1"')
                if '-genkeypair' in command:
                    key = Path(command[command.index('-keystore') + 1])
                    key.write_bytes(b'private fixture key, must never be uploaded')
                    seen_keys.append(key)
                if '-exportcert' in command:
                    Path(command[command.index('-file') + 1]).write_bytes(b'public fixture certificate')
                if command[0].endswith('/gradlew'):
                    d = root / ci.APK_DIR
                    d.mkdir(parents=True)
                    (d / ci.APK_NAME).write_bytes(b'fake built APK')
                    (d / 'output-metadata.json').write_text(json.dumps({'applicationId':ci.APP,'variantName':ci.VARIANT,
                        'elements':[{'outputFile':ci.APK_NAME,'versionName':'0.3.0-m1-dev','versionCode':123}]}))
                    for variant, cases in ci.JVM.items():
                        dest = root / f'app/build/test-results/test{variant}DebugUnitTest'
                        dest.mkdir(parents=True)
                        for name, count in cases.items():
                            (dest / f'TEST-{name}.xml').write_text(f'<testsuite name="{name}" tests="{count}" failures="0" errors="0" skipped="0">' + ''.join(f'<testcase classname="{name}" name="method_{i}"/>' for i in range(count)) + '</testsuite>')
                    dest = root / 'app/build/reports'
                    dest.mkdir(parents=True)
                    for suffix in ('xml','txt'):
                        (dest / f'lint-results-developmentDebug.{suffix}').write_text('lint fixture')
                if command[0].endswith('/aapt2'):
                    kwargs['stdout'].write("application-label:'TV Home – Entwicklung'\n".encode())
                if 'production' in command:
                    code = 1
                if command[0].endswith('/apkanalyzer'):
                    kwargs['stdout'].write(b'<manifest/>')
                if manifest_bad and any(x.endswith('/check-development-manifest.py') for x in command):
                    code = 1
                checks = {'ci-tests':'test_ci_development.py', 'm0-tests':'test_apk_isolation.py',
                          'manifest-tests':'test_development_manifest.py', 'gradle':'--rerun-tasks',
                          'm0-development':'development', 'production-rejected':'production',
                          'manifest':'/check-development-manifest.py', 'size':'/apk-size-check'}
                if failed and any(x == checks[failed] or x.endswith(checks[failed]) for x in command):
                    code = 0 if failed == 'production-rejected' else 1
                return subprocess.CompletedProcess(command, code)
            def git(command, **kwargs):
                return 'b' * 40 + '\n' if 'rev-parse' in command else b''
            with patch.object(ci, 'ROOT', root), patch.dict(os.environ, {
                'JAVA_HOME':str(java.parent), 'ANDROID_HOME':str(sdk), 'GITHUB_OUTPUT':str(github_output),
                'GITHUB_REF':'refs/tags/v1' if tag else 'refs/heads/master', 'GITHUB_SHA':'b'*40}, clear=True), \
                 patch.object(ci.subprocess, 'run', side_effect=process), \
                 patch.object(ci.subprocess, 'check_output', side_effect=git), \
                 patch.object(ci, 'validate_inventory', side_effect=inventory_check if inventory_check is not None else (ci.Rejected('inventory fixture rejected') if inventory_error else None),
                              return_value={v: {n: [f'method_{i}' for i in range(count)] for n,count in cases.items()} for v,cases in ci.JVM.items()}):
                class SimulatedApprovedContract:
                    capture = False
                    def begin(self, project, state, env, reports):
                        # Explicit simulated authority, never a real operator root.
                        # Match real child isolation: readiness belongs only to the parent.
                        env.pop('GITHUB_OUTPUT', None)
                        state['commit'] = 'b' * 40
                        state['working_tree_dirty'] = False
                    def bind_signer(self, pin, der, private_key_digest):
                        assert pin == ci.hashlib.sha256(der).hexdigest()
                    def gradle_executable(self):
                        return root / 'gradlew'
                    def gradle_flags(self):
                        return []
                    def finish(self, apk, state, pin, der):
                        state['size_contract_verified'] = True
                        # The old size command still runs in this orchestration fixture.
                        result = process([root / 'scripts/apk-size-check', apk], stdout=io.BytesIO(), env={})
                        ci.require(result.returncode == 0, 'fixture size failure')
                        for name in ('size', 'reference-contract', 'source-inputs', 'dependency-artifacts', 'size-profile', 'size-comparison'):
                            state['checks'][name] = 'passed'
                        return True
                code = ci.execute(root / 'out', root, SimulatedApprovedContract())
            approved = root / 'out/approved'
            if failed or manifest_bad or tag or self_test_fixture is not None or inventory_error or inventory_check is not None:
                self.assertEqual(1, code)
                self.assertFalse(approved.exists())
                outputs = dict(line.split('=',1) for line in github_output.read_text().splitlines())
                self.assertEqual('false', outputs['apk_ready'])
                self.assertEqual('true', outputs['diagnostics_ready'])
            else:
                self.assertEqual(0, code)
                self.assertTrue(approved.is_dir())
                outputs = dict(line.split('=',1) for line in github_output.read_text().splitlines())
                self.assertEqual('true', outputs['apk_ready'])
                self.assertEqual('true', outputs['diagnostics_ready'])
                self.assertFalse(any('keystore' in p.name for p in approved.rglob('*')))
            run_record = json.loads((root / 'out/reports/run.json').read_text())
            summary = json.loads((root / 'out/reports/summary.json').read_text())
            self.assertEqual(run_record['id'], summary['diagnostic_run']['id'])
            self.assertEqual(str(root / 'out/reports'), outputs['reports_dir'])
            self.assertTrue(all(not k.exists() for k in seen_keys), 'Private key must be removed after success/failure')

    def test_every_command_failure_prevents_ready_signal_and_apk_staging(self):
        for failed in ('ci-tests', 'm0-tests', 'manifest-tests', 'gradle', 'm0-development',
                       'production-rejected', 'manifest', 'size'):
            with self.subTest(failed=failed):
                self.run_pipeline(failed=failed)

    def test_unacceptable_manifest_prevents_upload(self):
        self.run_pipeline(manifest_bad=True)

    def test_success_emits_ready_only_after_checks_and_key_cleanup(self):
        self.run_pipeline()

    def test_tag_never_builds_development_apk(self):
        self.run_pipeline(tag=True)


class StrictSelfTestTests(unittest.TestCase):
    @staticmethod
    def fixture(count, skip=False, fail=False):
        source = 'import unittest\nclass Fixture(unittest.TestCase):\n'
        for i in range(count):
            if i == 0 and skip:
                source += ' @unittest.skip("controlled skip")\n'
            source += f' def test_{i}(self):\n  ' + ('self.fail("controlled failure")' if i == 0 and fail else 'pass') + '\n'
        return source if count else 'import unittest\n'

    def evaluate(self, source, expected, accepted):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            tests = root / 'scripts/tests'
            tests.mkdir(parents=True)
            (tests / 'test_fixture.py').write_text(source)
            result = root / 'result.json'
            with patch.object(ci, 'ROOT', root), redirect_stderr(io.StringIO()), patch.dict(sys.modules):
                sys.modules.pop('test_fixture', None)
                if accepted:
                    ci.python_tests('test_fixture.py', expected, result)
                else:
                    with self.assertRaises(ci.Rejected):
                        ci.python_tests('test_fixture.py', expected, result)
            data = json.loads(result.read_text())
            if accepted:
                ci.read_test_report(result, expected)
            else:
                with self.assertRaises(ci.Rejected):
                    ci.read_test_report(result, expected)
            return data

    def test_complete_successful_run(self):
        self.assertEqual(2, self.evaluate(self.fixture(2), 2, True)['tests'])

    def test_actual_skip_despite_normal_unittest_success(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'test_fixture.py').write_text(self.fixture(2, skip=True))
            process = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'discover',
                                      '-s', folder, '-p', 'test_fixture.py'], capture_output=True, text=True)
            self.assertEqual(0, process.returncode, process.stderr)
            self.assertIn('skipped=1', process.stderr)
        namespace = {}
        exec(self.fixture(2, skip=True), namespace)
        normal = unittest.TextTestRunner(stream=io.StringIO()).run(unittest.TestLoader().loadTestsFromTestCase(namespace['Fixture']))
        self.assertTrue(normal.wasSuccessful())
        self.assertEqual(1, len(normal.skipped))
        self.assertEqual(1, self.evaluate(self.fixture(2, skip=True), 2, False)['skipped'])

    def test_missing_case_rejected(self):
        self.assertEqual(1, self.evaluate(self.fixture(1), 2, False)['tests'])

    def test_empty_run_rejected(self):
        self.assertEqual(0, self.evaluate(self.fixture(0), 2, False)['tests'])

    def test_normal_failure_rejected(self):
        self.assertEqual(1, self.evaluate(self.fixture(2, fail=True), 2, False)['failures'])

    def test_unexpected_larger_inventory_rejected(self):
        self.assertEqual(3, self.evaluate(self.fixture(3), 2, False)['tests'])

    def test_missing_unreadable_or_contradictory_reports_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'result.json'
            with self.assertRaises(ci.Rejected):
                ci.read_test_report(path, 2)
            for text in ('invalid json', '[]', '{}',
                         '{"tests":2,"failures":0,"errors":0,"skipped":true}',
                         '{"tests":2,"failures":0,"errors":0,"skipped":3}'):
                path.write_text(text)
                with self.subTest(text=text), self.assertRaises(ci.Rejected):
                    ci.read_test_report(path, 2)
            path.unlink()
            path.mkdir()
            with self.assertRaises(ci.Rejected):
                ci.read_test_report(path, 2)

    def test_real_skip_and_missing_inventory_stop_pipeline_before_approval(self):
        harness = PipelineFailureTests()
        for source in (self.fixture(ci.CI_TEST_COUNT, skip=True), self.fixture(ci.CI_TEST_COUNT - 1)):
            harness.run_pipeline(self_test_fixture=source)


class JvmScopeTests(unittest.TestCase):
    """Real filesystem inventory/XML checks; no Kotlin/Gradle execution in these fixtures."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dev = 'dev.glasslauncher.net.CoreTest'
        self.orig = 'dev.glasslauncher.system.ApkInstallTest'
        self.core = {'Development': {self.dev: 1}, 'Original': {self.orig: 1}}
        self.patcher = patch.object(ci, 'JVM', self.core)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.filters = patch.object(ci, 'FILTERS', {'Development': ('dev.glasslauncher.net.*',), 'Original': (self.orig,)})
        self.filters.start()
        self.addCleanup(self.filters.stop)
        self.source('Core.kt', 'package dev.glasslauncher.net\nclass CoreTest { @Test fun core() {} }')
        self.source('Legacy.kt', 'package dev.glasslauncher.system\nclass ApkInstallTest { @Test fun legacy() {} }')
        (self.root / 'app/build.gradle.kts').write_text('preview configuration fixture')
        (self.root / 'settings.gradle.kts').write_text('include(":app")\n')
        self.data = self.inventory()
        self.save()

    def source(self, name, text):
        p = self.root / 'app/src/test/java' / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def inventory(self):
        entries = []
        for path in sorted(ci.test_sources(self.root)):
            facts = ci.source_facts(self.root / path)
            classes = []
            for c in facts['test_classes']:
                variant = next((v for v in ci.FILTERS if ci.selected(c['name'], v)), None)
                classes.append({'name': c['name'], 'role': 'A' if variant == 'Development' else 'B',
                                'reason': 'Explicit fixture classification', 'variant': variant})
            entries.append({'path':path,'role':'A' if any(c['role']=='A' for c in classes) else 'B',
                            'reason':'Reviewed fixture scope','facts':facts,'classes':classes})
        return {'schema':1,'sources':entries,'module_structure':{
                'path':'settings.gradle.kts','sha256':ci.digest(self.root/'settings.gradle.kts'),
                'projects':ci.settings_projects(self.root),'reason':'Explicit fixture module approval'},'generator':{'path':'app/build.gradle.kts',
                'sha256':ci.digest(self.root/'app/build.gradle.kts'),'role':'B','reason':'Separate fixture preview role',
                'class':'com.github.takahirom.roborazzi.RoborazziPreviewParameterizedTests'}}

    def save(self):
        p = self.root / ci.SCOPE_FILE
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.data))

    def expected(self):
        return ci.validate_inventory(self.root)

    def results(self, expected=None):
        expected = self.expected() if expected is None else expected
        for variant, classes in expected.items():
            folder = self.root / f'app/build/test-results/test{variant}DebugUnitTest'
            folder.mkdir(parents=True, exist_ok=True)
            for name, methods in classes.items():
                text = f'<testsuite name="{name}" tests="{len(methods)}" failures="0" errors="0" skipped="0">'
                text += ''.join(f'<testcase classname="{name}" name="{m}"/>' for m in methods) + '</testsuite>'
                (folder / f'TEST-{name}.xml').write_text(text)
        return self.root / f'app/build/test-results/testDevelopmentDebugUnitTest/TEST-{self.dev}.xml'

    def check_results(self):
        reports = self.root / 'reports'
        reports.mkdir(exist_ok=True)
        return ci.jvm_results(self.root, reports, 0)

    def test_current_inventory_valid(self):
        self.assertEqual({self.dev}, set(self.expected()['Development']))

    def test_unknown_new_source_in_allowed_package_rejected(self):
        self.source('New.kt', 'package dev.glasslauncher.net\nclass NewTest { @Test fun added() {} }')
        with self.assertRaises(ci.Rejected): self.expected()

    def test_removed_and_moved_sources_rejected(self):
        p = self.root / 'app/src/test/java/Core.kt'
        p.rename(p.with_name('Moved.kt'))
        with self.assertRaises(ci.Rejected): self.expected()
        p.with_name('Moved.kt').unlink()
        with self.assertRaises(ci.Rejected): self.expected()

    def test_missing_critical_class_after_review_rejected(self):
        self.source('Core.kt', 'package dev.glasslauncher.net\nclass NotCoreTest { @Test fun other() {} }')
        self.data = self.inventory(); self.save()
        with self.assertRaises(ci.Rejected): self.expected()

    def test_malformed_incomplete_and_duplicate_inventory_rejected(self):
        original = copy.deepcopy(self.data)
        for mutation in ('missing', 'json', 'field', 'source', 'duplicate'):
            self.data = copy.deepcopy(original)
            self.save()
            p = self.root / ci.SCOPE_FILE
            if mutation == 'missing': p.unlink()
            elif mutation == 'json': p.write_text('not json')
            elif mutation == 'field': p.write_text('{}')
            elif mutation == 'source': self.data['sources'].pop(); self.save()
            else: self.data['sources'].append(copy.deepcopy(self.data['sources'][0])); self.save()
            with self.subTest(mutation=mutation), self.assertRaises(ci.Rejected): self.expected()

    def test_changed_class_or_generator_requires_review(self):
        p = self.root / 'app/src/test/java/Core.kt'
        original = p.read_text()
        p.write_text(original.replace('core()', 'changed()'))
        with self.assertRaises(ci.Rejected): self.expected()
        p.write_text(original)
        (self.root / 'app/build.gradle.kts').write_text('changed previews')
        with self.assertRaises(ci.Rejected): self.expected()

    def test_updated_inventory_discovers_new_allowed_class(self):
        self.source('New.kt', 'package dev.glasslauncher.net\nclass NewTest { @Test fun added() {} }')
        self.data = self.inventory(); self.save()
        self.assertEqual(2, len(self.expected()['Development']))
        self.results()
        self.assertEqual(3, self.check_results())

    def test_multiple_classes_helpers_and_bc_roles(self):
        self.source('Core.kt', 'package dev.glasslauncher.net\nclass CoreTest { @Test fun core() {} }\nclass Helper {}')
        self.source('Other.kt', 'package other\nclass VisualTest { @Test fun visual() {} }\nclass DeviceTest { @Test fun connected() {} }')
        self.data = self.inventory()
        e = next(e for e in self.data['sources'] if e['path'].endswith('Other.kt'))
        e['classes'][1]['role'] = 'C'; e['classes'][1]['reason'] = 'Requires a device; excluded'
        self.save()
        self.assertEqual(1, len(self.expected()['Development']))
        self.assertIn('Helper', next(e for e in self.data['sources'] if e['path'].endswith('Core.kt'))['facts']['declarations'])

    def test_conflicting_roles_and_duplicate_variants_rejected(self):
        original = copy.deepcopy(self.data)
        for role, variant in [('C','Development'), ('A','Original'), ('A',None), ('unknown','Development')]:
            self.data = copy.deepcopy(original)
            c = next(c for e in self.data['sources'] for c in e['classes'] if c['name']==self.dev)
            c.update(role=role,variant=variant); self.save()
            with self.subTest(role=role,variant=variant), self.assertRaises(ci.Rejected): self.expected()
        self.data = copy.deepcopy(original)
        next(e for e in self.data['sources'] if e['path'].endswith('Core.kt'))['role'] = 'C'
        self.save()
        with self.assertRaises(ci.Rejected): self.expected()
        self.data = original; self.save()
        with patch.object(ci, 'FILTERS', {'Development': ('dev.glasslauncher.net.*',), 'Original': (self.orig, self.dev)}):
            with self.assertRaises(ci.Rejected): self.expected()

    def test_valid_additional_methods_and_parameter_cases(self):
        self.source('Core.kt', 'package dev.glasslauncher.net\nclass CoreTest { @Test fun core() {} @Test fun extra() {} }')
        self.data = self.inventory(); self.save()
        path = self.results()
        s = path.read_text().replace('tests="2"', 'tests="3"').replace('name="extra"', 'name="extra[0]"')
        s = s.replace('</testsuite>', f'<testcase classname="{self.dev}" name="extra[1]"/></testsuite>')
        path.write_text(s)
        self.assertEqual(4, self.check_results())

    def test_failure_error_skip_and_hidden_failure_rejected(self):
        for field in ('failure','error','skipped'):
            path = self.results()
            plural = {'failure':'failures','error':'errors','skipped':'skipped'}[field]
            path.write_text(path.read_text().replace(f'{plural}="0"',f'{plural}="1"'))
            with self.subTest(field=field), self.assertRaises(ci.Rejected): self.check_results()
            path = self.results()
            path.write_text(path.read_text().replace('/>',f'><{field}/></testcase>'))
            with self.subTest(hidden=field), self.assertRaises(ci.Rejected): self.check_results()

    def test_missing_invalid_stale_wrong_variant_and_incomplete_xml(self):
        for condition in ('missing','invalid','stale','wrong-variant','no-cases','missing-method','duplicate'):
            path = self.results()
            if condition=='missing': path.unlink()
            elif condition=='invalid': path.write_text('<broken')
            elif condition=='stale': os.utime(path,ns=(1,1))
            elif condition=='wrong-variant': path.write_text(path.read_text().replace(self.dev,self.orig))
            elif condition=='no-cases': path.write_text(path.read_text().replace(f'<testcase classname="{self.dev}" name="core"/>',''))
            elif condition=='missing-method': path.write_text(path.read_text().replace('name="core"','name="other"'))
            else: (path.parent / 'TEST-duplicate.xml').write_text(path.read_text())
            reports=self.root/'reports';reports.mkdir(exist_ok=True)
            with self.subTest(condition=condition), self.assertRaises(ci.Rejected): ci.jvm_results(self.root,reports,2 if condition=='stale' else 0)
            (path.parent/'TEST-duplicate.xml').unlink(missing_ok=True)

    def test_inventory_failure_prevents_pipeline_approval(self):
        PipelineFailureTests().run_pipeline(inventory_error=True)

    def test_scanner_ignores_comments_strings_and_rejects_ambiguous_mandatory(self):
        self.source('Core.kt', 'package dev.glasslauncher.net\n/* class Fake { @Test fun fake() {} } */\nclass CoreTest { val text = "class Fake {}"; @Test fun core() {} }')
        self.data=self.inventory();self.save()
        self.assertEqual(['core'],self.expected()['Development'][self.dev])
        self.source('Core.kt', 'package dev.glasslauncher.net\nclass CoreTest { class Nested { @Test fun nested() {} } }')
        self.data=self.inventory();self.save()
        with self.assertRaises(ci.Rejected): self.expected()

class ModuleStructureTests(unittest.TestCase):
    """Actual isolated Settings/source files; no Gradle evaluation or SDK required."""
    def setUp(self):
        self.case = JvmScopeTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.root = self.case.root
        self.settings = self.root / 'settings.gradle.kts'

    def add_module(self, with_source=False):
        module = self.root / 'feature'
        module.mkdir()
        self.settings.write_text(self.settings.read_text() + 'include(":feature")\n')
        if with_source:
            p = module / 'src/test/java/FeatureTest.kt'
            p.parent.mkdir(parents=True)
            p.write_text('package dev.extra\nclass FeatureTest { @Test fun feature() {} }')

    def test_approved_settings_structure_passes(self):
        self.assertEqual(['app'], ci.settings_projects(self.root, self.case.data['module_structure']))
        self.case.expected()

    def test_new_registered_module_without_sources_is_rejected(self):
        self.add_module()
        with self.assertRaisesRegex(ci.Rejected, 'settings.gradle.kts changed'):
            self.case.expected()

    def test_new_registered_module_with_kotlin_test_is_rejected(self):
        self.add_module(with_source=True)
        with self.assertRaisesRegex(ci.Rejected, 'settings.gradle.kts changed'):
            self.case.expected()

    def test_removed_renamed_or_differently_bound_module_is_rejected(self):
        for content in ('', 'include(":renamed")\n', 'include(":app")\nproject(":app").projectDir = file("other")\n'):
            self.settings.write_text(content)
            with self.subTest(content=content), self.assertRaises(ci.Rejected):
                self.case.expected()

    def test_missing_corrupt_or_contradictory_module_metadata_is_rejected(self):
        original = copy.deepcopy(self.case.data)
        for data in (None, {}, [], {'path':'other'},
                     {**original['module_structure'], 'sha256':'broken'},
                     {**original['module_structure'], 'projects':['app','unknown']},
                     {**original['module_structure'], 'projects':['app','app']}):
            self.case.data = copy.deepcopy(original)
            if data is None: del self.case.data['module_structure']
            else: self.case.data['module_structure'] = data
            self.case.save()
            with self.subTest(metadata=data), self.assertRaises(ci.Rejected): self.case.expected()

    def test_deliberate_complete_module_and_source_review_can_pass(self):
        self.add_module(with_source=True)
        self.case.data['module_structure']['sha256'] = ci.digest(self.settings)
        self.case.save()
        with self.assertRaisesRegex(ci.Rejected, 'contradict'): self.case.expected()
        self.case.data['module_structure']['projects'] = ['app','feature']
        self.case.save()
        with self.assertRaisesRegex(ci.Rejected, 'Unclassified'): self.case.expected()
        self.case.data = self.case.inventory()
        self.case.save()
        self.case.expected()
        entry = next(e for e in self.case.data['sources'] if e['path'].startswith('feature/'))
        self.assertEqual('B', entry['role'])
        self.assertEqual('dev.extra.FeatureTest', entry['classes'][0]['name'])

    def test_unparseable_settings_or_missing_module_directory_fails_closed(self):
        original = self.case.data['module_structure']
        for text in ('include(modules)\n', 'if (true) { include(":app") }',
                     'include(":app")\nincludeBuild("other")',
                     'include(":app", ":app")', 'include(":nested:child")'):
            self.settings.write_text(text)
            metadata = {**original, 'sha256':ci.digest(self.settings)}
            with self.subTest(text=text), self.assertRaises(ci.Rejected): ci.settings_projects(self.root,metadata)
        self.settings.write_text('include(":missing")')
        metadata = {**original, 'sha256':ci.digest(self.settings), 'projects':['missing']}
        with self.assertRaisesRegex(ci.Rejected,'Missing, renamed'): ci.settings_projects(self.root,metadata)
        self.settings.unlink()
        with self.assertRaises(ci.Rejected): self.case.expected()

    def test_actual_settings_rejection_prevents_ci_approval(self):
        self.add_module(with_source=True)
        validator = ci.validate_inventory
        PipelineFailureTests().run_pipeline(inventory_check=lambda *_: validator(self.root))


class DiagnosticRunTests(unittest.TestCase):
    """Real output directories/protocol files. Pipeline subprocesses remain simulated."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output_protocol = self.root / 'step-output'

    def outputs(self):
        return dict(line.split('=',1) for line in self.output_protocol.read_text().splitlines())

    def fail_early(self, target, attempt='1'):
        with patch.dict(os.environ, {'GITHUB_OUTPUT':str(self.output_protocol),
                'GITHUB_REF':'refs/tags/v-fixture', 'GITHUB_RUN_ID':'123',
                'GITHUB_RUN_ATTEMPT':attempt, 'GITHUB_SHA':'b'*40}, clear=True), \
             patch.object(ci.subprocess, 'check_output', side_effect=['b'*40+'\n',b'']):
            return ci.execute(target,self.root)

    def test_fresh_run_has_current_identity_and_rejected_summary(self):
        target=self.root/'first'
        self.assertEqual(1,self.fail_early(target))
        run=json.loads((target/'reports/run.json').read_text())
        summary=json.loads((target/'reports/summary.json').read_text())
        self.assertEqual('123',run['github_run_id'])
        self.assertEqual(run['id'],summary['diagnostic_run']['id'])
        self.assertEqual('rejected',summary['status'])
        self.assertEqual({},summary['checks'])
        self.assertEqual(str(target/'reports'),self.outputs()['reports_dir'])
        self.assertEqual('false',self.outputs()['apk_ready'])
        self.assertFalse((target/'approved').exists())

    def test_repeated_existing_directory_resets_flags_without_touching_foreign_data(self):
        target=self.root/'old'
        self.fail_early(target)
        marker=target/'reports/foreign.txt';marker.write_text('KEEP')
        before={str(p):p.read_bytes() for p in target.rglob('*') if p.is_file()}
        with self.assertRaises(ci.Rejected): self.fail_early(target,'2')
        self.assertEqual(before,{str(p):p.read_bytes() for p in target.rglob('*') if p.is_file()})
        self.assertEqual('false',self.outputs()['diagnostics_ready'])
        self.assertEqual('',self.outputs()['reports_dir'])
        self.assertEqual('false',self.outputs()['apk_ready'])

    def test_new_attempt_cannot_offer_previous_reports(self):
        first=self.root/'attempt1';second=self.root/'attempt2'
        self.fail_early(first)
        (first/'reports/old-only.txt').write_text('old')
        self.fail_early(second,'2')
        a=json.loads((first/'reports/run.json').read_text());b=json.loads((second/'reports/run.json').read_text())
        self.assertNotEqual(a['id'],b['id'])
        self.assertEqual('2',b['github_run_attempt'])
        self.assertEqual(str(second/'reports'),self.outputs()['reports_dir'])
        self.assertFalse((second/'reports/old-only.txt').exists())
        self.assertTrue((first/'reports/old-only.txt').is_file())

    def test_failure_before_directory_creation_does_not_offer_reports(self):
        with patch.object(Path,'mkdir',side_effect=OSError('controlled permissions error')):
            with self.assertRaises(OSError): self.fail_early(self.root/'failed')
        self.assertEqual('false',self.outputs()['diagnostics_ready'])
        self.assertEqual('',self.outputs()['reports_dir'])

    def test_missing_current_marker_never_offers_old_reports(self):
        original=Path.write_text
        def write(path,*args,**kwargs):
            if path.name=='run.json':raise OSError('controlled marker write failure')
            return original(path,*args,**kwargs)
        with patch.object(Path,'write_text',new=write):
            with self.assertRaises(OSError): self.fail_early(self.root/'incomplete')
        self.assertEqual('false',self.outputs()['diagnostics_ready'])
        self.assertEqual('',self.outputs()['reports_dir'])
        self.assertFalse((self.root/'incomplete/approved').exists())

    def test_later_mandatory_errors_leave_only_current_diagnostics(self):
        for failed in ('gradle','size'):
            PipelineFailureTests().run_pipeline(failed=failed)

    def test_successful_simulated_run_preserves_approval_gate(self):
        PipelineFailureTests().run_pipeline()

    def test_workflow_uses_current_step_paths_and_attempt_names(self):
        workflow=(Path(__file__).resolve().parents[2]/'.github/workflows/build.yml').read_text()
        self.assertIn('mktemp -d "$RUNNER_TEMP/tvhome-m1-ci.XXXXXXXX"',workflow)
        self.assertIn("always() && steps.verify.outputs.diagnostics_ready == 'true'",workflow)
        self.assertIn("success() && steps.verify.outputs.apk_ready == 'true'",workflow)
        self.assertIn('path: ${{ steps.verify.outputs.reports_dir }}/',workflow)
        self.assertIn('path: ${{ steps.verify.outputs.output_dir }}/approved/',workflow)
        self.assertEqual(2,workflow.count('if-no-files-found: error'))
        self.assertEqual(2,workflow.count('${{ github.run_id }}-${{ github.run_attempt }}'))
        self.assertNotIn('path: ${{ runner.temp }}/tvhome-m1-ci/',workflow)


if __name__ == '__main__':
    unittest.main()
