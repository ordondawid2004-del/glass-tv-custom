#!/usr/bin/env python3
"""Read-only APK size profile. No baseline, budget, build, installation or upload."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import sys
import zipfile
import zlib

SPEC = importlib.util.spec_from_file_location('_size_profile_m0', Path(__file__).with_name('check-apk-isolation.py'))
gate = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = gate
SPEC.loader.exec_module(gate)
FORMAT = 'tvhome.apk-size-profile/1'
GROUPS = ('dex', 'native', 'resources', 'assets', 'other')
CONFIGS = ('app/build.gradle.kts', 'build.gradle.kts', 'settings.gradle.kts',
           'gradle/libs.versions.toml', 'gradle/wrapper/gradle-wrapper.properties', 'gradle.properties')


def require(condition, message):
    if not condition:
        raise gate.InspectionError(message)


def identity(path):
    s = path.stat()
    return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)


def composition(path):
    groups = {n: {'zip_bytes': 0, 'uncompressed_bytes': 0, 'file_count': 0} for n in GROUPS}
    abis = {}
    embedded = None
    with zipfile.ZipFile(path) as z:
        entries = z.infolist()
        require(len(entries) <= 100000, 'Too many ZIP entries')
        require(len({e.filename for e in entries}) == len(entries), 'Duplicate ZIP names')
        require(sum(e.file_size for e in entries) <= 2 * 1024**3, 'APK exceeds supported uncompressed size (2 GiB)')
        for e in entries:
            name = e.filename
            require(not e.flag_bits & 1, 'Encrypted APK entry')
            require(not name.startswith('/') and '\\' not in name and '..' not in PurePosixPath(name).parts,
                    'Unsafe APK entry name')
            require(e.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED), 'Unsupported APK compression')
            if e.is_dir():
                require(e.file_size == e.compress_size == 0, 'Invalid directory data')
                continue
            group = 'dex' if name.endswith('.dex') else 'native' if name.startswith('lib/') else 'resources' if name.startswith('res/') or name == 'resources.arsc' else 'assets' if name.startswith('assets/') else 'other'
            g = groups[group]
            for field, value in [('zip_bytes', e.compress_size), ('uncompressed_bytes', e.file_size), ('file_count', 1)]:
                g[field] += value
            if group == 'native':
                parts = name.split('/')
                require(len(parts) == 3 and parts[1] and parts[2].endswith('.so'), 'Ambiguous native library path')
                abi = abis.setdefault(parts[1], {'zip_bytes': 0, 'uncompressed_bytes': 0, 'file_count': 0})
                abi['zip_bytes'] += e.compress_size
                abi['uncompressed_bytes'] += e.file_size
                abi['file_count'] += 1
            if name == 'META-INF/com/android/build/gradle/app-metadata.properties':
                require(e.file_size <= 65536, 'Oversized embedded build metadata')
                text = z.read(e).decode('utf-8')
                match = re.search(r'^androidGradlePluginVersion=(.+)$', text, re.M)
                if match: embedded = match.group(1).strip()
        require(z.testzip() is None, 'APK CRC verification failed')
    size = path.stat().st_size
    overhead = size - sum(g['zip_bytes'] for g in groups.values())
    require(size > 0 and overhead >= 0, 'Inconsistent ZIP sizes')
    for g in [*groups.values(), *abis.values()]: g['apk_percent'] = round(g['zip_bytes'] * 100 / size, 8)
    return {'groups': groups, 'abi_distribution': dict(sorted(abis.items())),
            'container_alignment_signature_overhead': {'bytes': overhead, 'apk_percent': round(overhead * 100 / size, 8),
              'meaning': 'Residual APK bytes; container/alignment/signature not individually attributed'},
            'total_uncompressed_entry_bytes': sum(g['uncompressed_bytes'] for g in groups.values())}, embedded


def project_evidence(root):
    if root is None:
        return {'state': 'unavailable', 'apk_source_association': 'unverified'}
    require(root.is_dir(), 'Project directory missing')
    hashes = {}
    for rel in CONFIGS:
        p = root / rel
        require(p.is_file() and not p.is_symlink(), f'Project configuration missing or symlinked: {rel}')
        hashes[rel] = gate.file_hash(p)
    combined = hashlib.sha256(json.dumps(hashes, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return {'state': 'project_files_observed', 'configuration_sha256': hashes,
            'configuration_set_sha256': combined, 'apk_source_association': 'unverified',
            'source_reference': None, 'complete_source_tree_identified': False,
            'note': 'Current configuration bytes only; not proof that this APK was built from them. No Git HEAD inferred.'}


def external_claims(path, manifest, digest):
    if path is None: return {'state': 'unavailable', 'values': None}
    data = json.loads(path.read_text(encoding='utf-8'))
    fields = {'apk_sha256', 'application_id', 'version_code', 'variant', 'source_reference',
              'build_recipe', 'build_toolchain', 'dependencies_reference', 'packaging_reference'}
    require(isinstance(data, dict) and set(data) <= fields and data.get('apk_sha256') == digest,
            'Provenance must bind to this exact APK SHA-256; unsupported fields rejected')
    if 'application_id' in data: require(data['application_id'] == manifest.package, 'Contradictory provenance application ID')
    if 'version_code' in data: require(type(data['version_code']) is int and data['version_code'] == manifest.version_code, 'Contradictory provenance versionCode')
    for key, value in data.items():
        if key != 'version_code': require(isinstance(value, str) and value.strip(), 'Invalid provenance value')
    return {'state': 'externally_supplied_not_independently_verified', 'values': data,
            'note': 'Hash/ID/version consistency checked, not source/build authenticity. An M0 HEAD alone does not identify uncommitted M1 sources.'}


def profile(apk, sdk=None, project=None, provenance=None):
    require(apk.is_file(), 'Explicit APK path is missing or not a regular file')
    before_stat = identity(apk)
    before = gate.file_hash(apk)
    tools = gate.sdk_tools(sdk or os.environ.get('ANDROID_HOME') or os.environ.get('ANDROID_SDK_ROOT'))
    manifest, signers, inspected_hash = gate.inspect_apk(apk, tools)
    require(inspected_hash == before, 'APK changed before inspection completed')
    require(signers and all(re.fullmatch(r'[0-9a-f]{64}', s.sha256) for s in signers), 'Missing reliable certificate fingerprint')
    sizes, embedded_agp = composition(apk)
    project_info = project_evidence(project)
    claims = external_claims(provenance, manifest, before)
    result = {'schema': FORMAT,
      'apk_observed': {'application_id': manifest.package, 'version_name': manifest.version_name,
        'version_code': manifest.version_code, 'debuggable': manifest.debuggable, 'test_only': manifest.test_only,
        'file_bytes': before_stat[2], 'sha256': before, 'file_type': 'APK/ZIP',
        'validity': 'CRC, manifest and cryptographic signature verified',
        'signatures': [{'certificate_sha256': s.sha256, 'certificate_dn': s.dn,
                        'android_debug_certificate': s.debug} for s in signers],
        'embedded_build_metadata': {'android_gradle_plugin_version': embedded_agp,
            'state': 'apk_embedded_metadata' if embedded_agp else 'unavailable'}},
      'size_profile': sizes, 'project_evidence': project_info, 'external_provenance': claims,
      'analysis_tools': {'python': platform.python_version(), 'profile_script_sha256': gate.file_hash(Path(__file__)),
        'm0_inspector_sha256': gate.file_hash(Path(gate.__file__)),
        'sdk_tools': {name: {'path': str(p), 'launcher_sha256': gate.file_hash(p)}
                      for name,p in [('apkanalyzer',tools.analyzer),('apksigner',tools.signer)]},
        'sdk_build_tools_directory': tools.signer.parent.name,
        'note': 'Launcher hashes/version directory identify analysis tools, not the complete SDK or APK compiler toolchain'},
      'comparison_context': {'gradle_variant': None, 'build_type': None, 'build_recipe': None,
        'build_toolchain': None, 'dependencies': None, 'packaging': None,
        'state': 'unavailable_from_apk_alone',
        'requirements': ['explicitly approved comparable recipe/variant', 'trusted source/toolchain/dependency/packaging reference',
                         'separate signer and time-based versionCode differences', 'independent baseline approval']},
      'approval': {'baseline_approved': False, 'production_approved': False, 'regression_gate_executed': False}}
    require(identity(apk) == before_stat and gate.file_hash(apk) == before, 'APK changed during profiling')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('apk', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--sdk', type=Path)
    parser.add_argument('--project-root', type=Path)
    parser.add_argument('--provenance', type=Path, help='Optional external claims JSON; not trusted build provenance')
    args = parser.parse_args(argv)
    created = False
    try:
        apk = args.apk.expanduser().resolve()
        output = args.output.expanduser().resolve()
        require(not output.exists() and not args.output.is_symlink(), 'Output already exists; no overwrite permitted')
        require(output != apk and (args.provenance is None or output != args.provenance.resolve()), 'Output aliases input')
        if args.project_root:
            require(output not in [(args.project_root / p).resolve() for p in CONFIGS], 'Output aliases configuration')
        start = identity(apk)
        result = profile(apk, args.sdk, args.project_root, args.provenance)
        text = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n'
        with output.open('x', encoding='utf-8') as stream:
            created = True
            stream.write(text)
        require(identity(apk) == start and gate.file_hash(apk) == result['apk_observed']['sha256'], 'APK changed before report completion')
        print(f'Größenprofil erstellt: {output}; keine Baseline-/Produktionsfreigabe')
        return 0
    except (gate.InspectionError, OSError, ValueError, zipfile.BadZipFile, RuntimeError, NotImplementedError, zlib.error, EOFError) as exc:
        if created: output.unlink(missing_ok=True)
        print(f'FEHLER: APK-Größenprofil abgelehnt: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
