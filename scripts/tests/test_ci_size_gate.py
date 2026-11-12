"""28 original contract cases plus 12 M1.4.5.1 regressions. GitHub, certificate and build evidence are SIMULATED.

These tests do not establish real Actions origin or perform APK cryptography;
the unchanged opt-in M0 integration suite supplies separate SDK coverage.
"""
import base64
import copy
import datetime as dt
import importlib.util
import json
import io
import zipfile
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


def module(filename, name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / filename)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value


m = module('ci-size-gate.py', 'contract_under_test')
ci = module('ci-development.py', 'contract_ci_under_test')
profile_fixtures = module('tests/test_compare_apk_size_profile.py', 'comparison_fixture_builder')


class ContractTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        self.dir = Path(folder.name); self.apk = self.dir / 'candidate.apk'; self.apk.write_bytes(b'a' * 1000)
        self.pin_der = b'PUBLIC SIMULATED CERTIFICATE, NOT A KEY'
        self.pin = m.sha(self.pin_der)
        self.certificate = {'key_type': 'RSA', 'key_bits': 2048, 'dn': 'CN=Android Debug,O=TV Home CI Development,C=DE',
                            'validity_days': 30, 'signature_algorithm': 'SHA256withRSA'}
        signing_policy = {'certificate_profile': self.certificate, 'signature_schemes': ['v2', 'v3']}
        self.platform = {'repository_id': 123, 'event': 'push', 'ref': 'refs/heads/master',
                         'workflow_path': '.github/workflows/build.yml', 'workflow_commit': 'a'*40,
                         'run_id': 200, 'run_attempt': 1, 'job_id': 'development-contract', 'source_commit': 'a'*40}
        self.recipe = {'schema': 'tvhome.size-recipe/1', 'variant': 'developmentDebug', 'application_id': m.APP,
                       'tasks': [':app:assembleDevelopmentDebug', ':app:lintDevelopmentDebug', ':app:testDevelopmentDebugUnitTest',
                                 ':app:testOriginalDebugUnitTest', ':app:writeDevelopmentSizeInputs'],
                       'gradle_arguments': ['--dependency-verification=strict', '--no-build-cache', '--rerun-tasks', '--no-configuration-cache'],
                       'engine_manifest_sha256': 'c'*64, 'init_script_sha256': 'd'*64, 'configuration_files': [],
                       'environment': {'LANG': 'C.UTF-8', 'TZ': 'UTC'}, 'sdk_levels': {'compile': 37, 'min': 28, 'target': 36},
                       'packaging': {'minify': False, 'shrink_resources': False, 'dex_compressed': True, 'native_compressed': False,
                                     'abis': ['arm64-v8a','armeabi-v7a','x86','x86_64'], 'excludes': []},
                       'signing_policy': signing_policy, 'version_policy': 'build-minute-since-2026-01-01'}
        self.toolchain = {'schema': 'tvhome.size-toolchain/1', 'runner': {'os': 'Linux', 'image_os': 'ubuntu24', 'image_version': 'fixture-image', 'architecture': 'X64'},
                          'versions': dict(java='21.0.13+9', python='3.12.14', gradle='9.8.0', kotlin_plugin='2.4.20', agp='9.4.1',
                                           d8_r8='fixture', sdk_tools='fixture', build_tools='37.0.0', platform='37'),
                          'distributions': {'jdk': 'a'*64}, 'actions': {'checkout': 'c'*40}, 'files': []}
        self.source = {'schema': 'tvhome.size-source-inputs/1', 'repository_id': 123, 'commit':'a'*40,'git_tree':'d'*40,
                       'files': [],'injected_inputs': [],'effective_inputs_sha256': m.sha(m.canonical({'files':[],'injected_inputs':[]}))}
        self.artifact = self.dir / 'artifact.jar'; self.artifact.write_bytes(b'ACTUAL FIXTURE ARTIFACT BYTES')
        self.dependencies = {'schema': 'tvhome.size-dependency-artifacts/1', 'artifacts': [
            {'component':'fixture:module:1.0','artifact':'artifact.jar','roles':sorted(m.ROLES),'configurations':['fixture'],
             'bytes':self.artifact.stat().st_size,'sha256':m.file_sha(self.artifact)}],
            'verification_metadata_sha256':'a'*64,'strict_verification':True,'coverage':{r:True for r in m.ROLES}}
        self.verification = self.dir / 'verification.xml'
        self.verification.write_text('<verification-metadata xmlns="https://schema.gradle.org/dependency-verification"><configuration><verify-metadata>true</verify-metadata></configuration><components><component group="fixture" name="module" version="1.0"><artifact name="artifact.jar"><sha256 value="'+m.file_sha(self.artifact)+'"/></artifact></component></components></verification-metadata>')
        self.dependencies['verification_metadata_sha256'] = m.file_sha(self.verification)
        self.paths = {('fixture:module:1.0','artifact.jar'):str(self.artifact)}
        self.observed = profile_fixtures.fixture(m.file_sha(self.apk), 101)
        self.refprofile = profile_fixtures.fixture('b'*64, 100)
        for p in (self.observed, self.refprofile):
            p['apk_observed']['signatures'][0].update(certificate_sha256=self.pin, certificate_dn=self.certificate['dn'])
            p['size_profile']['groups']['native']['file_count'] = 4
            p['size_profile']['abi_distribution'] = {abi:{'zip_bytes':25,'uncompressed_bytes':25,'file_count':1,'apk_percent':2.5} for abi in self.recipe['packaging']['abis']}
            p['external_provenance']['values'].update(source_reference=m.sha(m.canonical(self.source)),build_recipe=m.sha(m.canonical(self.recipe)),
                build_toolchain=m.sha(m.canonical(self.toolchain)),dependencies_reference=m.sha(m.canonical(self.dependencies)),packaging_reference=m.sha(m.canonical(self.recipe['packaging'])))
        self.bundle = {'profile.json': m.canonical(self.refprofile), 'source-manifest.json':m.canonical(self.source),
                       'dependency-artifacts.json':m.canonical(self.dependencies),'toolchain-manifest.json':m.canonical(self.toolchain),
                       'recipe.json':m.canonical(self.recipe),'verification-metadata.xml':self.verification.read_bytes()}
        reference_platform = {**self.platform,'event':'workflow_dispatch','workflow_path':'.github/workflows/size-reference.yml','run_id':100,'source_commit':'a'*40,'job_id':'capture-1'}
        self.reference = {'schema':'tvhome.development-size-reference/1','reference_id':'fixture-reference','policy_id':m.POLICY,
                          'repository_id':123,'source_commit':'a'*40,'engine_commit':'c'*40,'platform':reference_platform,'variant':'developmentDebug',
                          'application_id':m.APP,'apk_sha256':'b'*64,'profile_sha256':m.sha(self.bundle['profile.json']),'file_bytes':1000,
                          'source_sha256':m.sha(m.canonical(self.source)),'recipe_sha256':m.sha(m.canonical(self.recipe)),
                          'toolchain_sha256':m.sha(m.canonical(self.toolchain)),'dependencies_sha256':m.sha(m.canonical(self.dependencies)),
                          'selection':'larger_of_two_measurements_tie_first','files':[]}
        self.root = {'schema':'tvhome.size-trust-root/1','enabled':True,'repository':m.REPOSITORY,'repository_id':123,'engine_commit':'c'*40,
                     'engine_manifest_sha256':'c'*64,'reference_commit':'d'*40,'reference_manifest_path':'references/fixture/reference-manifest.json',
                     'reference_manifest_sha256':m.sha(m.canonical(self.reference)),'policy_id':m.POLICY}
        self.context = {'schema':'tvhome.size-gate-context/1','root_sha256':m.sha(m.canonical(self.root)), 'repository_id':123,
                        'engine_commit':'c'*40,'source_commit':'a'*40,'platform':self.platform,'reference_commit':'d'*40,
                        'reference_manifest_sha256':self.root['reference_manifest_sha256'],'policy_id':m.POLICY,'private_run_nonce':'1'*32}
        self.receipts = {name:m.canonical({'simulated':name}) for name in m.PRE}
        self.receipts.update({name:m.canonical({'tests':count,'failures':0,'errors':0,'skipped':0}) for name,count in m.PYTHON_COUNTS.items()})
        self.evidence = {'schema':'tvhome.size-build-evidence/1','mode':'candidate_before_size','platform':self.platform,
                         'source_sha256':m.sha(m.canonical(self.source)), 'recipe_sha256':m.sha(m.canonical(self.recipe)),
                         'toolchain_sha256':m.sha(m.canonical(self.toolchain)),'dependencies_sha256':m.sha(m.canonical(self.dependencies)),
                         'apk_sha256':m.file_sha(self.apk),'apk_bytes':1000,'profile_sha256':m.sha(m.canonical(self.observed)),
                         'started_at':'2026-01-01T01:41:00+00:00','finished_at':'2026-01-01T01:42:00+00:00','version_code':101,
                         'signing':{'expected_certificate_sha256':self.pin,'observed_certificate_sha256':self.pin,
                                    'public_certificate_der_base64':base64.b64encode(self.pin_der).decode(),'private_key_cleanup':True,**signing_policy},
                         'checks':{name:{'status':'passed','receipt_sha256':m.sha(raw),'platform':self.platform} for name,raw in self.receipts.items() if name in m.PRE}}

        self.fixture_certificates = {}; self.capture_pins = {}
        self.make_capture(1,1000); self.make_capture(2,999)
        self.select_capture(1); self.build_artifacts(); self.anchor_reference()

    def make_capture(self, i, size):
        """Synthetic capture with its own public DER, profile and evidence bytes."""
        platform = {**self.reference['platform'], 'job_id': f'capture-{i}'}
        der = self.pin_der if i == 1 else b'PUBLIC SIMULATED CAPTURE-2 CERTIFICATE'
        self.fixture_certificates[der] = copy.deepcopy(self.certificate)
        pin = m.sha(der); self.capture_pins[i] = pin
        profile = copy.deepcopy(self.refprofile)
        apk_hash = ('b' if i == 1 else '9') * 64
        profile['apk_observed'].update(sha256=apk_hash, file_bytes=size, version_code=100)
        profile['apk_observed']['signatures'][0].update(certificate_sha256=pin,certificate_dn=self.certificate['dn'])
        profile['size_profile']['container_alignment_signature_overhead']['bytes'] += size - 1000
        profile_fixtures.ComparisonTests.repercent(profile)
        profile['external_provenance']['values'].update(apk_sha256=apk_hash,version_code=100)
        self.bundle[f'capture-{i}-profile.json'] = m.canonical(profile)
        evidence = copy.deepcopy(self.evidence)
        evidence.update(mode='reference_capture',platform=platform,apk_sha256=apk_hash,apk_bytes=size,
                        profile_sha256=m.sha(m.canonical(profile)),version_code=100,
                        started_at='2026-01-01T01:40:00Z',finished_at='2026-01-01T01:40:30Z')
        evidence['signing'].update(expected_certificate_sha256=pin,observed_certificate_sha256=pin,
            public_certificate_der_base64=base64.b64encode(der).decode())
        evidence['checks']={name:{'status':'passed','receipt_sha256':m.sha(m.canonical({'simulated':name})),
                                 'platform':platform} for name in m.CAPTURE_PRE}
        for name,count in m.PYTHON_COUNTS.items():
            if name in evidence['checks']:
                evidence['checks'][name]['receipt_sha256']=m.sha(m.canonical({'tests':count,'failures':0,'errors':0,'skipped':0}))
        evidence['checks']['size']={'status':'not_applied_reference_capture','receipt_sha256':'c'*64,'platform':platform}
        self.bundle[f'capture-{i}-evidence.json']=m.canonical(evidence)
        measurement={'schema':'tvhome.size-reference-measurement/1','evidence_sha256':m.sha(m.canonical(evidence)),
          'profile_sha256':m.sha(m.canonical(profile)),'apk_sha256':apk_hash,'file_bytes':size,
          **{key:self.reference[key] for key in ('source_sha256','recipe_sha256','toolchain_sha256','dependencies_sha256')},
          'platform':platform,'release_budget_observation':{'exit_code':0,'limit_bytes':3563000}}
        self.bundle[f'measurement-{i}.json']=m.canonical(measurement)

    def select_capture(self, i):
        measurement=json.loads(self.bundle[f'measurement-{i}.json'])
        self.bundle['profile.json']=self.bundle[f'capture-{i}-profile.json']
        self.bundle['build-evidence.json']=self.bundle[f'capture-{i}-evidence.json']
        self.refprofile=json.loads(self.bundle['profile.json'])
        self.reference.update({key:measurement[key] for key in ('platform','apk_sha256','profile_sha256','file_bytes')})
        approval={'schema':'tvhome.size-reference-approval/1','reference_id':'fixture-reference',
          'reference_profile_sha256':self.reference['profile_sha256'],'reference_apk_sha256':self.reference['apk_sha256'],
          'file_bytes':self.reference['file_bytes'],'policy_id':m.POLICY,'approved_by':m.REPOSITORY.split('/')[0],
          'approved_at':'2026-01-01T01:40:50Z','operator_record':'SIMULATED OWNER REVIEW'}
        self.bundle['approval.json']=m.canonical(approval)

    def build_artifacts(self):
        """Freeze SIMULATED GitHub API digests separately from mutable bundle bytes."""
        self.api_artifacts=[]
        for i in (1,2):
            out=io.BytesIO()
            with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_STORED) as archive:
                for member,filename in [('candidate-profile.json',f'capture-{i}-profile.json'),
                                        ('build-evidence.json',f'capture-{i}-evidence.json'),('measurement.json',f'measurement-{i}.json')]:
                    archive.writestr(member,self.bundle[filename])
                evidence=json.loads(self.bundle[f'capture-{i}-evidence.json'])
                profile=json.loads(self.bundle[f'capture-{i}-profile.json'])
                summary={'status':'measurement_only','development_only':True,'production_approved':False,
                         'application_id':m.APP,'variant':'developmentDebug','expected_certificate_sha256':self.capture_pins[i],
                         'apk_sha256':profile['apk_observed']['sha256'],'apk_bytes':profile['apk_observed']['file_bytes'],
                         'version_code':profile['apk_observed']['version_code']}
                archive.writestr('summary.json',m.canonical(summary))
                for name,count in m.PYTHON_COUNTS.items():
                    archive.writestr(name+'.json',m.canonical({'tests':count,'failures':0,'errors':0,'skipped':0}))
            raw=out.getvalue();self.bundle[f'capture-{i}-artifact.zip']=raw
            self.api_artifacts.append({'id':1000+i,'name':f'size-reference-evidence-100-1-{i}','expired':False,
                'digest':'sha256:'+m.sha(raw),'size_in_bytes':len(raw),'created_at':'2026-01-01T01:40:31Z',
                'workflow_run':{'id':100,'repository_id':123,'head_repository_id':123,'head_branch':'master','head_sha':'a'*40}})

    def fixture_certificate(self, der):
        m.need(der in self.fixture_certificates,'Unknown simulated public certificate')
        return self.fixture_certificates[der]

    def anchor_reference(self):
        self.reference['files'] = sorted([{'path':k,'mode':'100644','bytes':len(v),'sha256':m.sha(v)} for k,v in self.bundle.items()],key=lambda e:e['path'])
        self.root['reference_manifest_sha256'] = m.sha(m.canonical(self.reference))
        self.context['reference_manifest_sha256'] = self.root['reference_manifest_sha256']
        self.context['root_sha256'] = m.sha(m.canonical(self.root))

    def simulated_api(self):
        outer=self
        class Api:
            def blob(self,commit,path):
                m.need(commit=='d'*40,'Wrong pinned reference commit')
                if path==outer.root['reference_manifest_path']:return m.canonical(outer.reference)
                return outer.bundle[path.rsplit('/',1)[1]]
            def get(self,path):
                repo={'id':123,'full_name':m.REPOSITORY,'owner':{'id':99,'login':m.REPOSITORY.split('/')[0]}}
                if path=='':return repo
                if '/runs/100/artifacts?' in path:
                    return {'total_count':len(outer.api_artifacts),'artifacts':copy.deepcopy(outer.api_artifacts)}
                if '/runs/100/' in path and '/jobs?' in path:
                    return {'jobs':[{'id':2000+i,'name':f'capture-{i}','status':'completed','conclusion':'success',
                                     'started_at':'2026-01-01T01:39:50Z','completed_at':'2026-01-01T01:40:35Z'} for i in (1,2)]}
                if '/runs/100/' in path:
                    return {'status':'completed','conclusion':'success','head_branch':'master','event':'workflow_dispatch','path':'.github/workflows/size-reference.yml',
                            'head_sha':'a'*40,'repository':repo,'actor':{'id':99},'triggering_actor':{'id':99},'updated_at':'2026-01-01T01:40:45Z'}
                if '/runs/200/' in path:return {'run_started_at':'2026-01-01T01:41:00Z'}
                raise AssertionError('Unexpected API call: '+path)
        return Api()

    def evaluate(self, comparison_exit=3, comparison=None):
        # These bytes and SDK outcomes are explicitly fixtures, not a real APK inspection.
        api=self.simulated_api()
        reference,bundle=m.fetch_bundle(api,self.root)
        m.validate_reference(api,self.root,reference,bundle,self.platform,self.fixture_certificate)
        m.check_dependencies(self.dependencies,self.paths,self.verification)
        cmp = profile_fixtures.m.comparison(self.refprofile,self.observed,m.sha(self.bundle['profile.json']),m.sha(m.canonical(self.observed)))
        return m.evaluate_records(self.context,self.root,self.reference,self.bundle,self.evidence,self.receipts,self.observed,
            m.canonical(self.observed),cmp if comparison is None else comparison,comparison_exit,m.file_sha(self.apk),self.apk.stat().st_size,
            self.source,self.dependencies,self.toolchain,self.recipe,self.pin,self.fixture_certificate)

    def rejected(self, **kwargs):
        with self.assertRaises((m.Rejected, ValueError, KeyError, TypeError, zipfile.BadZipFile)):
            self.evaluate(**kwargs)

    def test_01_complete_anchored_candidate(self):
        self.assertEqual('passed',self.evaluate()['status'])
        self.assertNotIn('apk_ready',self.evaluate())
        bad=copy.deepcopy(self.context);bad['schema']='tvhome.size-reference-context/1'
        self.context=bad;self.rejected()

    def test_02_missing_reference(self):
        self.bundle.pop('profile.json');self.rejected()

    def test_03_reference_tampered(self):
        self.bundle['profile.json']+=b'\n';self.rejected()
        with self.assertRaises(m.Rejected):m.decode(b'{"enabled":true,"enabled":false}')

    def test_04_untrusted_root(self):
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaises((m.Rejected,ValueError)):m.operator_root()
            with self.assertRaises(m.Rejected):m.live_platform(m.Github())
        self.context['root_sha256']='0'*64;self.rejected()

    def test_05_same_run_reference(self):
        self.reference['platform']['run_id']=200
        self.anchor_reference()
        self.rejected()
        self.reference['platform']['run_id']=100
        second=json.loads(self.bundle['measurement-2.json'])
        second['platform']['run_id']=101  # A second unrelated run is not the prescribed capture matrix.
        self.bundle['measurement-2.json']=m.canonical(second)
        self.anchor_reference();self.rejected()

    def test_06_baseline_self_update(self):
        self.root['reference_commit']='e'*40;self.rejected()
        # A complete old receipt set cannot stage without independently validated authority.
        state={'checks':{n:'passed' for n in ci.REQUIRED},'application_id':m.APP,'variant':'developmentDebug',
               'commit':'a'*40,'expected_certificate_sha256':self.pin,'apk_sha256':m.file_sha(self.apk)}
        with self.assertRaises(ci.Rejected):ci.approve(self.apk,self.dir,state)
        self.assertFalse((self.dir/'approved').exists())

    def test_07_missing_dependency_bytes(self):
        self.artifact.unlink();self.rejected()

    def test_08_changed_dependency_bytes(self):
        self.artifact.write_bytes(b'changed, same coordinate');self.rejected()

    def test_09_coordinates_only(self):
        self.dependencies['coverage']['plugins']=False;self.rejected()
        self.dependencies['coverage']['plugins']=True;self.dependencies['artifacts']=[];self.rejected()
        with self.subTest(regression='B1: filter external components before AGP artifact selection'):
            source=(m.ENGINE/'gradle/size-gate.init.gradle.kts').read_text()
            listener=source.split('override fun afterResolve(dependencies: ResolvableDependencies)',1)[1].split('\n})',1)[0]
            self.assertNotIn('dependencies.artifacts.artifacts',listener)
            self.assertIn('dependencies.artifactView',listener)
            self.assertIn('componentFilter { it is ModuleComponentIdentifier }',listener)
            self.assertLess(listener.index('componentFilter'),listener.index('.artifacts.artifacts'))
            self.assertNotIn('isLenient',listener)
            self.assertIn('rootProject.allprojects.forEach',source)
            self.assertIn('DependencyVerificationMode.STRICT',source)
            self.assertIn('gradle.startParameter.writeDependencyVerifications.isEmpty()',source)

    def test_10_unknown_toolchain(self):
        with patch.dict(os.environ,{'ImageOS':'ubuntu24','ImageVersion':'fixture-image','RUNNER_ARCH':'X64'}):
            self.toolchain['versions']['sdk_tools']='unknown'
            with self.assertRaises(m.Rejected):m.check_toolchain(self.toolchain,{})
        self.rejected()

    def test_11_changed_toolchain(self):
        self.toolchain['runner']['image_version']='other image';self.rejected()

    def test_12_changed_packaging(self):
        self.recipe['packaging']['minify']=True;self.rejected()

    def test_13_wrong_identity(self):
        self.observed['apk_observed']['application_id']='dev.glasslauncher';self.rejected()

    def test_14_wrong_run_signer(self):
        self.evidence['signing']['expected_certificate_sha256']='0'*64;self.rejected()

    def test_15_legitimate_ephemeral_signers(self):
        self.refprofile['apk_observed']['signatures'][0]['certificate_sha256']='f'*64
        self.bundle['profile.json']=m.canonical(self.refprofile);self.reference['profile_sha256']=m.sha(self.bundle['profile.json'])
        ref_e = json.loads(self.bundle['build-evidence.json'])
        ref_der = b'ANOTHER PUBLIC SIMULATED CERTIFICATE'
        ref_pin = m.sha(ref_der)
        self.refprofile['apk_observed']['signatures'][0]['certificate_sha256'] = ref_pin
        ref_e['signing'].update(expected_certificate_sha256=ref_pin, observed_certificate_sha256=ref_pin, public_certificate_der_base64=base64.b64encode(ref_der).decode())
        self.bundle['build-evidence.json'] = m.canonical(ref_e)
        self.bundle['profile.json'] = m.canonical(self.refprofile)
        self.reference['profile_sha256'] = m.sha(self.bundle['profile.json'])
        approval=json.loads(self.bundle['approval.json']);approval['reference_profile_sha256']=self.reference['profile_sha256']
        self.bundle['approval.json']=m.canonical(approval)
        ref_e['profile_sha256']=self.reference['profile_sha256']
        self.bundle['build-evidence.json']=m.canonical(ref_e)
        self.fixture_certificates[ref_der]=copy.deepcopy(self.certificate)
        self.capture_pins[1]=ref_pin
        self.bundle['capture-1-profile.json']=self.bundle['profile.json']
        self.bundle['capture-1-evidence.json']=self.bundle['build-evidence.json']
        measurement=json.loads(self.bundle['measurement-1.json'])
        measurement['profile_sha256']=self.reference['profile_sha256']
        measurement['evidence_sha256']=m.sha(self.bundle['build-evidence.json'])
        self.bundle['measurement-1.json']=m.canonical(measurement)
        self.build_artifacts();self.anchor_reference()
        self.assertEqual('passed',self.evaluate()['status'])
        self.assertNotEqual(self.refprofile['apk_observed']['signatures'][0]['certificate_sha256'],self.pin)

    def test_16_prohibited_signature(self):
        for field,value in [('key_type','EC'),('key_bits',1024),('dn','CN=Production'),('validity_days',365),('signature_algorithm','SHA1withRSA')]:
            with self.subTest(field=field):
                old=self.certificate[field];self.certificate[field]=value;self.rejected();self.certificate[field]=old
        self.observed['apk_observed']['signatures'][0]['android_debug_certificate']=False;self.rejected()

    def test_17_variable_version_metadata(self):
        self.assertEqual('passed',self.evaluate()['status'])
        self.observed['apk_observed']['version_code']=102;self.evidence['version_code']=102
        self.observed['external_provenance']['values']['version_code']=102
        self.evidence['profile_sha256']=m.sha(m.canonical(self.observed))
        self.assertEqual('passed',self.evaluate()['status'])

    def test_18_invalid_version_metadata(self):
        self.evidence['version_code']=9999;self.rejected()

    def test_19_growth_not_authorized(self):
        self.resize(1001);self.rejected()

    def resize(self, size):
        self.apk.write_bytes(b'a'*size)
        a=self.observed['apk_observed'];a['file_bytes']=size;a['sha256']=m.file_sha(self.apk)
        self.observed['external_provenance']['values']['apk_sha256']=a['sha256']
        self.observed['size_profile']['container_alignment_signature_overhead']['bytes']+=size-1000
        profile_fixtures.ComparisonTests.repercent(self.observed)
        self.evidence.update(apk_bytes=size,apk_sha256=a['sha256'],profile_sha256=m.sha(m.canonical(self.observed)))

    def test_20_smaller_candidate(self):
        self.resize(999);self.assertEqual(-1,self.evaluate()['delta_bytes'])

    def test_21_missing_mandatory_receipt(self):
        for name in m.PRE:
            with self.subTest(name=name):
                entry=self.evidence['checks'].pop(name);self.rejected();self.evidence['checks'][name]=entry

    def test_22_failed_m0_m1(self):
        for name in ('m0-development','manifest','production-rejected'):
            with self.subTest(name=name):
                self.evidence['checks'][name]['status']='failed';self.rejected();self.evidence['checks'][name]['status']='passed'

    def test_23_python_accounting_failure(self):
        expected_failure=unittest.TestResult();expected_failure.testsRun=40
        expected_failure.expectedFailures.append(('mandatory','simulated expected failure'))
        with self.assertRaises(ci.Rejected):ci.check_test_result(expected_failure,40)
        for name,count in m.PYTHON_COUNTS.items():
            for changes in ({'tests':0},{'tests':count-1},{'skipped':1},{'errors':1},{'failures':1}):
                with self.subTest(name=name,changes=changes):
                    old=self.receipts[name];bad={'tests':count,'errors':0,'failures':0,'skipped':0};bad.update(changes)
                    self.receipts[name]=m.canonical(bad)
                    if name in self.evidence['checks']:self.evidence['checks'][name]['receipt_sha256']=m.sha(self.receipts[name])
                    self.rejected();self.receipts[name]=old
                    if name in self.evidence['checks']:self.evidence['checks'][name]['receipt_sha256']=m.sha(old)

    def test_24_jvm_accounting_failure(self):
        self.evidence['checks']['jvm-results']['status']='skipped';self.rejected()
        reports=self.dir/'junit';reports.mkdir()
        with self.assertRaises(ci.Rejected):ci.jvm_results(self.dir,reports,0,{'Development':{'fixture.Test':['expected']},'Original':{}})

    def test_25_build_lint_apk_failure(self):
        self.evidence['checks']['gradle']['status']='failed';self.rejected()
        self.evidence['checks']['gradle']['status']='passed'
        self.apk.write_bytes(b'mutated after verification');self.rejected()
        with self.assertRaises(ci.Rejected):ci.select_apk(self.dir,0)

    def test_26_comparison_not_valid(self):
        for exit_code in (0,1,2,4):
            with self.subTest(exit=exit_code):self.rejected(comparison_exit=exit_code)
        self.rejected(comparison={'schema':'forged','status':'passed'})

    def test_27_manipulated_pr(self):
        self.platform['event']='pull_request';self.rejected()
        workflow=Path(m.ENGINE/'.github/workflows/build.yml').read_text()
        self.assertNotIn('pull_request_target:',workflow)
        self.assertIn('APK publication is disabled',workflow)

    def test_28_release_or_untrusted_event(self):
        for field,value in [('event','workflow_dispatch'),('ref','refs/tags/v1'),('ref','refs/heads/feature'),('workflow_path','untrusted.yml')]:
            with self.subTest(field=field,value=value):
                old=self.platform[field];self.platform[field]=value;self.rejected();self.platform[field]=old
        self.evidence['platform']={**self.platform,'run_attempt':2};self.rejected()
        api=self.simulated_api();real_get=api.get
        def failed_capture_job(path):
            response=real_get(path)
            if '/jobs?' in path: response['jobs'][1]['conclusion']='failure'
            return response
        with patch.object(api,'get',side_effect=failed_capture_job):
            with self.assertRaises(m.Rejected):m.validate_reference(api,self.root,self.reference,self.bundle,self.platform,self.fixture_certificate)

    def test_29_original_unselected_three_hash_audit_reproduction(self):
        measurement=json.loads(self.bundle['measurement-2.json'])
        for name,char in [('apk_sha256','1'),('profile_sha256','2'),('evidence_sha256','3')]:
            measurement[name]=char*64
        self.bundle['measurement-2.json']=m.canonical(measurement)
        self.anchor_reference()  # Exact audit attack: only manifest/root integrity renewed.
        self.rejected()

    def test_30_two_complete_independent_measurements(self):
        self.assertNotEqual(self.capture_pins[1],self.capture_pins[2])
        self.assertEqual('passed',self.evaluate()['status'])
        self.assertEqual('capture-1',self.reference['platform']['job_id'])

    def change_unselected_measurement_hash(self, name):
        measurement=json.loads(self.bundle['measurement-2.json']);measurement[name]='1'*64
        self.bundle['measurement-2.json']=m.canonical(measurement)
        # Even a simulated authentic archive with the bad measurement cannot
        # make contradictory profile/evidence bytes consistent.
        self.build_artifacts();self.anchor_reference()

    def test_31_unselected_apk_hash_rejected(self):
        self.change_unselected_measurement_hash('apk_sha256')
        with self.assertRaisesRegex(m.Rejected,'Capture APK hash/size differs'):self.evaluate()

    def test_32_unselected_profile_hash_rejected(self):
        self.change_unselected_measurement_hash('profile_sha256')
        with self.assertRaisesRegex(m.Rejected,'Measurement profile/evidence bytes mismatch'):self.evaluate()

    def test_33_unselected_evidence_hash_rejected(self):
        self.change_unselected_measurement_hash('evidence_sha256')
        with self.assertRaisesRegex(m.Rejected,'Measurement profile/evidence bytes mismatch'):self.evaluate()

    def test_34_missing_either_capture_profile_or_evidence(self):
        for i in (1,2):
            for kind in ('profile','evidence'):
                with self.subTest(capture=i,kind=kind):
                    filename=f'capture-{i}-{kind}.json';old=self.bundle.pop(filename)
                    self.anchor_reference();self.rejected()
                    self.bundle[filename]=old
        self.anchor_reference();self.assertEqual('passed',self.evaluate()['status'])

    def test_35_swapped_jobs_and_wrong_run_attempt(self):
        original=copy.deepcopy(self.bundle)
        for field,value in [('job_id','capture-1'),('run_id',101),('run_attempt',2)]:
            with self.subTest(field=field):
                self.bundle=copy.deepcopy(original)
                measurement=json.loads(self.bundle['measurement-2.json']);measurement['platform'][field]=value
                self.bundle['measurement-2.json']=m.canonical(measurement)
                self.build_artifacts();self.anchor_reference();self.rejected()
        self.bundle=copy.deepcopy(original)
        for i in (1,2):
            measurement=json.loads(self.bundle[f'measurement-{i}.json'])
            measurement['platform']['job_id']=f'capture-{3-i}'
            self.bundle[f'measurement-{i}.json']=m.canonical(measurement)
        self.build_artifacts();self.anchor_reference();self.rejected()

    def test_36_each_capture_signer_must_be_proven_and_independent(self):
        original=copy.deepcopy(self.bundle)
        original_pins=dict(self.capture_pins)
        for i in (1,2):
            for field,value in [('expected_certificate_sha256','0'*64),('observed_certificate_sha256','0'*64),
                ('public_certificate_der_base64',base64.b64encode(b'UNPROVEN CERTIFICATE').decode()),('private_key_cleanup',False)]:
                with self.subTest(capture=i,field=field):
                    self.bundle=copy.deepcopy(original)
                    evidence=json.loads(self.bundle[f'capture-{i}-evidence.json']);evidence['signing'][field]=value
                    self.bundle[f'capture-{i}-evidence.json']=m.canonical(evidence)
                    measurement=json.loads(self.bundle[f'measurement-{i}.json']);measurement['evidence_sha256']=m.sha(m.canonical(evidence))
                    self.bundle[f'measurement-{i}.json']=m.canonical(measurement)
                    self.select_capture(1);self.build_artifacts();self.anchor_reference();self.rejected()
        self.bundle=copy.deepcopy(original)
        # Reusing capture-1's signer in an otherwise coherent capture-2 is forbidden.
        profile=json.loads(self.bundle['capture-2-profile.json'])
        profile['apk_observed']['signatures']=copy.deepcopy(json.loads(self.bundle['capture-1-profile.json'])['apk_observed']['signatures'])
        evidence=json.loads(self.bundle['capture-2-evidence.json'])
        evidence['signing']=copy.deepcopy(json.loads(self.bundle['capture-1-evidence.json'])['signing'])
        evidence['profile_sha256']=m.sha(m.canonical(profile))
        self.bundle['capture-2-profile.json']=m.canonical(profile);self.bundle['capture-2-evidence.json']=m.canonical(evidence)
        measurement=json.loads(self.bundle['measurement-2.json'])
        measurement.update(profile_sha256=m.sha(m.canonical(profile)),evidence_sha256=m.sha(m.canonical(evidence)))
        self.bundle['measurement-2.json']=m.canonical(measurement);self.capture_pins[2]=original_pins[1]
        self.select_capture(1);self.build_artifacts();self.anchor_reference()
        with self.assertRaisesRegex(m.Rejected,'Reference captures reused a signer'):self.evaluate()
        # Consistent declared pins still do not replace certificate verification.
        self.bundle=copy.deepcopy(original);self.capture_pins=dict(original_pins)
        der=b'CONSISTENT BUT UNPROVEN SIMULATED CERTIFICATE';pin=m.sha(der)
        profile=json.loads(self.bundle['capture-2-profile.json'])
        profile['apk_observed']['signatures'][0]['certificate_sha256']=pin
        evidence=json.loads(self.bundle['capture-2-evidence.json'])
        evidence['signing'].update(expected_certificate_sha256=pin,observed_certificate_sha256=pin,
                                  public_certificate_der_base64=base64.b64encode(der).decode())
        evidence['profile_sha256']=m.sha(m.canonical(profile))
        self.bundle['capture-2-profile.json']=m.canonical(profile);self.bundle['capture-2-evidence.json']=m.canonical(evidence)
        measurement=json.loads(self.bundle['measurement-2.json'])
        measurement.update(profile_sha256=m.sha(m.canonical(profile)),evidence_sha256=m.sha(m.canonical(evidence)))
        self.bundle['measurement-2.json']=m.canonical(measurement);self.capture_pins[2]=pin
        self.select_capture(1);self.build_artifacts();self.anchor_reference()
        with self.assertRaisesRegex(m.Rejected,'Unknown simulated public certificate'):self.evaluate()

    def test_37_second_measurement_larger_is_selected_after_validation(self):
        self.make_capture(2,1002);self.select_capture(2);self.build_artifacts();self.anchor_reference()
        self.assertEqual('passed',self.evaluate()['status'])
        self.assertEqual(1002,self.reference['file_bytes'])
        self.assertEqual('capture-2',self.reference['platform']['job_id'])
        self.select_capture(1);self.anchor_reference()
        with self.assertRaisesRegex(m.Rejected,'Reference selection mismatch'):self.evaluate()

    def test_38_equal_sizes_select_first_after_validation(self):
        self.make_capture(2,1000);self.select_capture(1);self.build_artifacts();self.anchor_reference()
        self.assertEqual('passed',self.evaluate()['status'])
        self.assertEqual('capture-1',self.reference['platform']['job_id'])
        self.select_capture(2);self.anchor_reference()
        with self.assertRaisesRegex(m.Rejected,'Reference selection mismatch'):self.evaluate()

    def test_39_rehashed_semantically_contradictory_capture_rejected(self):
        original=copy.deepcopy(self.bundle)
        for field,reason in [('apk_sha256','Capture summary differs from evidence'),
                             ('profile_sha256','Capture evidence profile binding differs')]:
            with self.subTest(field=field):
                self.bundle=copy.deepcopy(original)
                evidence=json.loads(self.bundle['capture-2-evidence.json']);evidence[field]='1'*64
                self.bundle['capture-2-evidence.json']=m.canonical(evidence)
                measurement=json.loads(self.bundle['measurement-2.json']);measurement['evidence_sha256']=m.sha(m.canonical(evidence))
                self.bundle['measurement-2.json']=m.canonical(measurement)
                self.build_artifacts();self.anchor_reference()
                with self.assertRaisesRegex(m.Rejected,reason):self.evaluate()

    def test_40_artifact_origin_and_exact_member_bytes_required(self):
        original=copy.deepcopy(self.bundle);metadata=copy.deepcopy(self.api_artifacts)
        for field,value in [('digest',None),('digest','sha256:'+'0'*64),('expired',True),('name','untrusted-archive'),
                            ('created_at','2025-12-31T00:00:00Z')]:
            with self.subTest(field=field):
                self.bundle=copy.deepcopy(original);self.api_artifacts=copy.deepcopy(metadata)
                self.api_artifacts[1][field]=value;self.anchor_reference();self.rejected()
        self.api_artifacts=[];self.rejected()  # Successful jobs alone cannot authorize missing artifacts.
        for attack in ('wrong-workflow','archive-tamper','duplicate-name','member-tamper','missing-evidence','bad-zip','skipped-tests'):
            with self.subTest(attack=attack):
                self.bundle=copy.deepcopy(original);self.api_artifacts=copy.deepcopy(metadata)
                if attack=='wrong-workflow':self.api_artifacts[1]['workflow_run']['id']=101
                elif attack=='archive-tamper':self.bundle['capture-2-artifact.zip']+=b'tampered'
                elif attack=='duplicate-name':self.api_artifacts.append(copy.deepcopy(self.api_artifacts[1]))
                else:
                    archive_bytes=io.BytesIO()
                    if attack!='bad-zip':
                        with zipfile.ZipFile(io.BytesIO(original['capture-2-artifact.zip'])) as source, zipfile.ZipFile(archive_bytes,'w') as dest:
                            for member in source.namelist():
                                if attack=='missing-evidence' and member=='build-evidence.json':continue
                                data=source.read(member)
                                if attack=='member-tamper' and member=='candidate-profile.json':data+=b'\n'
                                if attack=='skipped-tests' and member=='profile-tests.json':
                                    data=m.canonical({'tests':19,'failures':0,'errors':0,'skipped':1})
                                dest.writestr(member,data)
                    raw=archive_bytes.getvalue() if attack!='bad-zip' else b'NOT A ZIP'
                    self.bundle['capture-2-artifact.zip']=raw
                    # Even API-authenticated archives must have valid, matching contents.
                    self.api_artifacts[1].update(digest='sha256:'+m.sha(raw),size_in_bytes=len(raw))
                self.anchor_reference();self.rejected()



if __name__=='__main__':unittest.main()
