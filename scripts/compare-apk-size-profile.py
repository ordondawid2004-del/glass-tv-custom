#!/usr/bin/env python3
"""Read two pinned APK size profiles; report differences, never grant CI approval."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys

PROFILE_SCHEMA = 'tvhome.apk-size-profile/1'
REPORT_SCHEMA = 'tvhome.apk-size-comparison/1'
DEVELOPMENT_ID = 'io.github.ordondawid2004del.tvhome.dev'
GROUPS = ('dex', 'resources', 'native', 'assets', 'other')
CONFIGS = {'app/build.gradle.kts', 'build.gradle.kts', 'settings.gradle.kts',
           'gradle/libs.versions.toml', 'gradle/wrapper/gradle-wrapper.properties', 'gradle.properties'}
CLAIMS = {'apk_sha256', 'application_id', 'version_code', 'variant', 'source_reference',
          'build_recipe', 'build_toolchain', 'dependencies_reference', 'packaging_reference'}
MAX_INPUT_BYTES = 2 * 1024**2
# Schema /1 has no authenticated build provenance. Even a complete comparison is
# deliberately nonzero, so this diagnostic CLI cannot be used as an approval gate.
ANALYSIS_ONLY = 3


class Rejected(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise Rejected(message)


def object_fields(value, fields, label):
    require(type(value) is dict and set(value) == set(fields), f'{label}: missing/unknown fields')


def string(value, label):
    require(type(value) is str and bool(value.strip()) and not any(ord(c) < 32 for c in value),
            f'{label}: nonempty string without control characters required')


def digest(value, label):
    require(type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None,
            f'{label}: lowercase SHA-256 required')


def integer(value, label, minimum=0):
    require(type(value) is int and minimum <= value <= 2**63 - 1, f'{label}: invalid integer')


def boolean(value, label):
    require(type(value) is bool, f'{label}: boolean required')


def percentage(value, numerator, denominator, label):
    require(type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 100,
            f'{label}: invalid percentage')
    require(abs(value - round(numerator * 100 / denominator, 8)) <= 0.00000001,
            f'{label}: inconsistent percentage')


def size_group(g, total, label):
    object_fields(g, {'zip_bytes', 'uncompressed_bytes', 'file_count', 'apk_percent'}, label)
    for key in ('zip_bytes', 'uncompressed_bytes', 'file_count'):
        integer(g[key], label + '.' + key)
    require(g['file_count'] <= 100000, f'{label}: too many files')
    require(g['file_count'] != 0 or g['zip_bytes'] == g['uncompressed_bytes'] == 0,
            f'{label}: bytes without files')
    percentage(g['apk_percent'], g['zip_bytes'], total, label)


def validate(profile):
    """Validate the actual, closed /1 producer structure; never attest its claims."""
    object_fields(profile, {'schema', 'apk_observed', 'size_profile', 'project_evidence',
                           'external_provenance', 'analysis_tools', 'comparison_context', 'approval'}, 'profile')
    require(profile['schema'] == PROFILE_SCHEMA, 'Unsupported profile schema')
    a = profile['apk_observed']
    object_fields(a, {'application_id', 'version_name', 'version_code', 'debuggable', 'test_only',
                      'file_bytes', 'sha256', 'file_type', 'validity', 'signatures', 'embedded_build_metadata'}, 'apk')
    string(a['application_id'], 'application_id')
    require(re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+', a['application_id']) is not None,
            'Invalid application ID')
    string(a['version_name'], 'version_name')
    integer(a['version_code'], 'version_code', 1)
    integer(a['file_bytes'], 'file_bytes', 1)  # A valid APK cannot be empty: no division by zero.
    digest(a['sha256'], 'apk SHA')
    for field in ('debuggable', 'test_only'):
        boolean(a[field], field)
    require(a['file_type'] == 'APK/ZIP' and a['validity'] == 'CRC, manifest and cryptographic signature verified',
            'Unsupported APK validity claim')
    signatures = a['signatures']
    require(type(signatures) is list and 0 < len(signatures) <= 16, 'Missing/ambiguous signatures')
    seen = set()
    for sig in signatures:
        object_fields(sig, {'certificate_sha256', 'certificate_dn', 'android_debug_certificate'}, 'signature')
        digest(sig['certificate_sha256'], 'certificate SHA')
        string(sig['certificate_dn'], 'certificate DN')
        boolean(sig['android_debug_certificate'], 'debug certificate')
        debug = re.search(r'(?:^|,)\s*CN\s*=\s*"?Android Debug"?\s*(?:,|$)', sig['certificate_dn'], re.I) is not None
        require(debug == sig['android_debug_certificate'], 'Contradictory debug certificate information')
        require(sig['certificate_sha256'] not in seen, 'Duplicate signer')
        seen.add(sig['certificate_sha256'])
    embedded = a['embedded_build_metadata']
    object_fields(embedded, {'state', 'android_gradle_plugin_version'}, 'embedded metadata')
    if embedded['state'] == 'unavailable':
        require(embedded['android_gradle_plugin_version'] is None, 'Contradictory embedded metadata')
    else:
        require(embedded['state'] == 'apk_embedded_metadata', 'Unknown embedded metadata state')
        string(embedded['android_gradle_plugin_version'], 'embedded AGP')

    sizes = profile['size_profile']
    object_fields(sizes, {'groups', 'abi_distribution', 'container_alignment_signature_overhead',
                          'total_uncompressed_entry_bytes'}, 'sizes')
    object_fields(sizes['groups'], GROUPS, 'size groups')
    for name, g in sizes['groups'].items():
        size_group(g, a['file_bytes'], name)
    overhead = sizes['container_alignment_signature_overhead']
    object_fields(overhead, {'bytes', 'apk_percent', 'meaning'}, 'container overhead')
    integer(overhead['bytes'], 'overhead')
    string(overhead['meaning'], 'overhead meaning')
    require(overhead['meaning'] == 'Residual APK bytes; container/alignment/signature not individually attributed',
            'Unsupported overhead interpretation')
    percentage(overhead['apk_percent'], overhead['bytes'], a['file_bytes'], 'overhead percentage')
    require(sum(g['zip_bytes'] for g in sizes['groups'].values()) + overhead['bytes'] == a['file_bytes'],
            'APK/group/container sum mismatch')
    integer(sizes['total_uncompressed_entry_bytes'], 'total uncompressed bytes')
    require(sum(g['uncompressed_bytes'] for g in sizes['groups'].values()) == sizes['total_uncompressed_entry_bytes']
            <= 2 * 1024**3, 'Uncompressed sum mismatch/unsupported size')
    require(sum(g['file_count'] for g in sizes['groups'].values()) <= 100000, 'Too many total files')
    require(type(sizes['abi_distribution']) is dict, 'ABI object required')
    for abi, g in sizes['abi_distribution'].items():
        require(re.fullmatch(r'[A-Za-z0-9_+\-]+', abi) is not None, 'Invalid ABI name')
        size_group(g, a['file_bytes'], 'ABI ' + abi)
        require(g['file_count'] > 0, 'Empty ABI entry')
    for key in ('zip_bytes', 'uncompressed_bytes', 'file_count'):
        require(sum(g[key] for g in sizes['abi_distribution'].values()) == sizes['groups']['native'][key],
                'Native/ABI sum mismatch: ' + key)

    p = profile['project_evidence']
    require(type(p) is dict, 'Project evidence object required')
    if p.get('state') == 'unavailable':
        object_fields(p, {'state', 'apk_source_association'}, 'unavailable project')
    else:
        object_fields(p, {'state', 'configuration_sha256', 'configuration_set_sha256', 'apk_source_association',
                          'source_reference', 'complete_source_tree_identified', 'note'}, 'project evidence')
        require(p['state'] == 'project_files_observed' and p['source_reference'] is None
                and p['complete_source_tree_identified'] is False, 'Unsupported project provenance assertion')
        object_fields(p['configuration_sha256'], CONFIGS, 'configuration hashes')
        for v in p['configuration_sha256'].values():
            digest(v, 'configuration SHA')
        digest(p['configuration_set_sha256'], 'configuration set SHA')
        require(hashlib.sha256(json.dumps(p['configuration_sha256'], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
                == p['configuration_set_sha256'], 'Configuration set SHA mismatch')
        string(p['note'], 'project note')
    require(p['apk_source_association'] == 'unverified', 'Unsupported APK/source association')

    e = profile['external_provenance']
    require(type(e) is dict, 'External provenance object required')
    if e.get('state') == 'unavailable':
        object_fields(e, {'state', 'values'}, 'unavailable external provenance')
        require(e['values'] is None, 'Unavailable claims must be null')
    else:
        object_fields(e, {'state', 'values', 'note'}, 'external provenance')
        require(e['state'] == 'externally_supplied_not_independently_verified', 'Unsupported provenance trust state')
        string(e['note'], 'external note')
        values = e['values']
        require(type(values) is dict and set(values) <= CLAIMS and 'apk_sha256' in values, 'Invalid claim fields')
        for key, value in values.items():
            if key == 'version_code':
                integer(value, key, 1)
            else:
                string(value, key)
        digest(values['apk_sha256'], 'claim APK SHA')
        require(values['apk_sha256'] == a['sha256'], 'Claim/APK SHA mismatch')
        for claim, observed in [('application_id', 'application_id'), ('version_code', 'version_code')]:
            require(claim not in values or values[claim] == a[observed], 'Contradictory claim: ' + claim)
        for key in ('source_reference', 'dependencies_reference', 'packaging_reference'):
            if key in values and re.fullmatch(r'[0-9A-Fa-f]{64}', values[key]):
                digest(values[key], key)

    t = profile['analysis_tools']
    object_fields(t, {'python', 'profile_script_sha256', 'm0_inspector_sha256', 'sdk_tools',
                      'sdk_build_tools_directory', 'note'}, 'analysis tools')
    for key in ('python', 'sdk_build_tools_directory', 'note'):
        string(t[key], 'analysis.' + key)
    for key in ('profile_script_sha256', 'm0_inspector_sha256'):
        digest(t[key], key)
    object_fields(t['sdk_tools'], {'apkanalyzer', 'apksigner'}, 'SDK analysis tools')
    for v in t['sdk_tools'].values():
        object_fields(v, {'path', 'launcher_sha256'}, 'SDK launcher')
        string(v['path'], 'SDK path')
        digest(v['launcher_sha256'], 'launcher SHA')
    c = profile['comparison_context']
    unknowns = {'gradle_variant', 'build_type', 'build_recipe', 'build_toolchain', 'dependencies', 'packaging'}
    object_fields(c, unknowns | {'state', 'requirements'}, 'comparison context')
    require(c['state'] == 'unavailable_from_apk_alone' and all(c[k] is None for k in unknowns),
            'Unsupported claim of verified comparison context')
    require(type(c['requirements']) is list and len(c['requirements']) == 4, 'Invalid requirements')
    for value in c['requirements']:
        string(value, 'comparison requirement')
    require(len(set(c['requirements'])) == 4, 'Ambiguous duplicate requirements')
    object_fields(profile['approval'], {'baseline_approved', 'production_approved', 'regression_gate_executed'}, 'approval')
    require(all(v is False for v in profile['approval'].values()), 'Profile must not assert approval')
    return profile


def no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'Duplicate JSON field: ' + key)
        result[key] = value
    return result


def reject_constant(value):
    raise Rejected('Non-finite JSON number: ' + value)


def fingerprint(s):
    return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns


def read_input(path):
    # O_NONBLOCK avoids blocking on a FIFO before the regular-file check.
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        start = os.fstat(stream.fileno())
        require(stat.S_ISREG(start.st_mode) and start.st_size <= MAX_INPUT_BYTES,
                'Input must be a regular profile <= 2 MiB')
        raw = stream.read(MAX_INPUT_BYTES + 1)
        require(len(raw) <= MAX_INPUT_BYTES and fingerprint(start) == fingerprint(os.fstat(stream.fileno())),
                'Input changed during read')
    require(fingerprint(path.stat()) == fingerprint(start), 'Input path changed during read')
    return raw, fingerprint(start)


def load_profile(path):
    raw, stamp = read_input(path)
    value = json.loads(raw.decode('utf-8'), object_pairs_hook=no_duplicates, parse_constant=reject_constant)
    return validate(value), hashlib.sha256(raw).hexdigest(), stamp


def unchanged(path, expected_hash, stamp):
    raw, now = read_input(path)
    require(now == stamp and hashlib.sha256(raw).hexdigest() == expected_hash, 'Input changed during comparison')


def claims(profile):
    return profile['external_provenance']['values'] or {}


def claim_relation(a, b, key):
    unknown = {'unknown', 'unavailable', 'unbekannt', '?', 'null', 'none'}
    if any(key not in c or c[key].strip().lower() in unknown for c in (a, b)):
        return 'unknown'
    return 'same_claim' if a[key] == b[key] else 'different_claim'


def comparison(reference, candidate, reference_hash, candidate_hash):
    """Compare validated reports; equality of claims is never authenticated provenance."""
    a, b = reference['apk_observed'], candidate['apk_observed']
    x, y = claims(reference), claims(candidate)
    reasons, missing = [], []
    if a['application_id'] != DEVELOPMENT_ID or b['application_id'] != DEVELOPMENT_ID:
        reasons.append('Only the isolated Development identity is eligible; original/other identity rejected')
    for side, observed, values in [('reference', a, x), ('candidate', b, y)]:
        if ('variant' in values and values['variant'] != 'developmentDebug') or not observed['debuggable'] or observed['test_only']:
            reasons.append(side + ': not the documented developmentDebug profile/flags')
        for key in ('variant', 'build_recipe', 'build_toolchain', 'packaging_reference',
                    'source_reference', 'dependencies_reference'):
            v = values.get(key)
            if v is None or v.strip().lower() in {'unknown', 'unavailable', 'unbekannt', '?', 'null', 'none'}:
                missing.append(side + '.' + key)
    for key in ('variant', 'build_recipe', 'build_toolchain', 'packaging_reference'):
        if key in x and key in y and x[key] != y[key]:
            reasons.append('Different documented ' + key)
    for side, profile in [('reference', reference), ('candidate', candidate)]:
        if profile['project_evidence']['state'] == 'unavailable':
            missing.append(side + '.project_configuration')
    configs_a = reference['project_evidence'].get('configuration_sha256', {})
    configs_b = candidate['project_evidence'].get('configuration_sha256', {})
    config_changes = sorted(k for k in configs_a.keys() | configs_b.keys() if configs_a.get(k) != configs_b.get(k))
    if config_changes:
        reasons.append('Different observed build configuration; semantic compatibility not proven')
    for field in ('profile_script_sha256', 'm0_inspector_sha256'):
        if reference['analysis_tools'][field] != candidate['analysis_tools'][field]:
            reasons.append('Different profile producer/inspector: ' + field)
    agp_a, agp_b = a['embedded_build_metadata']['android_gradle_plugin_version'], b['embedded_build_metadata']['android_gradle_plugin_version']
    if agp_a is None or agp_b is None:
        missing.append('embedded_AGP')
    elif agp_a != agp_b:
        reasons.append('Different APK-reported AGP versions')
    sig_a = {v['certificate_sha256']: v for v in a['signatures']}
    sig_b = {v['certificate_sha256']: v for v in b['signatures']}
    for pin in sig_a.keys() & sig_b.keys():
        require(sig_a[pin] == sig_b[pin], 'Contradictory certificate metadata for same fingerprint')
    if sig_a.keys() != sig_b.keys():
        reasons.append('Different signer fingerprints; no comparable signing policy supplied')
    if a['version_name'] != b['version_name']:
        reasons.append('Different versionName; metadata comparability requires review')
    same_apk = a['sha256'] == b['sha256']
    if same_apk:
        require(a == b and reference['size_profile'] == candidate['size_profile'],
                'Same APK hash with contradictory observed metadata/sizes')
    sa, sb = reference['size_profile'], candidate['size_profile']
    ga, gb = sa['groups'], sb['groups']
    group_deltas = {g: {key: gb[g][key] - ga[g][key] for key in ('zip_bytes', 'uncompressed_bytes', 'file_count')}
                    for g in GROUPS}
    if sa['abi_distribution'].keys() != sb['abi_distribution'].keys():
        reasons.append('Different observed ABI sets; packaging compatibility not established')
    abi_deltas = {}
    for abi in sorted(sa['abi_distribution'].keys() | sb['abi_distribution'].keys()):
        av, bv = sa['abi_distribution'].get(abi, {}), sb['abi_distribution'].get(abi, {})
        abi_deltas[abi] = {'presence': 'both' if av and bv else 'added' if bv else 'removed',
                          **{key: bv.get(key, 0) - av.get(key, 0) for key in ('zip_bytes', 'uncompressed_bytes', 'file_count')}}
    source_relation = claim_relation(x, y, 'source_reference')
    dependency_relation = claim_relation(x, y, 'dependencies_reference')
    if dependency_relation == 'different_claim':
        reasons.append('Different dependency references; runtime composition compatibility requires review')
    if reasons:
        classification = 'incompatible_documentation'
    elif missing:
        classification = 'insufficient_documentation'
    elif source_relation == 'same_claim':
        classification = 'matching_recipe_same_source_claim'
    else:
        classification = 'matching_recipe_changed_source_claim'
    delta = b['file_bytes'] - a['file_bytes']
    return {'schema': REPORT_SCHEMA,
            'inputs': {'reference_profile_sha256': reference_hash, 'candidate_profile_sha256': candidate_hash,
                       'reference_apk_sha256': a['sha256'], 'candidate_apk_sha256': b['sha256']},
            'identity': {'reference_application_id': a['application_id'], 'candidate_application_id': b['application_id'],
                         'reference_variant_claim': x.get('variant'), 'candidate_variant_claim': y.get('variant')},
            'sizes': {'reference_bytes': a['file_bytes'], 'candidate_bytes': b['file_bytes'], 'delta_bytes': delta,
                      'delta_percent_of_reference': delta * 100 / a['file_bytes'], 'groups': group_deltas,
                      'container_delta_bytes': sb['container_alignment_signature_overhead']['bytes'] - sa['container_alignment_signature_overhead']['bytes'],
                      'total_uncompressed_delta_bytes': sb['total_uncompressed_entry_bytes'] - sa['total_uncompressed_entry_bytes'],
                      'abi_differences': abi_deltas},
            'metadata': {'reference_version_code': a['version_code'], 'candidate_version_code': b['version_code'],
                         'version_code_delta': b['version_code'] - a['version_code'],
                         'version_code_interpretation': 'Variable build metadata; size effects not attributable from aggregate profiles alone',
                         'reference_version_name': a['version_name'], 'candidate_version_name': b['version_name'],
                         'reference_signers': a['signatures'], 'candidate_signers': b['signatures'],
                         'signer_fingerprints_equal': sig_a.keys() == sig_b.keys(), 'same_apk_hash': same_apk,
                         'source_reference_relation': source_relation, 'dependency_reference_relation': dependency_relation,
                         'reference_claims': x, 'candidate_claims': y, 'configuration_changes': config_changes},
            'comparability': {'status': classification, 'reasons': reasons, 'missing_information': missing,
                              'scope': 'Documented recipe/size observations only; not authenticated build provenance'},
            'provenance_verification': {'reference_profile_hash_matches_expected': True,
                                        'candidate_profile_hash_is_trusted_anchor': False,
                                        'apk_signatures_reverified': False, 'build_source_verified': False,
                                        'state': 'profile_integrity_only; build claims remain unverified',
                                        'note': 'A supplied profile hash binds bytes; it does not approve a baseline or authenticate APK/source association'},
            'restrictions': {'baseline_approved': False, 'production_approved': False, 'size_regression_approved': False,
                             'ci_gate_passed': False, 'upload_approved': False, 'fire_os_compatibility_proven': False,
                             'growth_tolerance_bytes': None, 'baseline_updated': False}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', type=Path, required=True)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--reference-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    output, created_identity = args.output.expanduser().absolute(), None
    try:
        digest(args.reference_sha256, 'expected reference SHA')
        reference_path, candidate_path = args.reference.expanduser().absolute(), args.candidate.expanduser().absolute()
        require(not output.exists() and not output.is_symlink(), 'Output exists; overwrite forbidden')
        require(output.resolve() not in {reference_path.resolve(), candidate_path.resolve()}, 'Output aliases input')
        ref, rh, rs = load_profile(reference_path)
        require(rh == args.reference_sha256, 'Reference profile SHA-256 mismatch')
        cand, ch, cs = load_profile(candidate_path)
        result = comparison(ref, cand, rh, ch)
        unchanged(reference_path, rh, rs)
        unchanged(candidate_path, ch, cs)
        payload = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n'
        fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            created_identity = fingerprint(os.fstat(stream.fileno()))[:2]
            stream.write(payload)
        unchanged(reference_path, rh, rs)
        unchanged(candidate_path, ch, cs)
        print(f'Analysebericht: {output}; {result["comparability"]["status"]}. Keine CI-/Baseline-/Uploadfreigabe; Exit 3.')
        return ANALYSIS_ONLY
    except (OSError, ValueError, TypeError, RecursionError, OverflowError) as exc:
        if created_identity is not None:
            try:
                if fingerprint(output.lstat())[:2] == created_identity:
                    output.unlink()
            except OSError:
                pass
        print(f'FEHLER: Profilvergleich abgelehnt: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
