#!/usr/bin/env python3
"""Device-free M1 CI. Standard library only; never uploads or publishes itself."""
import argparse
import hashlib
import fnmatch
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
import xml.etree.ElementTree as ET
import zipfile

ENGINE = Path(__file__).resolve().parents[1]
ROOT = ENGINE
APP = 'io.github.ordondawid2004del.tvhome.dev'
VARIANT = 'developmentDebug'
# Maintained explicitly; count unittest cases, never subtests or discovered cases.
CI_TEST_COUNT = 56
APK_DIR = Path('app/build/outputs/apk/development/debug')
APK_NAME = 'app-development-debug.apk'
REQUIRED = {'tools', 'ci-tests', 'm0-tests', 'manifest-tests', 'gradle', 'jvm-results',
            'apk-output', 'm0-development', 'production-rejected', 'manifest', 'size', 'inventory',
            'reference-contract', 'source-inputs', 'dependency-artifacts', 'size-profile', 'size-comparison'}
JVM = {
    'Development': {
        'dev.glasslauncher.LayoutTest': 9,
        'dev.glasslauncher.dream.ScreensaverTimeoutTest': 4,
        'dev.glasslauncher.home.DevelopmentUiTest': 1,
        'dev.glasslauncher.system.DevelopmentIsolationTest': 15,
        'dev.glasslauncher.system.KeysServicePolicyTest': 10,
        'dev.glasslauncher.system.KeysServiceTest': 2,
    },
    'Original': {'dev.glasslauncher.system.ApkInstallTest': 23,
                 'dev.glasslauncher.system.RootProcessTest': 4},
}


class Rejected(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise Rejected(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def select_apk(root, started_ns):
    directory = root / APK_DIR
    candidates = sorted(directory.rglob('*.apk'))
    expected = directory / APK_NAME
    require(candidates == [expected] and expected.is_file() and not expected.is_symlink(),
            'Missing or ambiguous development APK; no fallback to another variant')
    require(expected.stat().st_mtime_ns >= started_ns, 'Stale APK: build did not recreate the expected output')
    metadata = json.loads((directory / 'output-metadata.json').read_text())
    elements = metadata.get('elements', [])
    require(metadata.get('applicationId') == APP and metadata.get('variantName') == VARIANT,
            'Wrong variant or application ID in output metadata')
    require(len(elements) == 1 and elements[0].get('outputFile') == APK_NAME,
            'Ambiguous APK output metadata')
    require(elements[0].get('versionName') == '0.3.0-m1-dev'
            and isinstance(elements[0].get('versionCode'), int) and elements[0]['versionCode'] > 0,
            'Unexpected M1 version metadata')
    return expected, elements[0]


def check_test_result(result, expected):
    require(result.testsRun == expected and result.wasSuccessful() and not result.skipped and not result.expectedFailures,
            f'Expected {expected} executed tests without skips; got {result.testsRun}, skips={len(result.skipped)}')


def read_test_report(path, expected):
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise Rejected(f'Unreadable test result: {path}') from exc
    require(isinstance(data, dict) and set(data) == {'tests', 'failures', 'errors', 'skipped'},
            'Invalid test result fields')
    require(all(type(v) is int and v >= 0 for v in data.values()), 'Invalid test result counts')
    require(data == {'tests': expected, 'failures': 0, 'errors': 0, 'skipped': 0},
            'Incomplete, failed or skipped test result')
    return data


def python_tests(pattern, expected, output):
    # Set opt-in before discovery so the M0 decorator cannot silently skip its 12 SDK tests.
    os.environ['APK_ISOLATION_INTEGRATION'] = '1'
    suite = unittest.TestLoader().discover(str(ROOT / 'scripts/tests'), pattern=pattern)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    data = {'tests': result.testsRun, 'failures': len(result.failures),
            'errors': len(result.errors), 'skipped': len(result.skipped)}
    output.write_text(json.dumps(data, indent=2) + '\n')
    check_test_result(result, expected)


# No Kotlin compiler is invoked: this conservative inventory is a review boundary,
# not JUnit discovery. Full source hashes catch even changes the scanner cannot model.
SCOPE_FILE = 'scripts/ci-jvm-scope.json'
DEV_FILTERS = tuple(JVM['Development']) + (
    'dev.glasslauncher.net.*', 'dev.glasslauncher.search.*',
    'dev.glasslauncher.featured.YouTubeFeedTest', 'dev.glasslauncher.featured.DiskFeedCacheTest',
    'dev.glasslauncher.data.ConfigStoreFullDiskTest', 'dev.glasslauncher.system.LaunchGateTest',
    'dev.glasslauncher.apps.AppArtTest',
)
FILTERS = {'Development': DEV_FILTERS, 'Original': tuple(JVM['Original'])}


def kotlin_code(source):
    """Remove comments/strings without losing offsets; nested Kotlin block comments supported."""
    out = list(source)
    i = 0
    while i < len(source):
        start = i
        if source.startswith('//', i):
            end = source.find('\n', i)
            i = len(source) if end < 0 else end
        elif source.startswith('/*', i):
            level = 1
            i += 2
            while i < len(source) and level:
                if source.startswith('/*', i): level += 1; i += 2
                elif source.startswith('*/', i): level -= 1; i += 2
                else: i += 1
            require(level == 0, 'Unterminated Kotlin comment')
        elif source.startswith('"""', i):
            end = source.find('"""', i + 3)
            require(end >= 0, 'Unterminated Kotlin raw string')
            while end + 3 < len(source) and source[end + 3] == '"': end += 1
            i = end + 3
        elif source[i] in ('"', "'"):
            quote = source[i]
            i += 1
            while i < len(source):
                if source[i] == '\\': i += 2
                elif source[i] == quote: i += 1; break
                else: i += 1
            else: raise Rejected('Unterminated Kotlin string')
        elif source[i] == '`':
            end = source.find('`', i + 1)
            require(end >= 0, 'Unterminated Kotlin identifier')
            i = end + 1
            continue
        else:
            i += 1
            continue
        for pos in range(start, i):
            if out[pos] != '\n': out[pos] = ' '
    return ''.join(out)


def source_facts(path):
    code = kotlin_code(path.read_text())
    package = re.search(r'\bpackage\s+([\w.]+)', code)
    require(package is not None, f'Missing package: {path}')
    depth = [0]
    for ch in code:
        depth.append(depth[-1] + (ch == '{') - (ch == '}'))
        require(depth[-1] >= 0, f'Ambiguous braces: {path}')
    require(depth[-1] == 0, f'Unbalanced braces: {path}')
    declarations = list(re.finditer(r'\b(?:class|interface|object)\s+([A-Za-z_]\w*)', code))
    top = [m for m in declarations if depth[m.start()] == 0]
    methods = list(re.finditer(r'@(?:org\.junit\.)?Test\b(?:\s*\([^)]*\))?\s+(?:(?:public|internal|suspend)\s+)*fun\s+(`[^`]+`|\w+)', code))
    classes = []
    for n, declaration in enumerate(top):
        end = top[n + 1].start() if n + 1 < len(top) else len(code)
        owned = [m.group(1).strip('`') for m in methods if declaration.end() < m.start() < end]
        if owned:
            # Nested test methods / custom runners require explicit treatment, not guessed FQNs.
            ambiguous = any(depth[m.start()] != 1 for m in methods if declaration.end() < m.start() < end)
            classes.append({'name': package.group(1) + '.' + declaration.group(1),
                            'methods': sorted(owned), 'ambiguous': ambiguous})
    return {'sha256': digest(path), 'declarations': [m.group(1) for m in declarations],
            'test_classes': classes,
            'unparsed_test_annotations': len(re.findall(r'@(?:org\.junit\.)?Test\b', code)) - len(methods)}



def settings_projects(root, metadata=None):
    """Approved Settings bytes + conservative flat literal includes; never execute Gradle."""
    path = root / 'settings.gradle.kts'
    require(path.is_file() and not path.is_symlink(), 'Missing or symlinked settings.gradle.kts')
    raw = path.read_bytes()
    if metadata is not None:
        require(isinstance(metadata, dict) and set(metadata) == {'path', 'sha256', 'projects', 'reason'},
                'Missing or invalid module-structure metadata')
        require(metadata['path'] == 'settings.gradle.kts'
                and isinstance(metadata['sha256'], str)
                and re.fullmatch(r'[0-9a-f]{64}', metadata['sha256'])
                and isinstance(metadata['reason'], str) and metadata['reason'].strip(),
                'Invalid Settings binding')
        require(hashlib.sha256(raw).hexdigest() == metadata['sha256'],
                'settings.gradle.kts changed: explicit module/inventory review required')
    text = raw.decode('utf-8')
    code = kotlin_code(text)
    projects = []
    for match in re.finditer(r'\b(include|includeFlat|includeBuild|projectDir|project|apply|evaluate)\b', code):
        require(match.group() == 'include', 'Unsupported Settings project binding: explicit review required')
        prefix = code[:match.start()]
        require(prefix.count('{') == prefix.count('}'), 'Conditional/nested module includes are not supported')
        end = code.find(')', match.end())
        require(end >= 0, 'Unparseable Settings include')
        call = text[match.start():end + 1]
        require(re.fullmatch(r'include\s*\(\s*"\:[A-Za-z][A-Za-z0-9_-]*"(?:\s*,\s*"\:[A-Za-z][A-Za-z0-9_-]*")*\s*\)', call),
                'Only literal flat include(":module") bindings are supported')
        projects.extend(re.findall(r'":([A-Za-z][A-Za-z0-9_-]*)"', call))
    require(projects and len(projects) == len(set(projects)), 'Missing or duplicate Settings projects')
    projects = sorted(projects)
    if metadata is not None:
        require(isinstance(metadata['projects'], list) and metadata['projects'] == projects,
                'Inventoried modules contradict settings.gradle.kts')
    for project in projects:
        require((root / project).is_dir() and not (root / project).is_symlink(),
                f'Missing, renamed or symlinked module: {project}')
    return projects


def test_sources(root, projects=None):
    sources = set()
    for module in settings_projects(root) if projects is None else projects:
        src = root / module / 'src'
        if not src.exists(): continue
        require(not src.is_symlink(), 'Symlinked source root needs review')
        for directory in src.iterdir():
            if not directory.is_dir(): continue
            if directory.name.startswith('test') or directory.name == 'androidTest' or (module == 'baselineprofile' and directory.name == 'main'):
                require(not directory.is_symlink(), 'Symlinked source set needs review')
                for p in directory.rglob('*'):
                    require(not p.is_symlink(), 'Symlinked test source needs review')
                    if p.suffix in ('.kt', '.java'):
                        require(not p.is_symlink() and p.suffix == '.kt', f'Unsupported source needs review: {p}')
                        sources.add(p.relative_to(root).as_posix())
    return sources


def selected(name, variant):
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in FILTERS[variant])


def validate_inventory(root):
    try:
        data = json.loads((root / SCOPE_FILE).read_text())
        require(set(data) == {'schema', 'sources', 'generator', 'module_structure'}, 'Invalid scope fields')
        require(data['schema'] == 1 and isinstance(data['sources'], list), 'Invalid scope schema')
        projects = settings_projects(root, data['module_structure'])
        entries = data['sources']
        paths = [e['path'] for e in entries]
        require(len(paths) == len(set(paths)) and set(paths) == test_sources(root, projects), 'Unclassified, removed or moved test source')
        expected = {'Development': {}, 'Original': {}}
        for entry in entries:
            require(set(entry) == {'path', 'role', 'reason', 'facts', 'classes'}, 'Invalid source entry')
            require(entry['role'] in ('A', 'B', 'C') and isinstance(entry['reason'], str) and entry['reason'].strip(), 'Missing source classification')
            facts = source_facts(root / entry['path'])
            require(entry['facts'] == facts, f'Changed test source requires review: {entry["path"]}')
            classes = entry['classes']
            require(isinstance(classes, list) and len(classes) == len(facts['test_classes']), 'Missing class classification')
            names = [c['name'] for c in classes]
            require(len(names) == len(set(names)) and set(names) == {c['name'] for c in facts['test_classes']}, 'Contradictory class inventory')
            for c in classes:
                require(set(c) == {'name', 'role', 'reason', 'variant'}, 'Invalid class fields')
                require(c['role'] in ('A', 'B', 'C') and isinstance(c['reason'], str) and c['reason'].strip(), 'Missing class role/reason')
                variants = [v for v in FILTERS if selected(c['name'], v)] if entry['path'].startswith('app/') else []
                variant = c['variant']
                require(variant in (None, 'Development', 'Original'), 'Invalid variant')
                if variant is not None:
                    require(variants == [variant], 'Wrong variant or duplicate selection')
                    require(entry['path'].startswith('app/src/test/') or entry['path'].startswith('app/src/testDebug/') or (variant == 'Development' and entry['path'].startswith('app/src/testDevelopment/')), 'Wrong source set')
                    require(c['role'] == ('A' if variant == 'Development' else 'B'), 'Role/variant contradiction')
                    f = next(f for f in facts['test_classes'] if f['name'] == c['name'])
                    require(not f['ambiguous'] and not facts['unparsed_test_annotations'], 'Ambiguous mandatory Kotlin tests')
                    require(c['name'] not in expected[variant], 'Duplicate required class')
                    expected[variant][c['name']] = f['methods']
                else:
                    require(not variants and c['role'] != 'A', 'Selected or A class omitted from execution')
            require(entry['role'] != 'C' or all(c['role'] == 'C' and c['variant'] is None for c in classes), 'Excluded source contains non-excluded class')
            require((entry['role'] == 'A') == any(c['role'] == 'A' for c in classes), 'Source/class role contradiction')
        for variant, core in JVM.items():
            require(set(core) <= set(expected[variant]), f'Missing critical {variant} classes')
        generator = data['generator']
        require(set(generator) == {'path', 'sha256', 'role', 'reason', 'class'}, 'Invalid generator record')
        require(generator['path'] == 'app/build.gradle.kts' and generator['role'] == 'B'
                and generator['class'] == 'com.github.takahirom.roborazzi.RoborazziPreviewParameterizedTests'
                and isinstance(generator['reason'], str) and generator['reason'].strip(), 'Invalid preview classification')
        require(digest(root / generator['path']) == generator['sha256'], 'Preview generator/build configuration changed')
        return expected
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise Rejected(f'Invalid JVM inventory: {exc}') from exc


def jvm_results(root, reports, started_ns, inventory=None):
    expected = validate_inventory(root) if inventory is None else inventory
    total = 0
    for variant, required in expected.items():
        found = {}
        paths = sorted((root / f'app/build/test-results/test{variant}DebugUnitTest').glob('TEST-*.xml'))
        for path in paths:
            try:
                require(not path.is_symlink() and path.stat().st_mtime_ns >= started_ns, 'Stale JVM test report')
                node = ET.parse(path).getroot()
                name = node.attrib['name']
                require(node.tag == 'testsuite' and name in required and name not in found, 'Unexpected, duplicate or wrong-variant test class')
                counts = {k: int(node.attrib[k]) for k in ('tests', 'failures', 'errors', 'skipped')}
                cases = node.findall('testcase')
                require(counts['tests'] == len(cases) and counts['tests'] > 0, 'Missing or contradictory test cases')
                require(all(counts[k] == 0 for k in ('failures', 'errors', 'skipped')), 'Failed or skipped JVM tests')
                require(all(not any(c.find(k) is not None for k in ('failure', 'error', 'skipped')) for c in cases), 'Hidden test failure/skip')
                identifiers = [(c.attrib['classname'], c.attrib['name']) for c in cases]
                require(all(c == name for c, _ in identifiers) and len(set(identifiers)) == len(identifiers), 'Ambiguous test identities')
                methods = {n.split('[', 1)[0] for _, n in identifiers}
                require(set(required[name]) == methods, f'Missing or unexpected methods: {name}')
                require(counts['tests'] >= JVM.get(variant, {}).get(name, 1), 'Critical class below required minimum')
                found[name] = counts['tests']
                shutil.copyfile(path, reports / f'{variant}-{path.name}')
            except (OSError, ValueError, KeyError, ET.ParseError) as exc:
                raise Rejected(f'Invalid JUnit result {path}: {exc}') from exc
        require(set(found) == set(required), f'Missing required {variant} classes')
        total += sum(found.values())
    return total

def approve(apk, out, state):
    """Only this boundary can create the upload directory; fail closed on any missing receipt."""
    target = out / 'approved'
    require(not target.exists(), 'Refusing existing approval directory')
    require(state.get('size_contract_verified') is True, 'No independently verified active size contract')
    require(state.get('checks') == {name: 'passed' for name in REQUIRED}, 'Missing or failed mandatory check')
    require(state.get('application_id') == APP and state.get('variant') == VARIANT,
            'Wrong development identity')
    require(re.fullmatch(r'[0-9a-f]{40}', state.get('commit', '')) is not None, 'Invalid commit')
    require(re.fullmatch(r'[0-9a-f]{64}', state.get('expected_certificate_sha256', '')) is not None,
            'Missing independently established certificate pin')
    require(apk.is_file() and digest(apk) == state.get('apk_sha256'), 'Missing or changed checked APK')
    try:
        target.mkdir()
        name = f'tvhome-development-{state["commit"]}-{VARIANT}.apk'
        copied = target / name
        shutil.copyfile(apk, copied)
        require(digest(copied) == state['apk_sha256'] == digest(apk), 'APK changed during staging')
        shutil.copytree(out / 'reports', target / 'reports')
        (target / 'SHA256SUMS').write_text(''.join(
            f'{digest(p)}  {p.relative_to(target).as_posix()}\n'
            for p in sorted(target.rglob('*')) if p.is_file()))
    except BaseException:
        shutil.rmtree(target)
        raise
    return target


def step_outputs(**values):
    """Only step-scoped protocol data, never reuse an earlier readiness signal."""
    if os.environ.get('GITHUB_OUTPUT'):
        with Path(os.environ['GITHUB_OUTPUT']).open('a') as stream:
            for name, value in values.items():
                require('\n' not in str(value) and '\r' not in str(value), 'Unsafe step output')
                stream.write(f'{name}={value}\n')


def execute(output, temp_dir, contract=None):
    step_outputs(apk_ready='false', diagnostics_ready='false', output_dir='', reports_dir='')
    # Output must be new: a failed run can never upload an older approved artifact.
    require(not output.exists(), 'Output directory already exists; use a new directory for each run')
    output.mkdir(parents=True)  # Exclusive reservation; never adopt a concurrently created output.
    reports = output / 'reports'
    reports.mkdir()
    diagnostic_run = {'id': uuid.uuid4().hex, 'started_ns': time.time_ns(),
                      'github_run_id': os.environ.get('GITHUB_RUN_ID'),
                      'github_run_attempt': os.environ.get('GITHUB_RUN_ATTEMPT'),
                      'github_job': os.environ.get('GITHUB_JOB'),
                      'github_sha': os.environ.get('GITHUB_SHA')}
    (reports / 'run.json').write_text(json.dumps({'status': 'started', **diagnostic_run}, indent=2) + '\n')
    step_outputs(output_dir=output, reports_dir=reports, diagnostics_ready='true')
    env = dict(os.environ)
    for name in list(env):
        if name.startswith('RELEASE_'):
            del env[name]
    state = {'status': 'rejected', 'development_only': True, 'production_approved': False,
             'application_id': APP, 'variant': VARIANT, 'checks': {}, 'diagnostic_run': diagnostic_run}

    def run(name, command, expected=0, destination=None):
        print(f'CI: {name}', flush=True)
        with (destination or reports / f'{name}.log').open('wb') as stream:
            result = subprocess.run([str(x) for x in command], cwd=ROOT, env=env,
                                    stdout=stream, stderr=subprocess.STDOUT)
        require(result.returncode == expected, f'{name}: exit {result.returncode}, expected {expected}; see reports')
        if name in REQUIRED and name != 'ci-tests':
            state['checks'][name] = 'passed'

    try:
        if contract is not None:
            contract.begin(ROOT, state, env, reports)
        else:
            state['commit'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
            state['working_tree_dirty'] = bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT))
        require(not env.get('GITHUB_REF', '').startswith('refs/tags/'), 'Development pipeline refuses tags')
        if env.get('GITHUB_SHA') and contract is None:
            require(env['GITHUB_SHA'] == state['commit'], 'Checkout does not match the Actions commit')
        require(sys.version_info >= (3, 10), 'Python 3.10+ required')
        sdk = Path(env.get('ANDROID_HOME') or env.get('ANDROID_SDK_ROOT') or '')
        java = Path(env.get('JAVA_HOME', '')) / 'bin/java'
        keytool = java.with_name('keytool')
        tools = {name: sdk / 'build-tools/37.0.0' / name for name in ('apksigner', 'aapt2')}
        tools['apkanalyzer'] = sdk / 'cmdline-tools/latest/bin/apkanalyzer'
        for path in [java, keytool, *tools.values()]:
            require(path.is_file() and os.access(path, os.X_OK), f'Missing executable: {path}')
        require(any(p.is_file() for p in (sdk / 'platforms').glob('android-37*/android.jar')), 'SDK platform 37 required')
        version = subprocess.run([str(java), '-version'], capture_output=True, text=True, check=True)
        require(re.search(r'version "21[.\"]', version.stderr + version.stdout), 'Java 21 required')
        (reports / 'tools.log').write_text(version.stderr + version.stdout + f'Python: {sys.version}\nSDK: {sdk}\n')
        state['checks']['tools'] = 'passed'
        ci_result = reports / 'ci-tests.json'
        run('ci-tests', [sys.executable, '-B', __file__, 'python-tests',
                         '--pattern', 'test_ci_development.py', '--expected', str(CI_TEST_COUNT),
                         '--result', ci_result])
        read_test_report(ci_result, CI_TEST_COUNT)
        state['checks']['ci-tests'] = 'passed'
        inventory = validate_inventory(ROOT)
        state['checks']['inventory'] = 'passed'
        env['APK_ISOLATION_INTEGRATION'] = '1'
        for name, pattern, count in [('m0-tests', 'test_apk_isolation.py', 37),
                                     ('manifest-tests', 'test_development_manifest.py', 11)]:
            run(name, [sys.executable, '-B', __file__, 'python-tests', '--pattern', pattern,
                       '--expected', str(count), '--result', reports / f'{name}.json'])
        for name, pattern, count in [('size-contract-tests', 'test_ci_size_gate.py', 40),
                                     ('profile-tests', 'test_apk_size_report.py', 19),
                                     ('comparison-tests', 'test_compare_apk_size_profile.py', 60)]:
            result_file = reports / f'{name}.json'
            run(name, [sys.executable, '-B', __file__, 'python-tests', '--pattern', pattern,
                       '--expected', str(count), '--result', result_file])
            read_test_report(result_file, count)
        # This key exists only in the runner's private temp directory, never in reports or the checkout.
        with tempfile.TemporaryDirectory(prefix='tvhome-ci-key-', dir=temp_dir) as private:
            key = Path(private) / 'development.keystore'
            certificate = Path(private) / 'development.der'
            run('key-generation', [keytool, '-genkeypair', '-keystore', key, '-storepass', 'android',
                                  '-keypass', 'android', '-alias', 'androiddebugkey', '-keyalg', 'RSA',
                                  '-keysize', '2048', '-validity', '30', '-dname',
                                  'CN=Android Debug,O=TV Home CI Development,C=DE', '-noprompt'])
            key.chmod(0o600)
            run('public-certificate', [keytool, '-exportcert', '-keystore', key, '-storepass', 'android',
                                       '-alias', 'androiddebugkey', '-file', certificate])
            der = certificate.read_bytes()
            pin = digest(certificate)  # Independent of the APK under inspection, never derived from it.
            state['expected_certificate_sha256'] = pin
            if contract is not None:
                contract.bind_signer(pin, der, digest(key))
            tasks = [':app:assembleDevelopmentDebug', ':app:lintDevelopmentDebug', ':app:testDevelopmentDebugUnitTest']
            for name in FILTERS['Development']:
                tasks += ['--tests', name]
            tasks += [':app:testOriginalDebugUnitTest']
            for name in FILTERS['Original']:
                tasks += ['--tests', name]
            if contract is not None:
                tasks += [':app:writeDevelopmentSizeInputs', *contract.gradle_flags()]
            started_ns = time.time_ns()
            run('gradle', [contract.gradle_executable() if contract is not None else ROOT / 'gradlew', *tasks, f'-PtvhomeDevKeystore={key}',
                           '--rerun-tasks', '--no-configuration-cache', '--console=plain'])
            state['jvm_tests'] = jvm_results(ROOT, reports, started_ns, inventory)
            state['checks']['jvm-results'] = 'passed'
            for suffix in ('xml', 'txt'):
                source = ROOT / f'app/build/reports/lint-results-developmentDebug.{suffix}'
                require(source.is_file() and source.stat().st_mtime_ns >= started_ns, 'Missing or stale lint report')
                shutil.copyfile(source, reports / source.name)
            apk, metadata = select_apk(ROOT, started_ns)
            state['checks']['apk-output'] = 'passed'
            state.update(apk_path=str(apk.relative_to(ROOT)), version=metadata['versionName'],
                         version_code=metadata['versionCode'], apk_sha256=digest(apk), apk_bytes=apk.stat().st_size)
            run('apk-badging', [tools['aapt2'], 'dump', 'badging', apk])
            badging = (reports / 'apk-badging.log').read_text()
            require("application-label:'TV Home – Entwicklung'" in badging, 'Unexpected development app label')
            state['label'] = 'TV Home – Entwicklung'
            args = [sys.executable, '-B', ENGINE / 'scripts/check-apk-isolation.py', apk,
                    '--sdk', sdk, '--expected-id', APP, '--expected-cert-sha256', pin]
            run('m0-development', [*args, '--mode', 'development'])
            run('production-rejected', [*args, '--mode', 'production'], expected=1)
            # Capture only XML on stdout; do not hide analyzer diagnostics in the manifest.
            with (reports / 'manifest.xml').open('wb') as stream, (reports / 'manifest-decode.log').open('wb') as err:
                decoded = subprocess.run([str(tools['apkanalyzer']), 'manifest', 'print', str(apk)],
                                         env=env, stdout=stream, stderr=err)
            require(decoded.returncode == 0, 'Cannot decode final APK manifest')
            run('manifest', [sys.executable, '-B', ENGINE / 'scripts/check-development-manifest.py', reports / 'manifest.xml'])
            if contract is None:
                # Legacy limit stays mandatory; no absent-root fallback can approve an APK.
                run('size', [ENGINE / 'scripts/apk-size-check', apk])
            require(digest(apk) == state['apk_sha256'], 'APK changed during verification')
        require(not key.exists(), 'Temporary private key cleanup incomplete')
        if contract is not None:
            if not contract.finish(apk, state, pin, der):
                require(contract.capture, 'Missing candidate contract verdict')
                return 3  # Measurement only; never stage or set readiness.
        state['status'] = 'passed'
        (reports / 'summary.json').write_text(json.dumps(state, indent=2) + '\n')
        approve(apk, output, state)
        # Readiness is emitted only after successful checks, staging and key cleanup.
        # Build children never receive the parent Actions output channel.
        step_outputs(apk_ready='true')
        return 0
    except (Rejected, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, ET.ParseError, zipfile.BadZipFile) as exc:
        state['status'] = 'rejected'
        state['error'] = str(exc)
        if (output / 'approved').exists():
            shutil.rmtree(output / 'approved')
        print(f'CI REJECTED: {exc}', file=sys.stderr)
        return 1
    finally:
        (reports / 'summary.json').write_text(json.dumps(state, indent=2) + '\n')


def main():
    global ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    pipeline = commands.add_parser('run')
    pipeline.add_argument('--output', type=Path, required=True)
    pipeline.add_argument('--temp-dir', type=Path, required=True)
    pipeline.add_argument('--project-root', type=Path)
    pipeline.add_argument('--size-contract-context', type=Path)
    capture = commands.add_parser('reference-capture')
    capture.add_argument('--output', type=Path, required=True)
    capture.add_argument('--temp-dir', type=Path, required=True)
    capture.add_argument('--project-root', type=Path, required=True)
    capture.add_argument('--size-contract-context', type=Path, required=True)
    commands.add_parser('inventory')
    jvm = commands.add_parser('jvm-results')
    jvm.add_argument('--reports', type=Path, required=True)
    jvm.add_argument('--started-ns', type=int, required=True)
    tests = commands.add_parser('python-tests')
    tests.add_argument('--pattern', required=True)
    tests.add_argument('--expected', type=int, required=True)
    tests.add_argument('--result', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == 'inventory':
            print(json.dumps(validate_inventory(ROOT), indent=2))
            return 0
        if args.command == 'jvm-results':
            args.reports.mkdir(parents=True, exist_ok=True)
            print(f'JVM cases verified: {jvm_results(ROOT, args.reports, args.started_ns)}')
            return 0
        if args.command == 'python-tests':
            python_tests(args.pattern, args.expected, args.result)
            return 0
        contract = None
        if args.project_root is not None:
            ROOT = args.project_root.resolve()
        if args.size_contract_context is not None:
            import importlib.util
            spec = importlib.util.spec_from_file_location('_live_size_contract', ENGINE / 'scripts/ci-size-gate.py')
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            contract = module.Session(args.size_contract_context)
            require(contract.capture == (args.command == 'reference-capture'), 'Wrong contract mode')
        return execute(args.output.resolve(), args.temp_dir.resolve(), contract)
    except (Rejected, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
        print(f'CI REJECTED: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
