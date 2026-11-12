"""Size/CLI regressions. SDK manifest/signature responses are explicitly simulated."""
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch
import zipfile

SPEC = importlib.util.spec_from_file_location('size_report', Path(__file__).resolve().parents[1] / 'apk-size-report.py')
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


class SizeReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.apk = self.root / 'explicit.apk'
        with zipfile.ZipFile(self.apk,'w',zipfile.ZIP_DEFLATED) as z:
            for n,b in {'AndroidManifest.xml':b'fixture manifest', 'classes.dex':b'dex bytes'*10,
                        'lib/arm64-v8a/libx.so':b'native', 'lib/x86/libx.so':b'x86',
                        'resources.arsc':b'resources', 'res/raw/a':b'r', 'assets/data':b'a'}.items():
                z.writestr(n,b)
        self.xml = '<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="io.example.dev" android:versionName="1-dev" android:versionCode="7"><application android:debuggable="true"/></manifest>'
        self.manifest = m.gate.parse_manifest(self.xml)
        self.signer = m.gate.Signer('CN=Android Debug,O=Fixture', 'a'*64)
        self.tools = m.gate.SdkTools(Path(__file__),Path(__file__))
        p=patch.object(m.gate,'sdk_tools',return_value=self.tools);p.start();self.addCleanup(p.stop)
        p=patch.object(m.gate,'inspect_apk',side_effect=lambda p,t:(self.manifest,(self.signer,),m.gate.file_hash(p)));p.start();self.addCleanup(p.stop)

    def report(self, **kwargs): return m.profile(self.apk, **kwargs)
    def cli(self, *args):
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            return m.main([str(self.apk),'--output',str(self.root/'out.json'),*args])

    def test_valid_consistent_simulated_apk_profile(self):
        d=self.report();self.assertEqual('io.example.dev',d['apk_observed']['application_id'])
        self.assertEqual(7,d['apk_observed']['version_code'])
        self.assertEqual('a'*64,d['apk_observed']['signatures'][0]['certificate_sha256'])

    def test_exact_sizes_and_percentages(self):
        d=self.report();s=d['size_profile'];total=sum(g['zip_bytes'] for g in s['groups'].values())+s['container_alignment_signature_overhead']['bytes']
        self.assertEqual(self.apk.stat().st_size,total)
        perc=sum(g['apk_percent'] for g in s['groups'].values())+s['container_alignment_signature_overhead']['apk_percent']
        self.assertAlmostEqual(100,perc,places=6)
        self.assertEqual(90,s['groups']['dex']['uncompressed_bytes'])

    def test_dex_native_abi_resource_and_asset_classification(self):
        s=self.report()['size_profile'];self.assertEqual(1,s['groups']['dex']['file_count'])
        self.assertEqual(2,s['groups']['native']['file_count'])
        self.assertEqual({'arm64-v8a','x86'},set(s['abi_distribution']))
        self.assertEqual(2,s['groups']['resources']['file_count']);self.assertEqual(1,s['groups']['assets']['file_count'])

    def test_reproducible_schema_without_timestamp_or_baseline(self):
        self.assertEqual(json.dumps(self.report(),sort_keys=True),json.dumps(self.report(),sort_keys=True))
        self.assertEqual(m.FORMAT,self.report()['schema'])
        self.assertTrue(all(v is False for v in self.report()['approval'].values()))

    def test_missing_apk_does_not_fallback(self):
        self.apk.unlink();self.assertEqual(2,self.cli());self.assertFalse((self.root/'out.json').exists())

    def test_damaged_zip_rejected(self):
        self.apk.write_bytes(b'not zip');self.assertEqual(2,self.cli())

    def test_invalid_or_unreliable_signature_rejected(self):
        with patch.object(m.gate,'inspect_apk',side_effect=m.gate.InspectionError('signature rejected')):
            self.assertEqual(2,self.cli())
        self.assertFalse((self.root/'out.json').exists())

    def test_missing_sdk_tools_rejected(self):
        with patch.object(m.gate,'sdk_tools',side_effect=m.gate.InspectionError('missing SDK')):
            self.assertEqual(2,self.cli())

    def test_inconsistent_manifest_metadata_rejected_by_reused_parser(self):
        def invalid(*_): return m.gate.parse_manifest('<manifest/>')
        with patch.object(m.gate,'inspect_apk',side_effect=invalid):self.assertEqual(2,self.cli())

    def test_file_changed_during_inspection_rejected(self):
        def mutate(path,tools):
            old=m.gate.file_hash(path);path.write_bytes(path.read_bytes()+b'changed')
            return self.manifest,(self.signer,),old
        with patch.object(m.gate,'inspect_apk',side_effect=mutate):self.assertEqual(2,self.cli())
        self.assertFalse((self.root/'out.json').exists())

    def test_missing_provenance_is_explicitly_unknown(self):
        d=self.report();self.assertEqual('unavailable',d['external_provenance']['state'])
        self.assertIsNone(d['comparison_context']['gradle_variant'])
        self.assertFalse(d['approval']['baseline_approved'])

    def test_external_provenance_not_promoted_to_verified_build(self):
        p=self.root/'claims.json';p.write_text(json.dumps({'apk_sha256':m.gate.file_hash(self.apk),'variant':'developmentDebug','source_reference':'M0 HEAD; dirty M1'}))
        d=self.report(provenance=p);self.assertIn('not_independently_verified',d['external_provenance']['state'])
        self.assertIsNone(d['comparison_context']['gradle_variant'])

    def test_missing_malformed_and_contradictory_provenance_fails(self):
        p=self.root/'claims.json'
        for data in (None,'broken',json.dumps({'apk_sha256':'0'*64}),json.dumps({'apk_sha256':m.gate.file_hash(self.apk),'application_id':'wrong'}),json.dumps({'apk_sha256':m.gate.file_hash(self.apk),'version_code':8})):
            if data is not None:p.write_text(data)
            else:p.unlink(missing_ok=True)
            with self.subTest(data=data):self.assertEqual(2,self.cli('--provenance',str(p)))

    def test_project_config_is_observed_but_not_apk_source_proof(self):
        for rel in m.CONFIGS:
            p=self.root/'project'/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('fixture configuration')
        d=self.report(project=self.root/'project');self.assertEqual('project_files_observed',d['project_evidence']['state'])
        self.assertFalse(d['project_evidence']['complete_source_tree_identified'])
        self.assertEqual('unverified',d['project_evidence']['apk_source_association'])

    def test_successful_json_write_never_changes_apk(self):
        before=m.gate.file_hash(self.apk);self.assertEqual(0,self.cli())
        self.assertEqual(before,m.gate.file_hash(self.apk));self.assertEqual(m.FORMAT,json.loads((self.root/'out.json').read_text())['schema'])
        self.assertEqual(2,self.cli()) # Existing report never overwritten.

    def test_output_cannot_alias_apk_or_hardlink(self):
        before=m.gate.file_hash(self.apk)
        with redirect_stderr(io.StringIO()):self.assertEqual(2,m.main([str(self.apk),'--output',str(self.apk)]))
        link=self.root/'out.json';link.hardlink_to(self.apk)
        self.assertEqual(2,self.cli());self.assertEqual(before,m.gate.file_hash(self.apk))

    def test_duplicate_zip_names_are_rejected(self):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            with zipfile.ZipFile(self.apk,'a') as z:z.writestr('classes.dex',b'other')
        self.assertEqual(2,self.cli())


    def test_crc_corruption_rejected_with_simulated_signature(self):
        with zipfile.ZipFile(self.apk,'w',zipfile.ZIP_STORED) as z:
            z.writestr('AndroidManifest.xml',b'unique fixture payload')
        raw=self.apk.read_bytes().replace(b'unique fixture payload',b'broken fixture payload')
        self.apk.write_bytes(raw)
        self.assertEqual(2,self.cli())
        self.assertFalse((self.root/'out.json').exists())

    def test_change_after_profile_removes_new_report(self):
        original=m.profile
        def change(*args,**kwargs):
            result=original(*args,**kwargs)
            self.apk.write_bytes(self.apk.read_bytes()+b'controlled concurrent mutation')
            return result
        with patch.object(m,'profile',side_effect=change):self.assertEqual(2,self.cli())
        self.assertFalse((self.root/'out.json').exists())


if __name__=='__main__':unittest.main()
