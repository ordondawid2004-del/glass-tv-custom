"""Portable, SDK-free regressions for profile validation, trust and exclusive I/O."""
import copy
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('compare_size', Path(__file__).resolve().parents[1] / 'compare-apk-size-profile.py')
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def fixture(apk_sha='a' * 64, code=100):
    def group(z, u, count=1):
        return {'zip_bytes': z, 'uncompressed_bytes': u, 'file_count': count, 'apk_percent': z / 10}
    configs = {p: 'c' * 64 for p in m.CONFIGS}
    return {
        'schema': m.PROFILE_SCHEMA,
        'apk_observed': {'application_id': m.DEVELOPMENT_ID, 'version_name': '0.3.0-m1-dev',
                         'version_code': code, 'debuggable': True, 'test_only': False, 'file_bytes': 1000,
                         'sha256': apk_sha, 'file_type': 'APK/ZIP',
                         'validity': 'CRC, manifest and cryptographic signature verified',
                         'signatures': [{'certificate_sha256': 'e' * 64, 'certificate_dn': 'CN=Android Debug,O=Fixture',
                                         'android_debug_certificate': True}],
                         'embedded_build_metadata': {'state': 'apk_embedded_metadata', 'android_gradle_plugin_version': '9.4.1'}},
        'size_profile': {'groups': {'dex': group(300, 600), 'resources': group(200, 400),
                                    'native': group(100, 100), 'assets': group(100, 200), 'other': group(50, 100)},
                         'abi_distribution': {'arm64-v8a': group(100, 100)},
                         'container_alignment_signature_overhead': {'bytes': 250, 'apk_percent': 25,
                            'meaning': 'Residual APK bytes; container/alignment/signature not individually attributed'},
                         'total_uncompressed_entry_bytes': 1400},
        'project_evidence': {'state': 'project_files_observed', 'configuration_sha256': configs,
                             'configuration_set_sha256': sha(json.dumps(configs, sort_keys=True, separators=(',', ':')).encode()),
                             'apk_source_association': 'unverified', 'source_reference': None,
                             'complete_source_tree_identified': False, 'note': 'Fixture only; no source association'},
        'external_provenance': {'state': 'externally_supplied_not_independently_verified',
                                'values': {'apk_sha256': apk_sha, 'application_id': m.DEVELOPMENT_ID, 'version_code': code,
                                           'variant': 'developmentDebug', 'source_reference': 'f' * 64,
                                           'build_recipe': './gradlew :app:assembleDevelopmentDebug',
                                           'build_toolchain': 'Java 21; Gradle 9.8.0; AGP 9.4.1; Kotlin 2.4.20',
                                           'dependencies_reference': 'd' * 64, 'packaging_reference': 'c' * 64},
                                'note': 'Unverified fixture claims'},
        'analysis_tools': {'python': '3.12.14', 'profile_script_sha256': 'c' * 64, 'm0_inspector_sha256': 'd' * 64,
                           'sdk_build_tools_directory': '37.0.0', 'note': 'Analysis launchers, not build toolchain',
                           'sdk_tools': {n: {'path': '/fixture/' + n, 'launcher_sha256': 'b' * 64} for n in ('apkanalyzer', 'apksigner')}},
        'comparison_context': {'gradle_variant': None, 'build_type': None, 'build_recipe': None,
                               'build_toolchain': None, 'dependencies': None, 'packaging': None,
                               'state': 'unavailable_from_apk_alone', 'requirements': ['variant', 'toolchain', 'metadata', 'approval']},
        'approval': {'baseline_approved': False, 'production_approved': False, 'regression_gate_executed': False}}


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.reference, self.candidate, self.output = [self.root / p for p in ('ref.json', 'candidate.json', 'output.json')]
        self.a, self.b = fixture(), fixture('b' * 64, 101)
        self.save()

    def save(self):
        self.reference.write_text(json.dumps(self.a), encoding='utf-8')
        self.candidate.write_text(json.dumps(self.b), encoding='utf-8')
        self.pin = sha(self.reference.read_bytes())

    def cli(self, pin=None, output=None):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return m.main(['--reference', str(self.reference), '--candidate', str(self.candidate),
                           '--reference-sha256', self.pin if pin is None else pin,
                           '--output', str(output or self.output)])

    def report(self):
        self.save()
        self.assertEqual(m.ANALYSIS_ONLY, self.cli())
        return json.loads(self.output.read_text())

    def rejected(self):
        self.save()
        self.assertEqual(2, self.cli())
        self.assertFalse(self.output.exists())

    def test_valid_equal_sizes(self):
        d = self.report()
        self.assertEqual(0, d['sizes']['delta_bytes'])
        self.assertEqual(0, d['sizes']['delta_percent_of_reference'])
        self.assertEqual('matching_recipe_same_source_claim', d['comparability']['status'])
        self.assertEqual(self.pin, d['inputs']['reference_profile_sha256'])
        self.assertEqual(sha(self.candidate.read_bytes()), d['inputs']['candidate_profile_sha256'])

    def test_variable_version_codes_are_metadata(self):
        d = self.report()
        self.assertEqual(1, d['metadata']['version_code_delta'])
        self.assertIn('Variable build metadata', d['metadata']['version_code_interpretation'])
        self.assertFalse(d['comparability']['reasons'])

    def test_larger_candidate(self):
        self.b['apk_observed']['file_bytes'] += 50
        self.b['size_profile']['container_alignment_signature_overhead']['bytes'] += 50
        self.repercent(self.b)
        d = self.report()
        self.assertEqual(50, d['sizes']['delta_bytes'])
        self.assertEqual(5, d['sizes']['delta_percent_of_reference'])
        self.assertEqual(50, d['sizes']['container_delta_bytes'])

    def test_smaller_candidate(self):
        self.b['apk_observed']['file_bytes'] -= 50
        self.b['size_profile']['container_alignment_signature_overhead']['bytes'] -= 50
        self.repercent(self.b)
        d = self.report()
        self.assertEqual(-50, d['sizes']['delta_bytes'])
        self.assertEqual(-5, d['sizes']['delta_percent_of_reference'])

    @staticmethod
    def repercent(p):
        total = p['apk_observed']['file_bytes']
        for g in [*p['size_profile']['groups'].values(), *p['size_profile']['abi_distribution'].values()]:
            g['apk_percent'] = round(g['zip_bytes'] * 100 / total, 8)
        h = p['size_profile']['container_alignment_signature_overhead']
        h['apk_percent'] = round(h['bytes'] * 100 / total, 8)

    def test_changed_source_is_informative_not_automatic_rejection(self):
        self.b['external_provenance']['values']['source_reference'] = '1' * 64
        d = self.report()
        self.assertEqual('matching_recipe_changed_source_claim', d['comparability']['status'])
        self.assertEqual('different_claim', d['metadata']['source_reference_relation'])
        self.assertFalse(d['provenance_verification']['build_source_verified'])

    def test_different_variant(self):
        self.b['external_provenance']['values']['variant'] = 'originalDebug'
        self.assertEqual('incompatible_documentation', self.report()['comparability']['status'])

    def test_original_identity_cannot_be_development_comparison(self):
        self.b['apk_observed']['application_id'] = 'dev.glasslauncher'
        self.b['external_provenance']['values']['application_id'] = 'dev.glasslauncher'
        self.assertEqual('incompatible_documentation', self.report()['comparability']['status'])

    def test_contradictory_application_id_is_invalid(self):
        self.b['external_provenance']['values']['application_id'] = 'dev.glasslauncher'
        self.rejected()

    def test_different_packaging_claim(self):
        self.b['external_provenance']['values']['packaging_reference'] = '1' * 64
        self.assertEqual('incompatible_documentation', self.report()['comparability']['status'])

    def test_observed_configuration_change_cannot_hide_behind_same_claim(self):
        p = self.b['project_evidence']
        p['configuration_sha256']['app/build.gradle.kts'] = '1' * 64
        p['configuration_set_sha256'] = sha(json.dumps(p['configuration_sha256'], sort_keys=True, separators=(',', ':')).encode())
        self.assertIn('app/build.gradle.kts', self.report()['metadata']['configuration_changes'])

    def test_invalid_configuration_set_digest(self):
        self.b['project_evidence']['configuration_set_sha256'] = '1' * 64
        self.rejected()

    def test_same_certificate_pin_with_different_dn_is_invalid(self):
        self.b['apk_observed']['signatures'][0]['certificate_dn'] = 'CN=Android Debug,O=Different'
        self.rejected()

    def test_debug_certificate_flag_contradiction(self):
        self.b['apk_observed']['signatures'][0]['android_debug_certificate'] = False
        self.rejected()

    def test_different_signer_is_explicit_limitation(self):
        self.b['apk_observed']['signatures'][0]['certificate_sha256'] = '1' * 64
        d = self.report()
        self.assertFalse(d['metadata']['signer_fingerprints_equal'])
        self.assertEqual('incompatible_documentation', d['comparability']['status'])

    def test_duplicate_signers_rejected(self):
        self.b['apk_observed']['signatures'] *= 2
        self.rejected()

    def test_different_toolchain(self):
        self.b['external_provenance']['values']['build_toolchain'] = 'Java 25; another toolchain'
        self.assertEqual('incompatible_documentation', self.report()['comparability']['status'])

    def test_different_build_recipe(self):
        self.b['external_provenance']['values']['build_recipe'] += ' --offline'
        self.assertIn('Different documented build_recipe', self.report()['comparability']['reasons'])

    def test_missing_project_evidence_is_explicitly_insufficient(self):
        self.b['project_evidence'] = {'state': 'unavailable', 'apk_source_association': 'unverified'}
        self.assertIn('candidate.project_configuration', self.report()['comparability']['missing_information'])

    def test_missing_embedded_toolchain_metadata_is_not_guessed(self):
        self.b['apk_observed']['embedded_build_metadata'] = {'state': 'unavailable', 'android_gradle_plugin_version': None}
        self.assertIn('embedded_AGP', self.report()['comparability']['missing_information'])

    def test_invalid_utf8_is_rejected(self):
        self.candidate.write_bytes(b'\xff')
        self.assertEqual(2, self.cli())
        self.assertFalse(self.output.exists())

    def test_invalid_boolean_metadata(self):
        self.b['apk_observed']['debuggable'] = 1
        self.rejected()

    def test_oversize_uncompressed_profile_is_rejected(self):
        self.b['size_profile']['groups']['dex']['uncompressed_bytes'] = 2 * 1024**3
        self.b['size_profile']['total_uncompressed_entry_bytes'] = 2 * 1024**3 + 800
        self.rejected()

    def test_zero_count_group_cannot_contain_bytes(self):
        self.b['size_profile']['groups']['dex']['file_count'] = 0
        self.rejected()

    def test_embedded_agp_difference_is_not_hidden_by_equal_toolchain_claim(self):
        self.b['apk_observed']['embedded_build_metadata']['android_gradle_plugin_version'] = '9.5.0'
        self.assertIn('Different APK-reported AGP versions', self.report()['comparability']['reasons'])

    def test_unknown_toolchain(self):
        for p in (self.a, self.b):
            p['external_provenance']['values']['build_toolchain'] = 'unknown'
        self.assertIn('candidate.build_toolchain', self.report()['comparability']['missing_information'])

    def test_missing_external_provenance(self):
        self.b['external_provenance'] = {'state': 'unavailable', 'values': None}
        d = self.report()
        self.assertEqual('insufficient_documentation', d['comparability']['status'])
        self.assertIn('candidate.source_reference', d['comparability']['missing_information'])

    def test_unknown_source_is_not_reported_as_identical_source(self):
        for p in (self.a, self.b):
            p['external_provenance']['values']['source_reference'] = 'unknown'
        self.assertEqual('unknown', self.report()['metadata']['source_reference_relation'])

    def test_unknown_dependencies_are_not_reported_as_identical_dependencies(self):
        for p in (self.a, self.b):
            p['external_provenance']['values']['dependencies_reference'] = '?'
        self.assertEqual('unknown', self.report()['metadata']['dependency_reference_relation'])

    def test_missing_dependency_reference(self):
        del self.b['external_provenance']['values']['dependencies_reference']
        self.assertEqual('unknown', self.report()['metadata']['dependency_reference_relation'])

    def test_changed_dependency_reference(self):
        self.b['external_provenance']['values']['dependencies_reference'] = '1' * 64
        d = self.report()
        self.assertEqual('different_claim', d['metadata']['dependency_reference_relation'])
        self.assertEqual('incompatible_documentation', d['comparability']['status'])

    def test_missing_reference(self):
        self.reference.unlink()
        self.assertEqual(2, self.cli())
        self.assertFalse(self.output.exists())

    def test_wrong_reference_pin(self):
        self.assertEqual(2, self.cli(pin='0' * 64))
        self.assertFalse(self.output.exists())

    def test_reference_manipulation_even_whitespace_breaks_pin(self):
        self.reference.write_bytes(self.reference.read_bytes() + b'\n')
        self.assertEqual(2, self.cli())

    def test_malformed_pin(self):
        self.assertEqual(2, self.cli(pin='ABC'))

    def test_malformed_json(self):
        self.candidate.write_text('{broken')
        self.assertEqual(2, self.cli())

    def test_duplicate_json_key_is_not_silently_overwritten(self):
        raw = self.candidate.read_text().replace('"version_code": 101', '"version_code": 101, "version_code": 102', 1)
        self.candidate.write_text(raw)
        self.assertEqual(2, self.cli())

    def test_missing_and_unknown_schema_fields(self):
        mutations = [lambda p: p.pop('schema'), lambda p: p.update(schema='unknown/2'),
                     lambda p: p.update(unknown=True), lambda p: p['apk_observed'].pop('version_code'),
                     lambda p: p['size_profile']['groups'].pop('assets')]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.b = fixture('b' * 64, 101)
                mutate(self.b)
                self.rejected()

    def test_malformed_hash_fields(self):
        for key in ('sha256',):
            with self.subTest(key=key):
                self.b['apk_observed'][key] = 'not-a-hash'
                self.rejected()

    def test_claim_apk_hash_and_version_must_match(self):
        for key, value in [('apk_sha256', '1' * 64), ('version_code', 999)]:
            with self.subTest(key=key):
                self.b = fixture('b' * 64, 101)
                self.b['external_provenance']['values'][key] = value
                self.rejected()

    def test_bool_negative_fraction_and_oversize_are_not_size_integers(self):
        for value in (True, -1, 1.25, 2**80):
            with self.subTest(value=value):
                self.b = fixture('b' * 64, 101)
                self.b['size_profile']['groups']['dex']['zip_bytes'] = value
                self.rejected()

    def test_zero_reference_is_rejected_without_division(self):
        self.a['apk_observed']['file_bytes'] = 0
        self.rejected()

    def test_nan_infinity_and_float_overflow_are_invalid(self):
        for value in (float('nan'), float('inf'), -float('inf')):
            with self.subTest(value=value):
                self.b['size_profile']['groups']['dex']['apk_percent'] = value
                self.rejected()
        self.candidate.write_text(json.dumps(fixture('b' * 64, 101)).replace('"apk_percent": 30.0', '"apk_percent": 1e999', 1))
        self.assertEqual(2, self.cli())

    def test_group_container_sum_mismatch(self):
        self.b['size_profile']['container_alignment_signature_overhead']['bytes'] += 1
        self.rejected()

    def test_uncompressed_sum_mismatch(self):
        self.b['size_profile']['total_uncompressed_entry_bytes'] += 1
        self.rejected()

    def test_abi_sum_mismatch(self):
        self.b['size_profile']['abi_distribution']['arm64-v8a']['file_count'] += 1
        self.rejected()

    def test_percentage_mismatch(self):
        self.b['size_profile']['groups']['dex']['apk_percent'] += 1
        self.rejected()

    def test_changed_abi_is_reported_and_not_approved(self):
        native = self.b['size_profile']['abi_distribution'].pop('arm64-v8a')
        self.b['size_profile']['abi_distribution']['x86'] = native
        d = self.report()
        self.assertEqual('added', d['sizes']['abi_differences']['x86']['presence'])
        self.assertEqual('removed', d['sizes']['abi_differences']['arm64-v8a']['presence'])
        self.assertEqual('incompatible_documentation', d['comparability']['status'])

    def test_same_apk_hash_cannot_claim_different_bytes(self):
        self.b['apk_observed']['sha256'] = self.a['apk_observed']['sha256']
        self.b['external_provenance']['values']['apk_sha256'] = self.a['apk_observed']['sha256']
        self.rejected()  # versionCode still differs.

    def test_same_profile_can_be_compared_without_approval(self):
        self.b = copy.deepcopy(self.a)
        self.assertTrue(self.report()['metadata']['same_apk_hash'])

    def test_input_approval_or_trust_promotion_is_rejected(self):
        self.b['approval']['baseline_approved'] = True
        self.rejected()
        self.b = fixture('b' * 64, 101)
        self.b['external_provenance']['state'] = 'verified'
        self.rejected()
        self.b = fixture('b' * 64, 101)
        self.b['comparison_context']['build_recipe'] = 'claimed verified recipe'
        self.rejected()

    def test_inputs_stay_byte_identical(self):
        before = [p.read_bytes() for p in (self.reference, self.candidate)]
        self.assertEqual(m.ANALYSIS_ONLY, self.cli())
        self.assertEqual(before, [p.read_bytes() for p in (self.reference, self.candidate)])

    def test_existing_output_is_not_overwritten(self):
        self.output.write_text('keep exactly')
        self.assertEqual(2, self.cli())
        self.assertEqual('keep exactly', self.output.read_text())

    def test_output_alias_and_hardlink_are_not_modified(self):
        before = self.reference.read_bytes()
        self.assertEqual(2, self.cli(output=self.reference))
        self.output.hardlink_to(self.reference)
        self.assertEqual(2, self.cli())
        self.assertEqual(before, self.reference.read_bytes())

    def test_input_and_output_symlinks_are_rejected(self):
        self.output.symlink_to(self.root / 'missing')
        self.assertEqual(2, self.cli())
        self.assertFalse((self.root / 'missing').exists())
        self.output.unlink()
        self.candidate.unlink()
        self.candidate.symlink_to(self.reference)
        self.assertEqual(2, self.cli())

    def test_changed_input_removes_only_new_report(self):
        original = m.unchanged
        calls = []
        def change(path, expected, stamp):
            calls.append(path)
            if len(calls) == 3:
                self.candidate.write_bytes(self.candidate.read_bytes() + b' ')
            return original(path, expected, stamp)
        with patch.object(m, 'unchanged', side_effect=change):
            self.assertEqual(2, self.cli())
        self.assertFalse(self.output.exists())

    def test_missing_output_directory_is_error(self):
        self.assertEqual(2, self.cli(output=self.root / 'absent/output.json'))

    def test_oversized_profile_is_rejected(self):
        self.candidate.write_bytes(b' ' * (m.MAX_INPUT_BYTES + 1))
        self.assertEqual(2, self.cli())

    def test_nonregular_input_is_rejected_without_blocking(self):
        self.candidate.unlink()
        os.mkfifo(self.candidate)
        self.assertEqual(2, self.cli())

    def test_report_never_grants_approval_or_tolerance(self):
        d = self.report()
        self.assertNotIn('apk_ready', json.dumps(d))
        self.assertIsNone(d['restrictions']['growth_tolerance_bytes'])
        self.assertTrue(all(v is False for k, v in d['restrictions'].items() if k != 'growth_tolerance_bytes'))
        self.assertFalse(d['provenance_verification']['apk_signatures_reverified'])
        self.assertFalse(d['provenance_verification']['build_source_verified'])

    def test_actual_cli_process_uses_no_success_exit_or_approval_channel(self):
        result = subprocess.run([sys.executable, '-B', str(Path(m.__file__)), '--reference', str(self.reference),
                                 '--candidate', str(self.candidate), '--reference-sha256', self.pin,
                                 '--output', str(self.output)], capture_output=True, text=True, timeout=10,
                                env={**os.environ, 'GITHUB_OUTPUT': str(self.root / 'github-output')})
        self.assertEqual(3, result.returncode, result.stderr)
        self.assertIn('Keine CI-', result.stdout)
        self.assertFalse((self.root / 'github-output').exists())


if __name__ == '__main__':
    unittest.main()
