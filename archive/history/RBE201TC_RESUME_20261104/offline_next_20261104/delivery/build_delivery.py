"""Generate a self-contained viewer and exact-source archive; no device actions."""
from pathlib import Path
from urllib.parse import urlsplit, unquote
from datetime import datetime, timezone
import ast
import base64
import csv
import difflib
import hashlib
import html
import io
import json
import posixpath
import re
import zipfile

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
R = 'outputs/RBE201TC_RESUME_20261104/'
N = R + 'offline_next_20261104/'
P = 'work/device-console-v2/'
OLD_PREFIX = 'RBE201TC_OFFLINE_RESULTS_20261104/'
old_zip = zipfile.ZipFile(ROOT / (R + 'RBE201TC_OFFLINE_RESULTS_20261104.zip'))
paths = set(json.loads(old_zip.read(OLD_PREFIX + 'FILES_SHA256.json')))
# Explicit inventory from the workspace glob result; no directory-wide archive.
groups = {
 'matlab': '''environment-probes.log spotlight-probes.log mh_lint.log search-observations.json source-integrity.log test-inventory.json test-inventory.txt source_hashes.sha256 execution-status.json REPORT.md self-check.log''',
 'materials': '''README.md MATERIALS_STATUS_ZH.md FUNCTION_EVIDENCE_MATRIX_ZH.md ARCHIVE_REPRO_CHECKLIST_ZH.md build_materials_pack.py MATERIALS_INDEX.csv FUNCTION_EVIDENCE_MATRIX.csv MATERIAL_DISCOVERY_UPDATE.md VALIDATION.md''',
 'materials/templates': '''BOM_EMPTY.csv CAD_DEPENDENCIES_EMPTY.csv MANUFACTURING_EMPTY.csv MASS_MEASUREMENTS_EMPTY.csv BATTERY_LABEL_EMPTY.csv ENERGY_RUNS_EMPTY.csv ENERGY_SAMPLES_EMPTY.csv LCA_SOURCE_FACTS_EMPTY.csv STUDENT_CONTRIBUTION_EMPTY.csv FUNCTION_CLAIMS_EMPTY.csv SOURCE_RECEIPT_EMPTY.csv ARCHIVE_MANIFEST_EMPTY.csv REPRODUCTION_RUN_EMPTY.csv AI_ASSISTANCE_REGISTER_EMPTY.csv''',
 'mission': '''unittest_mission_plan.log unittest_line_calibration.log QA_REPORT.md''',
 'deployment': '''connection_preflight_20261104T2016.json unit-tests.log audited_six_file_candidate.zip prepared-bundle-status.json remote-preflight.stdout.log remote-preflight.stderr.log backend-full-regression.log frontend-full-regression.log backend-full-regression-rerun.log unit-tests-reviewed.log backend-final.log browser-recheck.log connection_refresh_round1.json README.md REVIEW_RESOLUTION.md''',
 'deployment/browser_recheck': '''browser_fixture.py browser_qa.cjs software-fixture-acquisition.png software-fixture-acquisition-mobile.png browser-summary.json''',
 'delivery': '''README.md build_delivery.py MATERIAL_VERSION_LEDGER.json FINAL_SOURCE_CHECK.json test_portability_changes.diff''',
 'delivery/history': '''CONTINUE_HERE_at_material_check.md''',
}
for group, names in groups.items():
    paths.update(N + group + '/' + name for name in names.split())
paths.update(P + name for name in ('backend/mission_plan.py', 'backend/tests/test_mission_plan.py', 'MISSION_PREPARATION.md', 'tools/deploy_web_acquisition.py', 'tools/tests/test_deploy_web_acquisition.py'))
paths.add(R + 'build_openable_delivery.py')
sha = lambda data: hashlib.sha256(data).hexdigest()
rows = list(csv.DictReader(io.StringIO((ROOT / (N + 'materials/MATERIALS_INDEX.csv')).read_text(encoding='utf-8-sig'))))
material_checks = []
snapshot = N + 'delivery/history/CONTINUE_HERE_at_material_check.md'
for row in rows:
    if row['PresenceState'] != '存在':
        continue
    match = re.fullmatch(r'\[[^\]]+\]\(<([^>]+)>\)', row['SourceLink'])
    assert match, row['MaterialID']
    source = unquote(urlsplit(match[1]).path)
    assert not source.startswith('/') and '..' not in Path(source).parts
    paths.add(source)
    checked = snapshot if row['MaterialID'] == 'M004' else source
    data = (ROOT / checked).read_bytes()
    assert sha(data) == row['SHA256AtCheck'], (row['MaterialID'], checked)
    assert len(data) == int(row['BytesAtCheck'])
    material_checks.append({'id': row['MaterialID'], 'original_source': source, 'verified_version': checked, 'sha256': sha(data)})
assert len(rows) == 41 and len(material_checks) == 31
ledger = {'checked_at': datetime.now(timezone.utc).isoformat(), 'material_id': 'M004',
          'original_source': R + 'CONTINUE_HERE.md', 'version_at_material_check': snapshot,
          'old_sha256': sha((ROOT / snapshot).read_bytes()),
          'latest_sha256': sha((ROOT / (R + 'CONTINUE_HERE.md')).read_bytes()),
          'note': 'Original index is a dated record. Old bytes preserved; old SHA not rewritten.'}
with (OUT / 'MATERIAL_VERSION_LEDGER.json').open('x') as f:
    json.dump(ledger, f, ensure_ascii=False, indent=2)
diff = ''
for name in ('backend/tests/test_cli.py', 'backend/tests/test_collectors.py'):
    key = P + name
    before = old_zip.read(OLD_PREFIX + key).decode().splitlines(True)
    after = (ROOT / key).read_text().splitlines(True)
    diff += ''.join(difflib.unified_diff(before, after, fromfile='previous_delivery/' + key, tofile='current/' + key))
with (OUT / 'test_portability_changes.diff').open('x') as f:
    f.write(diff)
old_integrity = json.loads((ROOT / (R + 'web_acquisition_qa/source-integrity.json')).read_text())['sha256']
for key, digest in old_integrity.items():
    assert sha((ROOT / key).read_bytes()) == digest, key
reviewed = {
 P + 'tools/deploy_web_acquisition.py': '47372510d74773830cbeb1bed5038235b2f84d02434e42f8dd637dbbe87170ed',
 P + 'tools/tests/test_deploy_web_acquisition.py': '29946097e97a67651cc3712fbcf5e8c42fd5f2a0c27cdf76f3b71f7a26a40b66',
 P + 'backend/mission_plan.py': 'cc0de5b55898183533dee07a7fde6a2d195eb782d12232aea6b1728530200561',
 P + 'backend/tests/test_mission_plan.py': '34c4cb2af04bb656fb4f9dc6946af4c1e55602fd5a18f97f76fdf0d6723110de',
}
for key, digest in reviewed.items():
    assert sha((ROOT / key).read_bytes()) == digest, key
    ast.parse((ROOT / key).read_text(), feature_version=(3, 10))
backend_log = (ROOT / (N + 'deployment/backend-final.log')).read_text()
assert 'Ran 152 tests' in backend_log and 'OK (skipped=1)' in backend_log
assert 'Ran 42 tests' in (ROOT / (N + 'deployment/unit-tests-reviewed.log')).read_text()
assert 'pass 65' in (ROOT / (N + 'deployment/frontend-full-regression.log')).read_text()
browser = json.loads((ROOT / (N + 'deployment/browser_recheck/browser-summary.json')).read_text())
assert browser['passed'] is True and len(browser['checks']) == 12 and not browser['page_errors'] and not browser['unexpected_origins']
assert browser['real_hardware_used'] is False and browser['temporary_fixture_cleaned'] is True
matlab = json.loads((ROOT / (N + 'matlab/execution-status.json')).read_text())
assert matlab['matlab_runtime']['executed'] == 0 and matlab['matlab_runtime']['not_executed'] == 32
for name in groups['materials/templates'].split():
    assert not list(csv.DictReader(io.StringIO((ROOT / (N + 'materials/templates/' + name)).read_text(encoding='utf-8-sig'))))
candidate = json.loads((ROOT / (N + 'deployment/prepared-bundle-status.json')).read_text())
with zipfile.ZipFile(ROOT / (N + 'deployment/audited_six_file_candidate.zip')) as archive:
    assert archive.testzip() is None
    for name, digest in candidate['sha256'].items():
        assert sha(archive.read(name)) == digest == sha((ROOT / (P + name)).read_bytes())
proof = {'recorded_at': datetime.now(timezone.utc).isoformat(), 'production_deployed': False,
         'real_board_deployment_or_rollback_tested': False, 'physical_nonzero_commands_sent': 0,
         'previous_20_source_digests_still_match': True, 'reviewed_sources': reviewed,
         'python_310_grammar_check': True, 'material_versions_verified': material_checks,
         'empty_templates': 14, 'backend': {'discovered': 152, 'passed': 151, 'skipped': 1, 'failed': 0},
         'deploy_tests': {'passed': 42, 'failed': 0}, 'frontend_tests': {'passed': 65, 'failed': 0},
         'browser_workflows': 12, 'matlab_executed': 0, 'matlab_unexecuted': 32,
         'test_portability_changes_only': ['backend/tests/test_cli.py', 'backend/tests/test_collectors.py'],
         'historical_failure_logs_preserved': True}
with (OUT / 'FINAL_SOURCE_CHECK.json').open('x') as f:
    json.dump(proof, f, ensure_ascii=False, indent=2)
paths = sorted(paths)
assert all((ROOT / p).is_file() for p in paths)
blobs = {p: (ROOT / p).read_bytes() for p in paths}
ids = {p: 'file-' + str(i) for i, p in enumerate(paths)}
CURRENT_DOCUMENT = ''

def link(label, target):
    url = urlsplit(target)
    if url.scheme in ('http', 'https'):
        return '<a href="%s" target="_blank" rel="noopener noreferrer">%s ↗</a>' % (html.escape(target, quote=True), html.escape(label))
    key = unquote(url.path)
    root_prefix = str(ROOT) + '/'
    if key.startswith(root_prefix):
        key = key[len(root_prefix):]
    possibilities = [key, posixpath.normpath(posixpath.join(posixpath.dirname(CURRENT_DOCUMENT), key))]
    if not key:
        possibilities.insert(0, CURRENT_DOCUMENT)
    for value in possibilities:
        if value in ids:
            return '<a href="#%s">%s</a>' % (ids[value], html.escape(label))
    if any(p.startswith(key.rstrip('/') + '/') for p in paths):
        return '<a href="#source-files">%s〔目录〕</a>' % html.escape(label)
    return '<a class="original" href="#not-included" title="%s">%s〔外部原件〕</a>' % (html.escape(target, quote=True), html.escape(label))

# Reuse only two inert renderer definitions and CSS, not the old builder's body.
old_tree = ast.parse((ROOT / (R + 'build_openable_delivery.py')).read_text())
functions = [n for n in old_tree.body if isinstance(n, ast.FunctionDef) and n.name in ('inline', 'markdown')]
assert len(functions) == 2
exec(compile(ast.Module(body=functions, type_ignores=[]), 'safe_renderer_definitions', 'exec'), globals())
css = next(ast.literal_eval(n.value) for n in old_tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'css' for t in n.targets))
primary = [
 ('本轮实际成果与真正剩余项', N + 'delivery/README.md'),
 ('网页：候选、预检和真实部署边界', N + 'deployment/README.md'),
 ('材料工作台与十四份事实空表', N + 'materials/README.md'),
 ('材料已有／待收状态', N + 'materials/MATERIALS_STATUS_ZH.md'),
 ('MATLAB环境搜索与0/32执行事实', N + 'matlab/REPORT.md'),
 ('自动任务：离线接口与未整合边界', P + 'MISSION_PREPARATION.md'),
 ('继续入口', R + 'CONTINUE_HERE.md'),
 ('课程要求和证据差距（历史审计）', R + 'course_audit/COURSE_EVIDENCE_GAP_CHECKLIST_ZH.md'),
]
parts = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>RBE201TC 晚间离线成果</title><style>' + css + '</style><body><main>',
 '<header id="top"><h1>RBE201TC 当前可做离线工作 · 新版总览</h1><p>中文报告、源码、CSV和日志已嵌入；无需本地服务器、Markdown软件或MATLAB才能阅读。原始二进制在配套ZIP内，本页仅列其元数据。</p><p class="notice"><strong>新CSV面板尚未真实上线；当前板子连接不可用。</strong><br>后端151通过＋1跳过，部署42/42、前端65/65、Chromium十二流程通过。MATLAB实际执行0/32。没有新正式房间观测或地图。</p><p>保持默认200、直接方向键/WASD、按住多久动多久、松开停、空格急停；未改三项控制／采样实现。</p><nav>']
parts += ['<a href="#%s">%s</a>' % (ids[p], html.escape(title)) for title, p in primary]
parts += ['<a href="#source-files">完整文件与验证资料</a></nav></header>']
for title, p in primary:
    CURRENT_DOCUMENT = p
    parts.append('<article id="%s"><h2>%s</h2>%s<p><a href="#top">回到目录</a></p></article>' % (ids[p], html.escape(title), markdown(blobs[p].decode('utf-8-sig'))))
parts.append('<section id="source-files"><h2>完整源码、空表与证据文件</h2><p>源码仅作文本展示，不执行。历史报告以各自时间为准；图片明确为软件夹具。CSV缺值保持空白，表内文本不执行公式。</p>')
primary_paths = {p for _, p in primary}
for p in paths:
    if p in primary_paths:
        continue
    CURRENT_DOCUMENT = p; data = blobs[p]
    if p.endswith('.png'):
        content = '<p class="notice">软件夹具截图，非板上页面／实物观测，不用于正式地图。</p><img alt="软件夹具界面截图" src="data:image/png;base64,%s">' % base64.b64encode(data).decode()
    else:
        try:
            text = data.decode('utf-8-sig')
        except UnicodeDecodeError:
            text = None
        if text is None:
            content = '<p>原始二进制已收录在ZIP内的同名路径；本页不执行或展开它。</p><p>字节：%s；SHA-256：<code>%s</code></p>' % (len(data), sha(data))
        elif p.endswith('.md'):
            content = markdown(text)
        elif p.endswith('.csv'):
            values = list(csv.reader(io.StringIO(text)))
            rendered = []
            for i, row in enumerate(values):
                tag = 'th' if i == 0 else 'td'
                cells = [inline(value) if re.fullmatch(r'\[[^\]]+\]\(<[^>]+>\)', value) else html.escape(value) for value in row]
                rendered.append('<tr>' + ''.join('<%s>%s</%s>' % (tag, value, tag) for value in cells) + '</tr>')
            content = '<p>数据行：%s。编辑请使用ZIP内原始CSV；这里不计算公式。</p><div class="table-scroll"><table>%s</table></div>' % (max(0, len(values) - 1), ''.join(rendered))
        else:
            content = '<pre><code>' + html.escape(text) + '</code></pre>'
    parts.append('<details id="%s"><summary>%s</summary>%s</details>' % (ids[p], html.escape(p), content))
parts.append('</section><section id="not-included"><h2>外部原件与交付边界</h2><p>未收录的教师原始PDF／外部平台、远端历史目录等引用保留追溯标签，不伪造替代。历史实物bench与20:25材料索引所引用的已提供文件按原字节保留。原始二进制请从ZIP打开，本总览不生成报告答案或视频内容。</p><p>ZIP解压后先打开根目录OPEN_FIRST.html。本包是技术增量和证据工作台，不是已经完成的最终课程提交包，也不是自动部署或机器人固件。</p><a href="#top">回到目录</a></section></main></body></html>')
page = '\n'.join(parts).encode()
assert '<script' not in page.decode().lower()
html_path = OUT / 'RBE201TC_OFFLINE_NEXT_20261104.html'
zip_path = OUT / 'RBE201TC_OFFLINE_NEXT_20261104.zip'
with html_path.open('xb') as f:
    f.write(page)
manifest = {p: sha(data) for p, data in blobs.items()}
prefix = 'RBE201TC_OFFLINE_NEXT_20261104/'
with zipfile.ZipFile(zip_path, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
    archive.writestr(prefix + 'OPEN_FIRST.html', page)
    archive.writestr(prefix + 'README_先看这里.txt', '先打开OPEN_FIRST.html；中文报告、源码、CSV与日志已嵌入。二进制原件按原路径在ZIP内。\n这是技术增量与证据包，不是课程最终提交包或自动部署器。\n生产未部署；设备连接不可用；MATLAB0/32实际执行；现场事项延期。\n')
    archive.writestr(prefix + 'FILES_SHA256.json', json.dumps(manifest, ensure_ascii=False, indent=2))
    for p, data in blobs.items():
        archive.writestr(prefix + p, data)
with zipfile.ZipFile(zip_path) as archive:
    assert archive.testzip() is None and archive.read(prefix + 'OPEN_FIRST.html') == page
    for p, digest in manifest.items():
        assert sha(archive.read(prefix + p)) == digest
print(json.dumps({'html': str(html_path), 'zip': str(zip_path), 'source_and_evidence_files': len(blobs), 'zip_members': len(blobs) + 3, 'html_bytes': len(page), 'zip_bytes': zip_path.stat().st_size, 'all_archive_hashes_verified': True}, ensure_ascii=False, indent=2))
