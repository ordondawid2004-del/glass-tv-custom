#!/usr/bin/env python3
"""Pinned Development size contract. No uploader, baseline writer or production mode."""
import argparse
import base64
import datetime as dt
import hashlib
import importlib.util
import json
import os
import platform as runtime_platform
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import urllib.request
import uuid
import xml.etree.ElementTree as ET
import zipfile

REPOSITORY = 'ordondawid2004-del/glass-tv-custom'
APP = 'io.github.ordondawid2004del.tvhome.dev'
POLICY = 'development-no-positive-delta-v1'
ENGINE = Path(__file__).resolve().parents[1]
BASE = {'tools', 'ci-tests', 'm0-tests', 'manifest-tests', 'gradle', 'jvm-results',
        'apk-output', 'm0-development', 'production-rejected', 'manifest', 'size', 'inventory'}
EXTRA = {'reference-contract', 'source-inputs', 'dependency-artifacts', 'size-profile', 'size-comparison'}
PRE = (BASE - {'size'}) | EXTRA
CAPTURE_PRE = (BASE - {'size'}) | {'bootstrap-contract', 'source-inputs', 'dependency-artifacts', 'size-profile'}
PYTHON_COUNTS = {'ci-tests': 56, 'm0-tests': 37, 'manifest-tests': 11,
                 'size-contract-tests': 40, 'profile-tests': 19, 'comparison-tests': 60}
ROLES = {'runtime', 'compile', 'tests', 'buildscript', 'plugins', 'descriptors'}
BUNDLE = {'profile.json', 'source-manifest.json', 'dependency-artifacts.json', 'verification-metadata.xml',
          'toolchain-manifest.json', 'recipe.json', 'build-evidence.json', 'measurement-1.json',
          'measurement-2.json', 'approval.json',
          *(f'capture-{i}-{kind}.json' for i in (1,2) for kind in ('profile','evidence')),
          *(f'capture-{i}-artifact.zip' for i in (1,2))}
SCHEMAS = {
 'size-trust-root': 'enabled repository repository_id engine_commit engine_manifest_sha256 reference_commit reference_manifest_path reference_manifest_sha256 policy_id',
 'size-source-inputs': 'repository_id commit git_tree files injected_inputs effective_inputs_sha256',
 'size-dependency-artifacts': 'artifacts verification_metadata_sha256 strict_verification coverage',
 'size-toolchain': 'runner versions distributions files actions',
 'size-recipe': 'variant application_id tasks gradle_arguments engine_manifest_sha256 init_script_sha256 configuration_files environment sdk_levels packaging signing_policy version_policy',
 'size-build-evidence': 'mode platform source_sha256 recipe_sha256 toolchain_sha256 dependencies_sha256 apk_sha256 apk_bytes profile_sha256 started_at finished_at version_code signing checks',
 'size-bootstrap': 'repository_id owner_id source_commit engine_commit engine_manifest_sha256 verification_commit verification_path verification_sha256 toolchain_commit toolchain_path toolchain_sha256',
 'size-reference-context': 'bootstrap_sha256 repository_id owner_id engine_commit source_commit platform private_run_nonce',
 'size-gate-context': 'root_sha256 repository_id engine_commit source_commit platform reference_commit reference_manifest_sha256 policy_id private_run_nonce',
 'size-gate-verdict': 'status reason_codes context_sha256 root_sha256 reference_manifest_sha256 source_sha256 evidence_sha256 apk_sha256 profile_sha256 comparison_sha256 platform reference_bytes candidate_bytes delta_bytes policy_id',
 'development-size-reference': 'reference_id policy_id repository_id source_commit engine_commit platform variant application_id apk_sha256 profile_sha256 file_bytes source_sha256 recipe_sha256 toolchain_sha256 dependencies_sha256 selection files',
 'size-reference-measurement': 'evidence_sha256 profile_sha256 apk_sha256 file_bytes source_sha256 recipe_sha256 toolchain_sha256 dependencies_sha256 platform release_budget_observation',
 'size-reference-approval': 'reference_id reference_profile_sha256 reference_apk_sha256 file_bytes policy_id approved_by approved_at operator_record',
}


class Rejected(ValueError):
    pass


class Denied(Rejected):
    pass


def need(ok, message):
    if not ok:
        raise Rejected(message)


def canonical(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def file_sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest() if hasattr(hashlib, 'file_digest') else sha(stream.read())


def hex_value(v, n=64):
    need(type(v) is str and re.fullmatch('[0-9a-f]{' + str(n) + '}', v) is not None, 'Invalid digest/commit')


def text(v):
    need(type(v) is str and v.strip() and not any(ord(c) < 32 for c in v), 'Invalid string')


def number(v, minimum=0):
    need(type(v) is int and minimum <= v <= 2**63 - 1, 'Invalid integer')


def fields(v, names):
    need(type(v) is dict and set(v) == set(names.split() if isinstance(names, str) else names), 'Missing/unknown object fields')


def relative(v):
    text(v)
    need(not v.startswith('/') and '\\' not in v and ':' not in v and
         all(p not in ('', '.', '..') for p in v.split('/')), 'Unsafe relative path')


def pairs(items):
    d = {}
    for k, v in items:
        need(k not in d, 'Duplicate JSON field')
        d[k] = v
    return d


def decode(raw, maximum=16 * 1024**2):
    need(len(raw) <= maximum, 'Oversized JSON')
    def constant(_):
        raise Rejected('Non-finite JSON')
    return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant)


def read_bytes(path, maximum=16 * 1024**2):
    path = Path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        need(stat.S_ISREG(before.st_mode) and before.st_size <= maximum, 'Not a bounded regular file')
        raw = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
    stamp = lambda x: (x.st_dev, x.st_ino, x.st_size, x.st_mtime_ns, x.st_ctime_ns)
    need(stamp(before) == stamp(after) == stamp(path.stat()) and len(raw) <= maximum, 'Input changed while reading')
    return raw


def read_json(path):
    return decode(read_bytes(path))


def write_new(path, value):
    path = Path(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(canonical(value))


def file_entries(entries, allow_links=False):
    need(type(entries) is list and len(entries) <= 100000, 'Invalid file inventory')
    seen = []
    for e in entries:
        fields(e, 'path mode bytes sha256')
        relative(e['path']); number(e['bytes']); hex_value(e['sha256'])
        need(e['mode'] in (('100644', '100755', '120000') if allow_links else ('100644', '100755')), 'Unbound symlink/file mode')
        seen.append(e['path'])
    need(seen == sorted(set(seen)), 'Unsorted/duplicate file inventory')


def platform(v):
    fields(v, 'repository_id event ref workflow_path workflow_commit run_id run_attempt job_id source_commit')
    for k in ('repository_id', 'run_id', 'run_attempt'):
        number(v[k], 1)
    for k in ('workflow_commit', 'source_commit'):
        hex_value(v[k], 40)
    for k in ('event', 'ref', 'job_id'):
        text(v[k])
    relative(v['workflow_path'])


def document(v, name):
    fields(v, {'schema', *SCHEMAS[name].split()})
    need(v['schema'] == f'tvhome.{name}/1', 'Wrong contract schema')
    for k, value in v.items():
        if k.endswith('_sha256'):
            hex_value(value)
        if k.endswith('_commit') or k in ('commit', 'git_tree'):
            hex_value(value, 40)
        if k in ('repository_id', 'owner_id', 'file_bytes', 'apk_bytes', 'version_code'):
            number(value, 1)
    for k in ('repository','reference_id','selection','variant','application_id','mode','status','version_policy','operator_record','approved_by','approved_at','started_at','finished_at'):
        if k in v: text(v[k])
    if 'enabled' in v: need(type(v['enabled']) is bool, 'Root enabled must be boolean')
    if 'private_run_nonce' in v:
        need(type(v['private_run_nonce']) is str and re.fullmatch('[0-9a-f]{32}',v['private_run_nonce']) is not None, 'Invalid run nonce')
    if 'platform' in v:
        platform(v['platform'])
    if 'policy_id' in v:
        need(v['policy_id'] == POLICY, 'Unapproved policy')
    return v


def timestamp(v):
    text(v)
    value = dt.datetime.fromisoformat(v.replace('Z', '+00:00'))
    need(value.utcoffset() == dt.timedelta(0), 'UTC timestamp required')
    return value


def source_inventory(root, commit, repository_id):
    root = Path(root)
    def git(*args):
        return subprocess.check_output(['git', '-C', str(root), *args], env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'})
    need(git('rev-parse', 'HEAD').decode().strip() == commit, 'Source commit mismatch')
    need(not git('status', '--porcelain', '--untracked-files=all'), 'Uncommitted source inputs')
    files = []
    for item in git('ls-tree', '-rz', '--full-tree', commit).split(b'\0'):
        if not item:
            continue
        meta, rel = item.split(b'\t', 1); mode, kind, _ = meta.decode().split()
        rel = rel.decode(); relative(rel)
        need(kind == 'blob' and mode in ('100644', '100755'), 'Submodule/symlink not supported')
        p = root / rel
        need(not p.is_symlink() and p.is_file(), 'Missing source file')
        raw = p.read_bytes()
        need(not raw.startswith(b'version https://git-lfs.github.com/spec/'), 'Unmaterialized LFS input')
        files.append({'path': rel, 'mode': mode, 'bytes': len(raw), 'sha256': sha(raw)})
    files.sort(key=lambda e: e['path'])
    return {'schema': 'tvhome.size-source-inputs/1', 'repository_id': repository_id, 'commit': commit,
            'git_tree': git('rev-parse', 'HEAD^{tree}').decode().strip(), 'files': files, 'injected_inputs': [],
            'effective_inputs_sha256': sha(canonical({'files': files, 'injected_inputs': []}))}


def check_files(root, entries, allow_links=False):
    file_entries(entries, allow_links)
    for e in entries:
        p = Path(root) / e['path']
        if allow_links and e['mode'] == '120000':
            need(p.is_symlink() and p.resolve().is_relative_to(Path(root).resolve()) and p.resolve().exists(), 'Unbound toolchain symlink')
            payload = os.readlink(p).encode('utf-8')
            need(len(payload)==e['bytes'] and sha(payload)==e['sha256'], 'Toolchain symlink changed')
            continue
        need(p.is_file() and not p.is_symlink() and all(not q.is_symlink() for q in p.parents), 'Missing/symlink input')
        need(('100755' if p.stat().st_mode & 0o111 else '100644') == e['mode'], 'Input mode changed')
        need(p.stat().st_size == e['bytes'] and file_sha(p) == e['sha256'], 'Input bytes mismatch: ' + e['path'])


def check_dependencies(data, paths, verification):
    document(data, 'size-dependency-artifacts')
    need(data['strict_verification'] is True and type(data['coverage']) is dict and set(data['coverage']) == ROLES
         and all(v is True for v in data['coverage'].values()), 'Incomplete verification coverage')
    need(data['verification_metadata_sha256'] == file_sha(verification), 'Verification list mismatch')
    xml = read_bytes(verification)
    need(b'<!DOCTYPE' not in xml and b'<!ENTITY' not in xml, 'Unsafe verification XML')
    node = ET.fromstring(xml)
    ns = {'v': 'https://schema.gradle.org/dependency-verification'}
    need(node.tag == '{https://schema.gradle.org/dependency-verification}verification-metadata', 'Wrong verification XML')
    cfg = node.find('v:configuration', ns)
    need(cfg is not None and cfg.findtext('v:verify-metadata', namespaces=ns) == 'true', 'Metadata verification disabled')
    need(not any(cfg.findall('v:' + n + '/*', ns) for n in ('trusted-artifacts', 'ignored-keys')), 'Verification exceptions forbidden')
    allowed = {}
    for c in node.findall('v:components/v:component', ns):
        component = ':'.join(c.attrib[k] for k in ('group', 'name', 'version'))
        for a in c.findall('v:artifact', ns):
            allowed[(component, a.attrib['name'])] = {n.attrib['value'] for n in a.findall('v:sha256', ns)}
    need(type(data['artifacts']) is list and data['artifacts'], 'Missing artifact byte inventory')
    seen = []
    for a in data['artifacts']:
        fields(a, 'component artifact roles configurations bytes sha256')
        text(a['component']); text(a['artifact']); number(a['bytes'], 1); hex_value(a['sha256'])
        version = a['component'].rsplit(':', 1)[-1]
        need(not re.search(r'[+\[\](),]|SNAPSHOT|latest|release', version, re.I), 'Dynamic/changing dependency')
        for key in ('roles', 'configurations'):
            need(type(a[key]) is list and a[key] and a[key] == sorted(set(a[key])), 'Ambiguous artifact roles')
            for v in a[key]: text(v)
        need(set(a['roles']) <= ROLES, 'Unknown artifact role')
        key = (a['component'], a['artifact']); seen.append(key)
        need(a['sha256'] in allowed.get(key, set()), 'Actual dependency not approved by XML')
        need(key in paths, 'Missing actual artifact file')
        p = Path(paths[key]); need(p.is_file() and not p.is_symlink(), 'Missing artifact bytes')
        need(p.stat().st_size == a['bytes'] and file_sha(p) == a['sha256'], 'Changed dependency bytes')
    need(seen == sorted(set(seen)), 'Ambiguous dependency inventory')
    need(set(paths) == set(seen), 'Uninventoried artifact files')
    need(set().union(*(set(a['roles']) for a in data['artifacts'])) == ROLES, 'Missing actual artifact role')


def check_toolchain(data, roots):
    document(data, 'size-toolchain')
    fields(data['runner'], 'os image_os image_version architecture')
    need(data['runner'] == {'os': 'Linux', 'image_os': os.environ.get('ImageOS'),
                           'image_version': os.environ.get('ImageVersion'), 'architecture': os.environ.get('RUNNER_ARCH')}, 'Unknown/different runner')
    for v in data['runner'].values(): text(v)
    names = 'java python gradle kotlin_plugin agp d8_r8 sdk_tools build_tools platform'.split()
    fields(data['versions'], names)
    for v in data['versions'].values(): text(v); need(v.lower() not in ('unknown', 'latest', '?'), 'Unknown toolchain')
    need('21.0.13+9' in data['versions']['java'], 'Unapproved JDK patch/build')
    need(data['versions']['python'] == runtime_platform.python_version(), 'Actual Python version mismatch')
    need(data['versions']['gradle'] == '9.8.0' and data['versions']['kotlin_plugin'] == '2.4.20'
         and data['versions']['agp'] == '9.4.1', 'Wrong build toolchain')
    for key in ('distributions', 'actions'):
        need(type(data[key]) is dict and data[key], 'Missing distribution/action pins')
        for name, value in data[key].items(): text(name); hex_value(value, 40 if key == 'actions' else 64)
    file_entries(data['files'],allow_links=True); need(data['files'], 'No toolchain byte inventory')
    grouped = {}
    for f in data['files']:
        prefix, rel = f['path'].split('/', 1)
        need(prefix in roots, 'Unknown toolchain distribution')
        if prefix == 'sdk':
            need(rel.split('/')[0] in {'cmdline-tools','build-tools','platforms','packages.xml'}, 'Unused/unbounded SDK package in toolchain class')
        grouped.setdefault(prefix, []).append({**f, 'path': rel})
    need(set(grouped) == {'jdk', 'sdk', 'gradle'}, 'Missing toolchain distribution')
    for prefix, entries in grouped.items():
        check_files(roots[prefix], entries, allow_links=True)
        actual = sorted(p.relative_to(roots[prefix]).as_posix() for p in Path(roots[prefix]).rglob('*')
                        if (p.is_file() or p.is_symlink()) and (prefix != 'sdk' or p.relative_to(roots[prefix]).parts[0] in {'cmdline-tools','build-tools','platforms','packages.xml'}))
        need(actual == sorted(e['path'] for e in entries), 'Incomplete toolchain inventory')
    java_version = subprocess.check_output([str(Path(roots['jdk'])/'bin/java'),'-version'], stderr=subprocess.STDOUT, text=True)
    need('21.0.13+9' in java_version and ('Temurin' in java_version or 'Adoptium' in java_version), 'Actual JDK vendor/build mismatch')



def check_recipe(v):
    document(v, 'size-recipe')
    need(v['variant'] == 'developmentDebug' and v['application_id'] == APP, 'Wrong recipe identity')
    for key in ('tasks', 'gradle_arguments'):
        need(type(v[key]) is list and v[key], 'Missing recipe command')
        for a in v[key]: text(a)
    need(v['tasks'] == [':app:assembleDevelopmentDebug', ':app:lintDevelopmentDebug', ':app:testDevelopmentDebugUnitTest',
          ':app:testOriginalDebugUnitTest', ':app:writeDevelopmentSizeInputs'], 'Incomplete/extra task recipe')
    need(not any(re.search('connected|install|record|assemble.*release|macrobenchmark', t, re.I) for t in v['tasks']), 'Forbidden task')
    need({'--dependency-verification=strict', '--no-build-cache', '--rerun-tasks', '--no-configuration-cache'} <= set(v['gradle_arguments']), 'Unsafe Gradle flags')
    need(not any(a in {'--build-cache','--configuration-cache','--dependency-verification=off','--dependency-verification=lenient','--write-verification-metadata'} for a in v['gradle_arguments']), 'Contradictory verification/cache flags')
    file_entries(v['configuration_files'])
    fields(v['sdk_levels'], 'compile min target'); need(v['sdk_levels'] == {'compile': 37, 'min': 28, 'target': 36}, 'Wrong SDK')
    fields(v['packaging'], 'minify shrink_resources dex_compressed native_compressed abis excludes')
    p = v['packaging']; need(p['minify'] is False and p['shrink_resources'] is False and p['dex_compressed'] is True
                            and p['native_compressed'] is False, 'Wrong debug packaging')
    need(p['abis'] == ['arm64-v8a', 'armeabi-v7a', 'x86', 'x86_64'], 'Wrong ABI policy')
    need(type(p['excludes']) is list and p['excludes'] == sorted(set(p['excludes'])), 'Invalid exclusions')
    fields(v['environment'], 'LANG TZ'); need(v['environment'] == {'LANG': 'C.UTF-8', 'TZ': 'UTC'}, 'Unbound environment')
    need(v['version_policy'] == 'build-minute-since-2026-01-01', 'Unknown version policy')
    check_signing_policy(v['signing_policy'])


def check_signing_policy(v):
    fields(v, 'certificate_profile signature_schemes')
    fields(v['certificate_profile'], 'key_type key_bits dn validity_days signature_algorithm')
    c = v['certificate_profile']
    need(c['key_type'] == 'RSA' and type(c['key_bits']) is int and c['key_bits'] == 2048
         and c['dn'] == 'CN=Android Debug,O=TV Home CI Development,C=DE'
         and type(c['validity_days']) is int and c['validity_days'] == 30
         and c['signature_algorithm'] in {'SHA256withRSA','SHA384withRSA'}, 'Unapproved development certificate profile')
    need(type(v['signature_schemes']) is list and v['signature_schemes'] and
         v['signature_schemes'] == sorted(set(v['signature_schemes'])) and
         set(v['signature_schemes']) <= {'v1', 'v2', 'v3', 'v3.0', 'v3.1', 'v4'}, 'Invalid signature scheme profile')


class Github:
    """Only fixed GET endpoints in our fork; no mutation or free request URLs."""
    def get(self, suffix):
        need((suffix == '' or suffix.startswith('/')) and '..' not in suffix and not suffix.startswith('//'), 'Unsafe API suffix')
        url = 'https://api.github.com/repos/' + REPOSITORY + suffix
        headers = {'Accept': 'application/vnd.github+json', 'User-Agent': 'tvhome-size-gate'}
        token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
        if token: headers['Authorization'] = 'Bearer ' + token
        class SafeRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, hdrs, newurl):
                need(newurl.startswith('https://api.github.com/repos/' + REPOSITORY + '/'), 'Untrusted API redirect')
                return super().redirect_request(req, fp, code, msg, hdrs, newurl)
        opener = urllib.request.build_opener(SafeRedirect())
        with opener.open(urllib.request.Request(url, headers=headers), timeout=30) as response:
            need(response.geturl().startswith('https://api.github.com/repos/' + REPOSITORY), 'Unexpected API redirect')
            return decode(response.read(24 * 1024**2 + 1), maximum=24*1024**2)

    def repository(self):
        return self.get('')

    def blob(self, commit, path):
        hex_value(commit, 40); relative(path)
        from urllib.parse import quote
        response = self.get('/contents/' + quote(path, safe='/') + '?ref=' + commit)
        need(response['type'] == 'file', 'Not a Git file')
        if response['encoding'] == 'none':
            hex_value(response['sha'],40)
            response=self.get('/git/blobs/'+response['sha'])
        need(response['encoding'] == 'base64','Unsupported Git blob encoding')
        raw=base64.b64decode(response['content'],validate=False)
        need(len(raw)<=16*1024**2,'Oversized pinned blob')
        return raw


def live_platform(api, capture=False):
    need(os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('GITHUB_REPOSITORY') == REPOSITORY,
         'No trusted Actions repository context')
    repo = api.get('')
    need(repo['full_name'] == REPOSITORY and repo['owner']['login'] == REPOSITORY.split('/')[0], 'Wrong fork')
    run_id, attempt = int(os.environ['GITHUB_RUN_ID']), int(os.environ['GITHUB_RUN_ATTEMPT'])
    run = api.get(f'/actions/runs/{run_id}/attempts/{attempt}')
    event = 'workflow_dispatch' if capture else 'push'
    workflow = '.github/workflows/size-reference.yml' if capture else '.github/workflows/build.yml'
    need(run['repository']['id'] == repo['id'] and run['status'] == 'in_progress' and run['event'] == event and run['head_branch'] == 'master'
         and run['path'] == workflow and os.environ.get('GITHUB_REF') == 'refs/heads/master', 'Untrusted event/workflow')
    if capture:
        need(run['actor']['id'] == run['triggering_actor']['id'] == repo['owner']['id'], 'Only owner may capture')
    source = os.environ['GITHUB_SHA']; hex_value(source, 40)
    need(run['head_sha'] == source, 'Actions commit mismatch')
    jobs = api.get(f'/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100')
    job = os.environ['GITHUB_JOB']
    job = os.environ.get('TVHOME_SIZE_JOB_NAME', job)
    need(any(j['name'] == job and j['status'] == 'in_progress' for j in jobs['jobs']), 'Unknown/not-running current job')
    return {'repository_id': repo['id'], 'event': event, 'ref': 'refs/heads/master', 'workflow_path': workflow,
            'workflow_commit': source, 'run_id': run_id, 'run_attempt': attempt, 'job_id': job, 'source_commit': source}, repo


def operator_root():
    raw = os.environ.get('TVHOME_SIZE_TRUST_ROOT', '').encode()
    root = document(decode(raw, 8192), 'size-trust-root')
    need(raw == canonical(root), 'Operator root must use canonical contract bytes')
    need(root['enabled'] is True and root['repository'] == REPOSITORY, 'No active approved operator root')
    relative(root['reference_manifest_path'])
    return root, sha(raw)


def fetch_bundle(api, root):
    raw = api.blob(root['reference_commit'], root['reference_manifest_path'])
    need(sha(raw) == root['reference_manifest_sha256'], 'Reference manifest tampered')
    ref = document(decode(raw), 'development-size-reference')
    need(raw == canonical(ref), 'Reference manifest must have canonical contract bytes')
    file_entries(ref['files']); need({x['path'] for x in ref['files']} == BUNDLE, 'Incomplete reference bundle')
    parent = str(PurePosixPath(root['reference_manifest_path']).parent)
    bundle = {}
    for item in ref['files']:
        content = api.blob(root['reference_commit'], parent + '/' + item['path'])
        need(len(content) == item['bytes'] and sha(content) == item['sha256'], 'Reference bytes mismatch')
        bundle[item['path']] = content
    need(ref['repository_id'] == root['repository_id'] and ref['policy_id'] == root['policy_id']
         and ref['variant'] == 'developmentDebug' and ref['application_id'] == APP, 'Wrong reference contract')
    need(ref['selection'] == 'larger_of_two_measurements_tie_first', 'Unapproved reference selection')
    return ref, bundle


def capture_artifact_bytes(api, ref, bundle, jobs):
    """Bind stored capture ZIP bytes to GitHub's artifact digest, not a bundle claim.

    No archive download or extraction: the owner-pinned bundle carries the exact
    upload archives. Missing/expired API metadata or absent digests fail closed.
    The trusted workflow's fixed name plus run, attempt and job time binds each
    artifact to its capture job. This is not an OIDC or runner attestation.
    """
    import io
    import zipfile
    p = ref['platform']
    response = api.get(f'/actions/runs/{p["run_id"]}/artifacts?per_page=100')
    need(type(response['artifacts']) is list and type(response['total_count']) is int
         and response['total_count'] == len(response['artifacts']) <= 100, 'Incomplete artifact API result')
    captures = []; artifact_ids = set()
    for i, job in enumerate(jobs, 1):
        name = f'size-reference-evidence-{p["run_id"]}-{p["run_attempt"]}-{i}'
        matches = [a for a in response['artifacts'] if a['name'] == name]
        need(len(matches) == 1, 'Missing/ambiguous capture artifact')
        artifact = matches[0]; number(artifact['id'], 1)
        need(artifact['id'] not in artifact_ids, 'Capture artifact ID reused')
        artifact_ids.add(artifact['id'])
        archive = bundle[f'capture-{i}-artifact.zip']
        need(artifact['expired'] is False and artifact['digest'] == 'sha256:' + sha(archive)
             and type(artifact['size_in_bytes']) is int and artifact['size_in_bytes'] == len(archive),
             'Expired/unbound capture artifact bytes')
        origin = artifact['workflow_run']
        need(origin['id'] == p['run_id'] and origin['head_sha'] == p['workflow_commit']
             and origin['repository_id'] == origin['head_repository_id'] == p['repository_id']
             and origin['head_branch'] == 'master', 'Wrong artifact workflow origin')
        need(timestamp(job['started_at']) <= timestamp(artifact['created_at']) <= timestamp(job['completed_at']),
             'Artifact not created within the capture job')
        need(len(archive) <= 16 * 1024**2, 'Oversized capture archive')
        with zipfile.ZipFile(io.BytesIO(archive)) as z:
            entries = z.infolist(); names = [e.filename for e in entries]
            need(0 < len(entries) <= 128 and len(names) == len(set(names)), 'Ambiguous capture archive members')
            need(sum(e.file_size for e in entries) <= 64 * 1024**2, 'Oversized expanded capture archive')
            for e in entries:
                relative(e.filename)
                need('/' not in e.filename and not e.is_dir() and not e.flag_bits & 1
                     and stat.S_IFMT(e.external_attr >> 16) in (0, stat.S_IFREG)
                     and e.file_size <= 16 * 1024**2 and e.filename.endswith('.json'),
                     'Unsafe/non-report capture archive member')
            need(z.testzip() is None, 'Capture archive CRC mismatch')
            required = {'candidate-profile.json': f'capture-{i}-profile.json',
                        'build-evidence.json': f'capture-{i}-evidence.json',
                        'measurement.json': f'measurement-{i}.json'}
            for member, filename in required.items():
                need(member in names and z.read(member) == bundle[filename], 'Capture archive/report bytes differ')
            need('summary.json' in names, 'Missing independent capture summary')
            summary = decode(z.read('summary.json'))
            evidence = decode(bundle[f'capture-{i}-evidence.json'])
            need(timestamp(job['started_at']) <= timestamp(evidence['started_at']) <=
                 timestamp(evidence['finished_at']) <= timestamp(artifact['created_at']),
                 'Capture evidence outside actual job/upload interval')
            need(all(summary[key] == evidence[key] for key in ('apk_sha256','apk_bytes','version_code')),
                 'Capture summary differs from evidence')
            need(summary['status'] == 'measurement_only' and summary['development_only'] is True
                 and summary['production_approved'] is False and summary['application_id'] == APP
                 and summary['variant'] == 'developmentDebug', 'Unapproved capture summary')
            # All six strict Python accounts are evidence, not only a successful job name.
            for name, count in PYTHON_COUNTS.items():
                filename = name + '.json'
                need(filename in names, 'Missing capture Python accounting')
                data = decode(z.read(filename)); fields(data, 'tests failures errors skipped')
                need(all(type(v) is int for v in data.values()) and
                     data == {'tests':count,'failures':0,'errors':0,'skipped':0}, 'Invalid capture Python accounting')
                if name in CAPTURE_PRE:
                    evidence = decode(bundle[f'capture-{i}-evidence.json'])
                    need(evidence['checks'][name]['receipt_sha256'] == sha(z.read(filename)),
                         'Capture Python receipt differs from actual report')
            captures.append(summary)
    return captures


def check_reference_measurement(measurement, profile_raw, evidence_raw, ref, recipe, expected_pin, certificate_verifier):
    """Validate one capture completely, without borrowing the selected capture's signer."""
    m = measurement
    evidence = document(decode(evidence_raw), 'size-build-evidence')
    profile = decode(profile_raw)
    validator = load_module('compare-apk-size-profile.py', '_reference_measurement_profile')
    validator.validate(profile)
    need(m['profile_sha256'] == sha(profile_raw) and m['evidence_sha256'] == sha(evidence_raw),
         'Measurement profile/evidence bytes mismatch')
    need(evidence['profile_sha256'] == m['profile_sha256'], 'Capture evidence profile binding differs')
    need(evidence['mode'] == 'reference_capture' and evidence['platform'] == m['platform'], 'Wrong capture evidence origin')
    fields(evidence['checks'], CAPTURE_PRE | {'size'})
    for name, receipt in evidence['checks'].items():
        fields(receipt, 'status receipt_sha256 platform'); hex_value(receipt['receipt_sha256'])
        need(receipt['platform'] == m['platform'] and receipt['status'] ==
             ('not_applied_reference_capture' if name == 'size' else 'passed'), 'Invalid capture mandatory receipt')
    for key in ('source_sha256', 'recipe_sha256', 'toolchain_sha256', 'dependencies_sha256'):
        need(evidence[key] == m[key] == ref[key], 'Capture evidence build class differs')
    a = profile['apk_observed']
    need(a['application_id'] == APP and a['version_name'] == '0.3.0-m1-dev'
         and a['debuggable'] is True and a['test_only'] is False, 'Invalid capture APK identity')
    need(a['sha256'] == evidence['apk_sha256'] == m['apk_sha256'] and
         a['file_bytes'] == evidence['apk_bytes'] == m['file_bytes'], 'Capture APK hash/size differs')
    claims = {'apk_sha256': m['apk_sha256'], 'application_id': APP, 'version_code': a['version_code'],
              'variant': 'developmentDebug', 'source_reference': m['source_sha256'],
              'build_recipe': m['recipe_sha256'], 'build_toolchain': m['toolchain_sha256'],
              'dependencies_reference': m['dependencies_sha256'], 'packaging_reference': sha(canonical(recipe['packaging']))}
    need(profile['external_provenance']['values'] == claims, 'Capture profile build binding differs')
    need(sorted(profile['size_profile']['abi_distribution']) == recipe['packaging']['abis'], 'Capture ABI differs')
    start, end = timestamp(evidence['started_at']), timestamp(evidence['finished_at'])
    epoch = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    need(start <= end and type(a['version_code']) is int and a['version_code'] == evidence['version_code']
         and int((start-epoch).total_seconds()//60) <= a['version_code'] <= int((end-epoch).total_seconds()//60),
         'Invalid capture version/build interval')
    sign = evidence['signing']
    fields(sign, 'expected_certificate_sha256 observed_certificate_sha256 public_certificate_der_base64 certificate_profile signature_schemes private_key_cleanup')
    der = base64.b64decode(sign['public_certificate_der_base64'], validate=True)
    hex_value(expected_pin)
    need(sign['expected_certificate_sha256'] == sign['observed_certificate_sha256'] == sha(der) == expected_pin
         and sign['private_key_cleanup'] is True and len(a['signatures']) == 1
         and a['signatures'][0]['certificate_sha256'] == expected_pin
         and a['signatures'][0]['android_debug_certificate'] is True
         and a['signatures'][0]['certificate_dn'].replace(', ', ',') == sign['certificate_profile']['dn'],
         'Capture signer not independently bound')
    policy = {key:sign[key] for key in ('certificate_profile','signature_schemes')}
    check_signing_policy(policy)
    need(certificate_verifier(der) == sign['certificate_profile'] and policy == recipe['signing_policy'],
         'Capture certificate/signature policy differs')
    return expected_pin


def validate_reference(api, root, ref, bundle, current, certificate_verifier=None):
    certificate_verifier = certificate_verifier or certificate_profile
    need(ref['platform']['run_id'] != current['run_id'], 'Same-run reference')
    rr = api.get(f'/actions/runs/{ref["platform"]["run_id"]}/attempts/{ref["platform"]["run_attempt"]}')
    cr = api.get(f'/actions/runs/{current["run_id"]}/attempts/{current["run_attempt"]}')
    need(rr['status'] == 'completed' and rr['conclusion'] == 'success' and rr['head_branch'] == 'master'
         and timestamp(rr['updated_at']) < timestamp(cr['run_started_at']), 'Reference not successfully completed before candidate')
    need(rr['repository']['id'] == root['repository_id'] and rr['event'] == 'workflow_dispatch'
         and rr['path'] == '.github/workflows/size-reference.yml' and rr['head_sha'] == ref['platform']['workflow_commit'], 'Untrusted reference run')
    repo = api.get('')
    need(rr['actor']['id'] == rr['triggering_actor']['id'] == repo['owner']['id'], 'Reference was not captured by owner')
    for field, filename in [('source_sha256','source-manifest.json'), ('recipe_sha256','recipe.json'),
                            ('toolchain_sha256','toolchain-manifest.json'), ('dependencies_sha256','dependency-artifacts.json')]:
        need(ref[field] == sha(bundle[filename]), 'Reference metadata digest mismatch')
    source = document(decode(bundle['source-manifest.json']), 'size-source-inputs')
    need(source['commit'] == ref['source_commit'] and source['repository_id'] == root['repository_id'], 'Wrong reference source')
    recipe = document(decode(bundle['recipe.json']), 'size-recipe'); check_recipe(recipe)
    approval = document(decode(bundle['approval.json']), 'size-reference-approval')
    need(approval['reference_id'] == ref['reference_id'] and approval['reference_profile_sha256'] == ref['profile_sha256']
         and approval['reference_apk_sha256'] == ref['apk_sha256'] and approval['file_bytes'] == ref['file_bytes']
         and approval['policy_id'] == POLICY and approval['approved_by'] == REPOSITORY.split('/')[0], 'Wrong reference approval record')
    measurements = [document(decode(bundle[f'measurement-{i}.json']), 'size-reference-measurement') for i in (1, 2)]
    jobs = api.get(f'/actions/runs/{ref["platform"]["run_id"]}/attempts/{ref["platform"]["run_attempt"]}/jobs?per_page=100')
    capture_jobs = []
    for i, m in enumerate(measurements, 1):
        need(m['platform']['repository_id'] == root['repository_id'] and m['platform']['source_commit'] == ref['source_commit'], 'Wrong measurement origin')
        need({k:v for k,v in m['platform'].items() if k != 'job_id'} ==
             {k:v for k,v in ref['platform'].items() if k != 'job_id'} and m['platform']['job_id'] == f'capture-{i}',
             'Measurements must be distinct jobs of the same controlled capture')
        actual_jobs = [j for j in jobs['jobs'] if j['name'] == m['platform']['job_id']]
        need(len(actual_jobs) == 1 and actual_jobs[0]['status'] == 'completed' and actual_jobs[0]['conclusion'] == 'success',
             'Reference measurement job did not succeed on GitHub')
        capture_jobs.append(actual_jobs[0])
        for k in ('source_sha256', 'recipe_sha256', 'toolchain_sha256', 'dependencies_sha256'):
            need(m[k] == ref[k], 'Noncomparable reference measurements')
    summaries = capture_artifact_bytes(api, ref, bundle, capture_jobs)
    pins = []
    for i, (measurement, summary) in enumerate(zip(measurements, summaries), 1):
        pins.append(check_reference_measurement(measurement, bundle[f'capture-{i}-profile.json'],
            bundle[f'capture-{i}-evidence.json'], ref, recipe, summary['expected_certificate_sha256'], certificate_verifier))
    need(len(set(pins)) == 2, 'Reference captures reused a signer')
    # Selection happens only after both complete captures, their bytes and origins pass.
    index = 0 if measurements[0]['file_bytes'] >= measurements[1]['file_bytes'] else 1
    choice = measurements[index]
    need(choice['platform'] == ref['platform'] and choice['apk_sha256'] == ref['apk_sha256']
         and choice['file_bytes'] == ref['file_bytes'] and choice['profile_sha256'] == ref['profile_sha256']
         and bundle['profile.json'] == bundle[f'capture-{index+1}-profile.json']
         and bundle['build-evidence.json'] == bundle[f'capture-{index+1}-evidence.json'], 'Reference selection mismatch')


def load_module(filename, name):
    spec = importlib.util.spec_from_file_location(name, ENGINE / 'scripts' / filename)
    mod = importlib.util.module_from_spec(spec); sys.modules[name] = mod; spec.loader.exec_module(mod)
    return mod


def validate_comparison(report, observed, reference, profile_raw, reference_raw, comparison_exit):
    need(comparison_exit == 3, 'Comparator is diagnostic, expected exactly exit 3')
    mod = load_module('compare-apk-size-profile.py', '_size_contract_comparator')
    expected = mod.comparison(mod.validate(reference), mod.validate(observed), sha(reference_raw), sha(profile_raw))
    need(report == expected, 'Comparison report differs from recomputed observations')
    c = report['comparability']
    need(not c['missing_information'] and set(c['reasons']) <= {'Different signer fingerprints; no comparable signing policy supplied'}, 'Insufficient/incompatible size comparison')


def check_receipts(evidence, receipt_bytes, expected_platform):
    document(evidence, 'size-build-evidence')
    need(evidence['mode'] == 'candidate_before_size' and evidence['platform'] == expected_platform, 'Wrong evidence mode/run')
    fields(evidence['checks'], PRE)
    for name, entry in evidence['checks'].items():
        fields(entry, 'status receipt_sha256 platform'); hex_value(entry['receipt_sha256']); platform(entry['platform'])
        need(entry['status'] == 'passed' and entry['platform'] == expected_platform, 'Missing/failed mandatory receipt')
        need(name in receipt_bytes and sha(receipt_bytes[name]) == entry['receipt_sha256'], 'Missing/changed live receipt')
    for name, count in PYTHON_COUNTS.items():
        need(name in receipt_bytes, 'Missing strict Python accounting')
        result = decode(receipt_bytes[name]); fields(result, 'tests failures errors skipped')
        need(all(type(v) is int for v in result.values()) and result == {'tests': count, 'failures': 0, 'errors': 0, 'skipped': 0}, 'Incomplete/failed/skipped Python tests')


def evaluate_records(context, root, reference, reference_bundle, evidence, receipts, observed, profile_raw,
                     comparison_report, comparison_exit, actual_apk_sha, actual_apk_bytes, source, dependencies,
                     toolchain, recipe, expected_pin, certificate_verifier, comparison_raw=None):
    """Pure policy core; caller MUST independently establish live context and actual bytes."""
    document(context, 'size-gate-context'); document(root, 'size-trust-root')
    need(root['enabled'] is True and root['repository'] == REPOSITORY and root['policy_id'] == POLICY, 'Inactive operator policy')
    need(context['root_sha256'] == sha(canonical(root)), 'Root hash differs from independently supplied root')
    for key in ('repository_id', 'engine_commit', 'reference_commit', 'reference_manifest_sha256', 'policy_id'):
        need(context[key] == root[key], 'Context/root mismatch')
    p = context['platform']; platform(p)
    need(p['event'] == 'push' and p['ref'] == 'refs/heads/master' and p['workflow_path'] == '.github/workflows/build.yml', 'Untrusted candidate event')
    need(context['source_commit'] == p['source_commit'] and context['repository_id'] == p['repository_id'], 'Wrong source/run identity')
    need(sha(canonical(reference)) == context['reference_manifest_sha256'], 'Reference manifest not bound to root')
    need(reference['platform']['run_id'] != p['run_id'], 'Reference from current run')
    check_receipts(evidence, receipts, p)
    for key, filename in [('source_sha256', 'source-manifest.json'), ('dependencies_sha256', 'dependency-artifacts.json'),
                          ('toolchain_sha256', 'toolchain-manifest.json'), ('recipe_sha256', 'recipe.json')]:
        obj = {'source_sha256': source, 'dependencies_sha256': dependencies, 'toolchain_sha256': toolchain, 'recipe_sha256': recipe}[key]
        need(evidence[key] == sha(canonical(obj)), 'Evidence document hash mismatch')
        if key != 'source_sha256': need(canonical(obj) == reference_bundle[filename], 'Different dependency/toolchain/recipe class')
    document(source, 'size-source-inputs'); file_entries(source['files']); file_entries(source['injected_inputs'])
    need(source['commit'] == context['source_commit'] and source['repository_id'] == p['repository_id']
         and source['effective_inputs_sha256'] == sha(canonical({k: source[k] for k in ('files', 'injected_inputs')})), 'Invalid effective source inventory')
    check_recipe(recipe)
    need(recipe['engine_manifest_sha256'] == root['engine_manifest_sha256'], 'Recipe uses another engine inventory')
    profile_checker = load_module('compare-apk-size-profile.py', '_size_contract_profile_validator')
    profile_checker.validate(observed)
    a = observed['apk_observed']
    need(a['application_id'] == APP and a['version_name'] == '0.3.0-m1-dev' and a['debuggable'] is True
         and a['test_only'] is False, 'Wrong APK identity/flags')
    expected_claims = {'apk_sha256':actual_apk_sha,'application_id':APP,'version_code':a['version_code'],
        'variant':'developmentDebug','source_reference':evidence['source_sha256'],'build_recipe':evidence['recipe_sha256'],
        'build_toolchain':evidence['toolchain_sha256'],'dependencies_reference':evidence['dependencies_sha256'],
        'packaging_reference':sha(canonical(recipe['packaging']))}
    need(observed['external_provenance']['values'] == expected_claims, 'Profile claims do not bind actual build evidence')
    need(evidence['apk_sha256'] == a['sha256'] == actual_apk_sha and evidence['apk_bytes'] == a['file_bytes'] == actual_apk_bytes
         and evidence['profile_sha256'] == sha(profile_raw), 'APK/profile/evidence mismatch')
    need(sorted(observed['size_profile']['abi_distribution']) == recipe['packaging']['abis'], 'Observed ABI mismatch')
    start, end = timestamp(evidence['started_at']), timestamp(evidence['finished_at'])
    need(start <= end, 'Invalid build time interval')
    epoch = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    need(type(a['version_code']) is int and int((start - epoch).total_seconds() // 60) <= a['version_code'] <= int((end - epoch).total_seconds() // 60)
         and a['version_code'] == evidence['version_code'], 'Invalid variable versionCode')
    sign = evidence['signing']; fields(sign, 'expected_certificate_sha256 observed_certificate_sha256 public_certificate_der_base64 certificate_profile signature_schemes private_key_cleanup')
    der = base64.b64decode(sign['public_certificate_der_base64'], validate=True)
    need(sign['expected_certificate_sha256'] == sign['observed_certificate_sha256'] == expected_pin == sha(der), 'Run signer mismatch')
    need(sign['private_key_cleanup'] is True and len(a['signatures']) == 1 and a['signatures'][0]['certificate_sha256'] == expected_pin
         and a['signatures'][0]['android_debug_certificate'] is True, 'Unapproved signature/cleanup')
    actual_certificate_profile = certificate_verifier(der)
    signing_policy = {k: sign[k] for k in ('certificate_profile', 'signature_schemes')}
    check_signing_policy(signing_policy)
    need(actual_certificate_profile == sign['certificate_profile'] and signing_policy == recipe['signing_policy'], 'Certificate/signature profile mismatch')
    refprofile_raw = reference_bundle['profile.json']; refprofile = decode(refprofile_raw)
    need(sha(refprofile_raw) == reference['profile_sha256'] and refprofile['apk_observed']['file_bytes'] == reference['file_bytes'], 'Wrong pinned reference profile')
    ref_evidence = document(decode(reference_bundle['build-evidence.json']), 'size-build-evidence')
    rs = ref_evidence['signing']
    ref_der = base64.b64decode(rs['public_certificate_der_base64'], validate=True)
    need(rs['expected_certificate_sha256'] == rs['observed_certificate_sha256'] == sha(ref_der)
         == refprofile['apk_observed']['signatures'][0]['certificate_sha256']
         and rs['private_key_cleanup'] is True, 'Reference signer not independently bound')
    need(certificate_verifier(ref_der) == rs['certificate_profile']
         and {k: rs[k] for k in ('certificate_profile', 'signature_schemes')} == recipe['signing_policy'], 'Unapproved reference signer profile')
    validate_comparison(comparison_report, observed, refprofile, profile_raw, refprofile_raw, comparison_exit)
    if comparison_raw is not None:
        need(decode(comparison_raw) == comparison_report, 'Comparison raw-byte mismatch')
    if actual_apk_bytes > reference['file_bytes']:
        raise Denied('Unauthorized positive size growth')
    return {'schema': 'tvhome.size-gate-verdict/1', 'status': 'passed', 'reason_codes': [],
            'context_sha256': sha(canonical(context)), 'root_sha256': context['root_sha256'],
            'reference_manifest_sha256': context['reference_manifest_sha256'], 'source_sha256': evidence['source_sha256'],
            'evidence_sha256': sha(canonical(evidence)), 'apk_sha256': actual_apk_sha, 'profile_sha256': sha(profile_raw),
            'comparison_sha256': sha(comparison_raw if comparison_raw is not None else canonical(comparison_report)), 'platform': p, 'reference_bytes': reference['file_bytes'],
            'candidate_bytes': actual_apk_bytes, 'delta_bytes': actual_apk_bytes - reference['file_bytes'], 'policy_id': POLICY}


def preflight(source, output, bootstrap=None, api=None):
    api = api or Github()
    live, repo = live_platform(api, bootstrap is not None)
    if bootstrap is not None:
        raw = read_bytes(bootstrap); b = document(decode(raw), 'size-bootstrap')
        need(b['repository_id'] == repo['id'] and b['owner_id'] == repo['owner']['id'], 'Wrong owner bootstrap')
        live['source_commit'] = b['source_commit']
        context = {'schema': 'tvhome.size-reference-context/1', 'bootstrap_sha256': sha(raw), 'repository_id': repo['id'],
                   'owner_id': b['owner_id'], 'engine_commit': b['engine_commit'], 'source_commit': b['source_commit'],
                   'platform': live, 'private_run_nonce': uuid.uuid4().hex}
    else:
        root, _ = operator_root()
        need(root['repository_id'] == repo['id'], 'Wrong operator repository ID')
        ref, bundle = fetch_bundle(api, root); validate_reference(api, root, ref, bundle, live)
        context = {'schema': 'tvhome.size-gate-context/1', 'root_sha256': sha(canonical(root)),
                   'repository_id': repo['id'], 'engine_commit': root['engine_commit'], 'source_commit': live['source_commit'],
                   'platform': live, 'reference_commit': root['reference_commit'],
                   'reference_manifest_sha256': root['reference_manifest_sha256'], 'policy_id': POLICY, 'private_run_nonce': uuid.uuid4().hex}
    inventory = source_inventory(source, context['source_commit'], live['repository_id'])
    engine = source_inventory(ENGINE, context['engine_commit'], repo['id'])
    anchor = b['engine_manifest_sha256'] if bootstrap is not None else root['engine_manifest_sha256']
    need(sha(canonical(engine['files'])) == anchor, 'Wrong authoritative engine inventory')
    write_new(output, context)
    write_new(Path(output).with_name('source-manifest.json'), inventory)
    return context


def certificate_profile(der):
    import tempfile
    with tempfile.TemporaryDirectory(prefix='tvhome-public-certificate-') as folder:
        cert = Path(folder) / 'public.der'; cert.write_bytes(der)
        keytool = Path(os.environ['JAVA_HOME']) / 'bin/keytool'
        value = subprocess.check_output([str(keytool), '-J-Duser.language=en', '-J-Duser.country=US',
                                         '-J-Duser.timezone=UTC', '-printcert', '-file', str(cert)], text=True)
    owner = re.search(r'^Owner: (.+)$', value, re.M)
    dates = re.search(r'Valid from: (.+?) until: (.+)', value)
    algorithm = re.search(r'Signature algorithm name: (\S+)', value)
    key = re.search(r'Subject Public Key Algorithm: (\d+)-bit (\w+) key', value)
    need(owner and dates and algorithm and key, 'Unreadable certificate profile')
    parse = lambda x: dt.datetime.strptime(x.strip(), '%a %b %d %H:%M:%S %Z %Y')
    validity = parse(dates[2]) - parse(dates[1])
    need(validity.total_seconds() % 86400 == 0, 'Unexpected certificate validity')
    return {'dn': owner[1].replace(', ', ','), 'key_type': key[2], 'key_bits': int(key[1]),
            'validity_days': validity.days, 'signature_algorithm': algorithm[1]}


class Session:
    """Live authority facade. Fixture cores do not bypass this CLI boundary."""
    def __init__(self, context_path, api=None):
        self.api = api or Github(); self.path = Path(context_path).resolve()
        self.context = read_json(self.path)
        self.capture = self.context.get('schema') == 'tvhome.size-reference-context/1'
        document(self.context, 'size-reference-context' if self.capture else 'size-gate-context')
        live, repo = live_platform(self.api, self.capture)
        self.base = self.path.parent
        if self.capture:
            self.bootstrap_raw = read_bytes(self.base / 'bootstrap.json')
            b = self.bootstrap = document(decode(self.bootstrap_raw), 'size-bootstrap')
            need(sha(self.bootstrap_raw) == self.context['bootstrap_sha256'] and b['owner_id'] == repo['owner']['id']
                 and b['repository_id'] == repo['id'], 'Untrusted owner bootstrap')
            live['source_commit'] = b['source_commit']
            self.toolchain = document(decode(self.api.blob(b['toolchain_commit'], b['toolchain_path'])), 'size-toolchain')
            need(sha(canonical(self.toolchain)) == b['toolchain_sha256'], 'Bootstrap toolchain bytes mismatch')
            self.verification = self.api.blob(b['verification_commit'], b['verification_path'])
            need(sha(self.verification) == b['verification_sha256'], 'Bootstrap verification bytes mismatch')
            self.bundle, self.reference, self.root = None, None, None
            engine_commit, engine_digest = b['engine_commit'], b['engine_manifest_sha256']
        else:
            self.root, _ = operator_root()
            need(sha(canonical(self.root)) == self.context['root_sha256'], 'Root changed since preflight')
            self.reference, self.bundle = fetch_bundle(self.api, self.root)
            validate_reference(self.api, self.root, self.reference, self.bundle, live)
            self.toolchain = document(decode(self.bundle['toolchain-manifest.json']), 'size-toolchain')
            self.verification = self.bundle['verification-metadata.xml']
            engine_commit, engine_digest = self.root['engine_commit'], self.root['engine_manifest_sha256']
        need(live == self.context['platform'] and engine_commit == self.context['engine_commit'], 'Context platform/engine mismatch')
        inventory = source_inventory(ENGINE, engine_commit, live['repository_id'])
        need(sha(canonical(inventory['files'])) == engine_digest, 'Authoritative engine changed')
        self.platform = live

    def roots(self):
        if not hasattr(self,'_tool_roots'):
            gradle_home=os.environ.get('TVHOME_GRADLE_HOME') or os.environ.get('GRADLE_HOME')
            if not gradle_home:
                launcher=shutil.which('gradle')
                need(launcher is not None,'No explicit/preinstalled Gradle distribution locator')
                gradle_home=str(Path(launcher).resolve().parent.parent)
            self._tool_roots={'jdk':Path(os.environ['JAVA_HOME']).resolve(),
                              'sdk':Path(os.environ['ANDROID_HOME']).resolve(),'gradle':Path(gradle_home).resolve()}
        return self._tool_roots

    def prepare(self, source, output):
        check_toolchain(self.toolchain, self.roots())
        original = source_inventory(source, self.context['source_commit'], self.platform['repository_id'])
        need(original == read_json(self.base / 'source-manifest.json'), 'Source changed since preflight')
        target = self.base / 'source'; target.mkdir(mode=0o700)
        for e in original['files']:
            dest = target / e['path']; dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(Path(source) / e['path'], dest)
        verification_path = target / 'gradle/verification-metadata.xml'
        if verification_path.exists():
            need(verification_path.read_bytes() == self.verification, 'Candidate supplied another verification policy')
        else:
            verification_path.write_bytes(self.verification)
            original['injected_inputs'] = [{'path': 'gradle/verification-metadata.xml', 'mode': '100644',
                                            'bytes': len(self.verification), 'sha256': sha(self.verification)}]
        original['effective_inputs_sha256'] = sha(canonical({k: original[k] for k in ('files', 'injected_inputs')}))
        write_new(self.base / 'effective-source.json', original)
        (self.base / 'private').mkdir(mode=0o700); (self.base / 'gradle-user-home').mkdir(mode=0o700)
        gitdir = subprocess.check_output(['git', '-C', str(source), 'rev-parse', '--absolute-git-dir'], text=True).strip()
        write_new(output, {'context_sha256': file_sha(self.path), 'source': str(target), 'original_source': str(Path(source).resolve()),
                           'git_dir': gitdir, 'source_manifest_sha256': sha(canonical(original))})

    def begin(self, project, state, env, reports):
        self.prepared = read_json(self.base / 'input.json')
        fields(self.prepared, 'context_sha256 source original_source git_dir source_manifest_sha256')
        need(self.prepared['context_sha256'] == file_sha(self.path) and Path(self.prepared['source']) == Path(project), 'Wrong prepared source path')
        self.source = read_json(self.base / 'effective-source.json')
        document(self.source, 'size-source-inputs')
        need(sha(canonical(self.source)) == self.prepared['source_manifest_sha256'], 'Wrong effective source')
        expected_source = source_inventory(self.prepared['original_source'], self.context['source_commit'], self.platform['repository_id'])
        expected_source['injected_inputs'] = []
        if not any(e['path'] == 'gradle/verification-metadata.xml' for e in expected_source['files']):
            expected_source['injected_inputs'] = [{'path':'gradle/verification-metadata.xml','mode':'100644','bytes':len(self.verification),'sha256':sha(self.verification)}]
        expected_source['effective_inputs_sha256'] = sha(canonical({k:expected_source[k] for k in ('files','injected_inputs')}))
        need(self.source == expected_source and Path(project) == self.base/'source', 'Source identity/injection differs from reviewed commit')
        need((Path(project)/'gradle/verification-metadata.xml').read_bytes() == self.verification, 'Injected policy changed')
        need((Path(project)/'scripts/ci-jvm-scope.json').read_bytes() == (ENGINE/'scripts/ci-jvm-scope.json').read_bytes(), 'Unreviewed test inventory')
        check_files(project, self.source['files']); check_files(project, self.source['injected_inputs'])
        check_toolchain(self.toolchain, self.roots())
        allowed_env = {'PATH','HOME','JAVA_HOME','ANDROID_HOME','ANDROID_SDK_ROOT','ANDROID_USER_HOME',
                       'TMPDIR','TMP','TEMP','LANG','LC_ALL','TZ','APK_ISOLATION_INTEGRATION',
                       'SSL_CERT_FILE','SSL_CERT_DIR','HTTPS_PROXY','HTTP_PROXY','NO_PROXY','https_proxy','http_proxy','no_proxy'}
        for key in list(env):
            if key not in allowed_env: env.pop(key)
        env.update(GRADLE_USER_HOME=str(self.base / 'gradle-user-home'), LANG='C.UTF-8', TZ='UTC',
                   GIT_DIR=self.prepared['git_dir'], GIT_WORK_TREE=str(project), GIT_OPTIONAL_LOCKS='0',
                   TVHOME_SIZE_INPUT_OUTPUT=str(reports / 'resolved-inputs.json'))
        state['commit'] = self.context['source_commit']; state['working_tree_dirty'] = False
        self.env, self.project, self.reports = env, Path(project), reports
        self.started = dt.datetime.now(dt.timezone.utc)
        self.context_digest = file_sha(self.path)
        self.check_source_files(after_build=True)

    def bind_signer(self, pin, der, private_key_digest):
        need(pin == sha(der), 'Wrong independently produced public certificate')
        self.signer_pin = pin
        hex_value(private_key_digest); self.private_key_digest = private_key_digest
        write_new(self.base / 'signer-pin.json', {'context_sha256': self.context_digest, 'expected_pin': pin, 'private_key_digest':private_key_digest})

    def check_source_files(self, after_build=False):
        expected={e['path'] for e in self.source['files']+self.source['injected_inputs']}
        modules={p.split('/')[0] for p in expected if p.endswith('/build.gradle.kts')}
        actual=set()
        for path in self.project.rglob('*'):
            need(not path.is_symlink(), 'Unbound symlink in prepared source/output')
            if not path.is_file(): continue
            rel=path.relative_to(self.project).as_posix(); parts=rel.split('/')
            if after_build and (parts[0] in {'.gradle','.kotlin'} or (len(parts)>1 and parts[0] in modules and parts[1]=='build')):
                continue
            actual.add(rel)
        need(actual == expected, 'Unexpected prepared source input')
        check_files(self.project,self.source['files']);check_files(self.project,self.source['injected_inputs'])

    def gradle_executable(self):
        return self.roots()['gradle'] / 'bin/gradle'

    def gradle_flags(self):
        return ['--init-script', str(ENGINE / 'gradle/size-gate.init.gradle.kts'),
                '--dependency-verification=strict', '--no-build-cache']

    def finish(self, apk, state, pin, der):
        need(file_sha(self.path) == self.context_digest and getattr(self, 'signer_pin', None) == pin,
             'Context or pre-build signer binding changed during pipeline')
        self.check_source_files(after_build=True)
        for location in (self.project,self.reports,self.base/'gradle-user-home'):
            for candidate in location.rglob('*'):
                if candidate.is_file():
                    need(file_sha(candidate) != self.private_key_digest, 'Private signing key copied to output/cache')
        raw = read_json(self.reports / 'resolved-inputs.json')
        fields(raw, 'schema artifacts paths coverage strict_verification verification_metadata_sha256')
        need(raw['schema'] == 'tvhome.size-resolved-inputs/1', 'Wrong collector report')
        paths = {}
        for p in raw['paths']:
            fields(p, 'component artifact path'); key = (p['component'], p['artifact']); need(key not in paths, 'Duplicate live artifact path')
            paths[key] = p['path']
        dependencies = {k: raw[k] for k in ('artifacts', 'coverage', 'strict_verification', 'verification_metadata_sha256')}
        dependencies['schema'] = 'tvhome.size-dependency-artifacts/1'
        check_dependencies(dependencies, paths, self.project / 'gradle/verification-metadata.xml')
        write_new(self.reports / 'dependency-artifacts.json', dependencies)
        write_new(self.reports / 'source-manifest.json', self.source)
        write_new(self.reports / 'toolchain-manifest.json', self.toolchain)
        profiler = load_module('apk-size-report.py', '_size_contract_profiler')
        actual = profiler.profile(apk, project=self.project)
        schemes_output = subprocess.check_output([str(profiler.gate.sdk_tools(os.environ['ANDROID_HOME']).signer),
                                                 'verify', '--verbose', '--print-certs', str(apk)], text=True, env=self.env)
        schemes = sorted(set(re.findall(r'Verified using (v[\d.]+) scheme[^\n]*: true', schemes_output)))
        cert_profile = certificate_profile(der)
        policy = {'certificate_profile': cert_profile, 'signature_schemes': schemes}; check_signing_policy(policy)
        signing = {'expected_certificate_sha256': pin, 'observed_certificate_sha256': actual['apk_observed']['signatures'][0]['certificate_sha256'],
                   'public_certificate_der_base64': base64.b64encode(der).decode(), 'private_key_cleanup': True, **policy}
        configurations = [e for e in self.source['files'] if e['path'].endswith(('.gradle.kts', '.toml', 'gradle.properties', 'gradle-wrapper.properties'))]
        ci = load_module('ci-development.py', '_size_contract_recipe')
        tasks = [':app:assembleDevelopmentDebug', ':app:lintDevelopmentDebug', ':app:testDevelopmentDebugUnitTest',
                 ':app:testOriginalDebugUnitTest', ':app:writeDevelopmentSizeInputs']
        arguments = ['--dependency-verification=strict', '--no-build-cache', '--rerun-tasks', '--no-configuration-cache', '--console=plain',
                     '--init-script', '<engine-init>', '-PtvhomeDevKeystore=<private-run-key>']
        # Preserve variant-scoped filters in the recipe as typed strings, not an unordered test union.
        for variant in ('Development', 'Original'):
            arguments += [variant + ':' + pattern for pattern in ci.FILTERS[variant]]
        recipe = {'schema': 'tvhome.size-recipe/1', 'variant': 'developmentDebug', 'application_id': APP, 'tasks': tasks,
                  'gradle_arguments': arguments, 'engine_manifest_sha256': sha(canonical(source_inventory(ENGINE, self.context['engine_commit'], self.platform['repository_id'])['files'])),
                  'init_script_sha256': file_sha(ENGINE / 'gradle/size-gate.init.gradle.kts'), 'configuration_files': configurations,
                  'environment': {'LANG': 'C.UTF-8', 'TZ': 'UTC'}, 'sdk_levels': {'compile': 37, 'min': 28, 'target': 36},
                  'packaging': {'minify': False, 'shrink_resources': False, 'dex_compressed': True, 'native_compressed': False,
                                'abis': sorted(actual['size_profile']['abi_distribution']), 'excludes': ['DebugProbesKt.bin', 'META-INF/androidx/**']},
                  'signing_policy': policy, 'version_policy': 'build-minute-since-2026-01-01'}
        check_recipe(recipe)
        check_toolchain(self.toolchain,self.roots())
        # The recipe fields above are not accepted as an assertion about arbitrary Gradle sources.
        need((self.project / 'app/build.gradle.kts').read_bytes() == (ENGINE / 'app/build.gradle.kts').read_bytes(), 'Unreviewed app packaging configuration')
        import zipfile
        with zipfile.ZipFile(apk) as z:
            need(all(e.compress_type == zipfile.ZIP_DEFLATED for e in z.infolist() if e.filename.endswith('.dex')),
                 'DEX packaging differs from recipe')
            need(all(e.compress_type == zipfile.ZIP_STORED for e in z.infolist() if e.filename.startswith('lib/')), 'Native packaging differs')
        buildconfig = subprocess.check_output([str(profiler.gate.sdk_tools(os.environ['ANDROID_HOME']).analyzer), 'dex', 'code',
                                              '--class', 'dev.glasslauncher.BuildConfig', str(apk)], text=True, env=self.env)
        match = re.search(r'VERSION_CODE:I = (0x[0-9a-f]+|\d+)', buildconfig)
        need(match and int(match[1], 0) == state['version_code'], 'BuildConfig/manifest version mismatch')
        claims = {'apk_sha256': file_sha(apk), 'application_id': APP, 'version_code': state['version_code'], 'variant': 'developmentDebug',
                  'source_reference': sha(canonical(self.source)), 'build_recipe': sha(canonical(recipe)),
                  'build_toolchain': sha(canonical(self.toolchain)), 'dependencies_reference': sha(canonical(dependencies)),
                  'packaging_reference': sha(canonical(recipe['packaging']))}
        write_new(self.base / 'claims.json', claims)
        actual = profiler.profile(apk, project=self.project, provenance=self.base / 'claims.json')
        profile_raw = (json.dumps(actual, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()
        with (self.reports / 'candidate-profile.json').open('xb') as stream: stream.write(profile_raw)
        write_new(self.reports / 'recipe.json', recipe)
        for name, filename in [('source-inputs', 'source-manifest.json'), ('dependency-artifacts', 'dependency-artifacts.json'),
                               ('size-profile', 'candidate-profile.json')]:
            state['checks'][name] = 'passed'
        contract_check = 'bootstrap-contract' if self.capture else 'reference-contract'
        state['checks'][contract_check] = 'passed'
        comparison = None
        if not self.capture:
            refpath = self.base / 'reference-profile.json'
            with refpath.open('xb') as stream: stream.write(self.bundle['profile.json'])
            compare = subprocess.run([sys.executable, '-B', str(ENGINE / 'scripts/compare-apk-size-profile.py'),
                                      '--reference', str(refpath), '--candidate', str(self.reports / 'candidate-profile.json'),
                                      '--reference-sha256', self.reference['profile_sha256'],
                                      '--output', str(self.reports / 'size-comparison.json')], env=self.env,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            need(compare.returncode == 3, 'Comparator did not finish as a valid diagnostic')
            comparison = read_json(self.reports / 'size-comparison.json')
            state['checks']['size-comparison'] = 'passed'
        wanted = CAPTURE_PRE if self.capture else PRE
        receipts = {}
        checks = {}
        for name in wanted:
            need(state['checks'].get(name) == 'passed', 'Missing live pre-size check: ' + name)
            raw_receipt = canonical({'status': 'passed', 'platform': self.platform, 'name': name,
                                     'apk_sha256': state['apk_sha256'], 'run_nonce': self.context['private_run_nonce']})
            receipts[name] = raw_receipt
            checks[name] = {'status': 'passed', 'platform': self.platform, 'receipt_sha256': sha(raw_receipt)}
        for name in PYTHON_COUNTS:
            receipts[name] = read_bytes(self.reports / (name + '.json'))
            if name in checks: checks[name]['receipt_sha256'] = sha(receipts[name])
        receipt_dir = self.base / 'receipts'; receipt_dir.mkdir(mode=0o700)
        for name, content in receipts.items():
            with (receipt_dir / (name + '.json')).open('xb') as stream: stream.write(content)
        evidence = {'schema': 'tvhome.size-build-evidence/1', 'mode': 'reference_capture' if self.capture else 'candidate_before_size',
                    'platform': self.platform, 'source_sha256': sha(canonical(self.source)), 'recipe_sha256': sha(canonical(recipe)),
                    'toolchain_sha256': sha(canonical(self.toolchain)), 'dependencies_sha256': sha(canonical(dependencies)),
                    'apk_sha256': file_sha(apk), 'apk_bytes': apk.stat().st_size, 'profile_sha256': sha(profile_raw),
                    'started_at': self.started.isoformat(), 'finished_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                    'version_code': state['version_code'], 'signing': signing, 'checks': checks}
        if self.capture:
            observation = subprocess.run([str(ENGINE / 'scripts/apk-size-check'), str(apk)], cwd=self.project, env=self.env, capture_output=True, text=True)
            need(observation.returncode in (0,1), 'Invalid legacy size observation')
            checks['size'] = {'status': 'not_applied_reference_capture', 'platform': self.platform, 'receipt_sha256': sha(b'reference-capture')}
            write_new(self.reports / 'build-evidence.json', evidence)
            measurement = {'schema':'tvhome.size-reference-measurement/1', 'evidence_sha256':sha(canonical(evidence)),
                           'profile_sha256':sha(profile_raw), 'apk_sha256':file_sha(apk), 'file_bytes':apk.stat().st_size,
                           'source_sha256':evidence['source_sha256'], 'recipe_sha256':evidence['recipe_sha256'],
                           'toolchain_sha256':evidence['toolchain_sha256'], 'dependencies_sha256':evidence['dependencies_sha256'],
                           'platform':self.platform, 'release_budget_observation': {'exit_code':observation.returncode,
                               'limit_bytes':3563000,'stdout':observation.stdout,'stderr':observation.stderr}}
            write_new(self.reports / 'measurement.json', measurement)
            state['status'] = 'measurement_only'
            return False
        # A final live verification of the APK and its independently generated pin, not a report string.
        manifest, signers, inspected = profiler.gate.inspect_apk(apk, profiler.gate.sdk_tools(os.environ['ANDROID_HOME']))
        need(inspected == file_sha(apk) and {s.sha256 for s in signers} == {pin}, 'Final live signer mismatch')
        verdict = evaluate_records(self.context, self.root, self.reference, self.bundle, evidence, receipts, actual, profile_raw,
                                   comparison, 3, inspected, apk.stat().st_size, self.source, dependencies, self.toolchain,
                                   recipe, pin, certificate_profile, read_bytes(self.reports / 'size-comparison.json'))
        check_files(self.project, self.source['files']); check_files(self.project, self.source['injected_inputs'])
        check_toolchain(self.toolchain,self.roots())
        check_dependencies(dependencies, paths, self.project / 'gradle/verification-metadata.xml')
        write_new(self.reports / 'build-evidence.json', evidence)
        write_new(self.reports / 'size-verdict.json', verdict)
        state['checks']['size'] = 'passed'; state['size_contract_verified'] = True
        return True



    def evaluate_files(self, apk, evidence_path, comparison_path, output):
        need(not self.capture, 'Reference context cannot authorize a candidate')
        reports = Path(evidence_path).parent
        state = {}; env = dict(os.environ)
        self.begin(self.base / 'source', state, env, reports)
        evidence = read_json(evidence_path)
        prepared_pin = read_json(self.base / 'signer-pin.json')
        fields(prepared_pin, 'context_sha256 expected_pin private_key_digest')
        need(prepared_pin['context_sha256'] == file_sha(self.path), 'Unbound pre-build signer record')
        expected_pin = prepared_pin['expected_pin']; hex_value(expected_pin)
        profile_raw = read_bytes(reports / 'candidate-profile.json'); observed = decode(profile_raw)
        dependencies = read_json(reports / 'dependency-artifacts.json')
        recipe = read_json(reports / 'recipe.json')
        collector = read_json(reports / 'resolved-inputs.json')
        paths = {(v['component'],v['artifact']):v['path'] for v in collector['paths']}
        check_dependencies(dependencies, paths, self.project / 'gradle/verification-metadata.xml')
        receipts = {name:read_bytes(self.base / 'receipts' / (name+'.json')) for name in PRE | set(PYTHON_COUNTS)}
        ci = load_module('ci-development.py','_size_contract_live_junit')
        scratch = self.base / ('junit-recheck-'+uuid.uuid4().hex); scratch.mkdir()
        expected = ci.validate_inventory(self.project)
        ci.jvm_results(self.project, scratch, int(timestamp(evidence['started_at']).timestamp()*1e9), expected)
        profiler = load_module('apk-size-report.py','_size_contract_live_evaluate')
        tools = profiler.gate.sdk_tools(os.environ['ANDROID_HOME'])
        manifest, signers, actual_hash = profiler.gate.inspect_apk(apk, tools)
        need(not profiler.gate.policy_errors(manifest,signers,APP,'development',(expected_pin,)), 'M0 development recheck failed')
        need(profiler.gate.policy_errors(manifest,signers,APP,'production',(expected_pin,)), 'Production was unexpectedly accepted')
        xml = subprocess.check_output([str(tools.analyzer),'manifest','print',str(apk)],env=self.env,text=True)
        checker = load_module('check-development-manifest.py','_size_contract_live_manifest')
        need(not checker.audit(xml), 'Final M1 manifest recheck failed')
        measured=profiler.profile(apk, project=self.project)
        need(measured['apk_observed'] == observed['apk_observed'] and measured['size_profile'] == observed['size_profile'], 'Profile differs from live APK observation')
        cmp_raw=read_bytes(comparison_path)
        refcopy=self.base/('reference-recheck-'+uuid.uuid4().hex+'.json')
        with refcopy.open('xb') as stream: stream.write(self.bundle['profile.json'])
        cmpcheck=self.base/('comparison-recheck-'+uuid.uuid4().hex+'.json')
        result=subprocess.run([sys.executable,'-B',str(ENGINE/'scripts/compare-apk-size-profile.py'),
            '--reference',str(refcopy),'--candidate',str(reports/'candidate-profile.json'),
            '--reference-sha256',self.reference['profile_sha256'],'--output',str(cmpcheck)],env=self.env,capture_output=True)
        need(result.returncode == 3 and read_json(cmpcheck) == decode(cmp_raw), 'Live comparator recheck failed')
        verdict = evaluate_records(self.context,self.root,self.reference,self.bundle,evidence,receipts,observed,profile_raw,
            decode(cmp_raw),result.returncode,actual_hash,Path(apk).stat().st_size,self.source,dependencies,self.toolchain,recipe,
            expected_pin,certificate_profile,cmp_raw)
        check_files(self.project,self.source['files']);check_files(self.project,self.source['injected_inputs'])
        write_new(output,verdict)


def prepare(context, source, output):
    Session(context).prepare(source, output)


def resolution_only(source, output, source_commit, engine_digest, api=None):
    api = api or Github()
    live, repo = live_platform(api, True)
    hex_value(source_commit,40); hex_value(engine_digest)
    engine_inv=source_inventory(ENGINE,os.environ['GITHUB_SHA'],repo['id'])
    need(sha(canonical(engine_inv['files'])) == engine_digest, 'Untrusted owner-dispatched engine')
    inventory=source_inventory(source,source_commit,repo['id'])
    output=Path(output);output.mkdir(mode=0o700)
    copied=output/'source';copied.mkdir()
    for e in inventory['files']:
        dest=copied/e['path'];dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(Path(source)/e['path'],dest)
    home=output/'gradle-home';home.mkdir(mode=0o700)
    env={k:v for k,v in os.environ.items() if k in {'PATH','HOME','JAVA_HOME','ANDROID_HOME','ANDROID_SDK_ROOT','SSL_CERT_FILE','HTTPS_PROXY','HTTP_PROXY','NO_PROXY'}}
    env.update(GRADLE_USER_HOME=str(home),TVHOME_SIZE_INPUT_OUTPUT=str(output/'resolved-draft.json'),LANG='C.UTF-8',TZ='UTC')
    command=[str(copied/'gradlew'),':app:writeDevelopmentSizeInputs','--init-script',str(ENGINE/'gradle/size-gate.init.gradle.kts'),
             '--write-verification-metadata','sha256','--no-build-cache','--no-configuration-cache','--console=plain']
    with (output/'resolution.log').open('xb') as log:
        result=subprocess.run(command,cwd=copied,env=env,stdout=log,stderr=subprocess.STDOUT)
    need(result.returncode == 0, 'Resolution-only Gradle task failed')
    verification=copied/'gradle/verification-metadata.xml'
    raw=read_bytes(verification)
    with (output/'DRAFT-verification-metadata.xml').open('xb') as stream:stream.write(raw)
    collector=read_json(output/'resolved-draft.json')
    write_new(output/'DRAFT-inputs.json',{'schema':'tvhome.size-resolution-draft/1','status':'unreviewed',
        'platform':live,'source_sha256':sha(canonical(inventory)),'verification_metadata_sha256':sha(raw),
        'collector':collector,'baseline_approved':False})
    return 3


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    resolve=commands.add_parser('resolution-only')
    resolve.add_argument('--source',type=Path,required=True);resolve.add_argument('--output',type=Path,required=True)
    resolve.add_argument('--source-commit',required=True);resolve.add_argument('--engine-manifest-sha256',required=True)
    pre = commands.add_parser('preflight'); pre.add_argument('--source', type=Path, required=True); pre.add_argument('--output', type=Path, required=True)
    pre.add_argument('--reference-capture', action='store_true'); pre.add_argument('--bootstrap', type=Path)
    for name in ('prepare', 'evaluate'):
        c = commands.add_parser(name); c.add_argument('--context', type=Path, required=True); c.add_argument('--output', type=Path, required=True)
        if name == 'prepare': c.add_argument('--source', type=Path, required=True)
        else:
            for flag in ('apk', 'evidence', 'comparison'): c.add_argument('--' + flag, type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'resolution-only':
            return resolution_only(args.source,args.output,args.source_commit,args.engine_manifest_sha256)
        if args.command == 'preflight':
            need(args.reference_capture == (args.bootstrap is not None), 'Explicit owner capture mode required')
            preflight(args.source, args.output, args.bootstrap)
        elif args.command == 'prepare':
            prepare(args.context, args.source, args.output)
        else:
            Session(args.context).evaluate_files(args.apk, args.evidence, args.comparison, args.output)
        return 0
    except Denied as exc:
        print(f'SIZE CONTRACT DENIED: {exc}', file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError, ET.ParseError, zipfile.BadZipFile) as exc:
        print(f'SIZE CONTRACT REJECTED: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
