"""Package existing results without touching robot services or source files."""
from pathlib import Path
from urllib.parse import urlsplit, unquote
import base64
import hashlib
import html
import json
import re
import zipfile

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
PROJECT = 'work/device-console-v2/'
RESUME = 'outputs/RBE201TC_RESUME_20261104/'
# Explicit file inventory obtained using the workspace glob tool, not a broad archive.
project_files = '''frontend/tests/app.test.cjs
backend/collectors.py
backend/tests/test_parser.py
backend/tests/test_static.py
backend/tests/test_service.py
backend/tests/test_http.py
backend/tests/test_collectors.py
backend/tests/test_cli.py
backend/parser.py
backend/native/dht11_rdk_x5_gpio401.c
backend/service.py
frontend/tests/browser-check.cjs
backend/readings.py
backend/hardware_worker.py
backend/tests/test_readings.py
frontend/tests/sensors.test.cjs
frontend/styles.css
frontend/app.js
frontend/tests/control.test.cjs
frontend/control.js
backend/motor_control.py
backend/tests/test_motor_control.py
backend/README.md
config/stations.csv
tools/record_stations.py
backend/tests/test_experiment_log.py
tools/line_calibration.py
backend/line_calibration.py
backend/tests/test_line_calibration.py
CALIBRATION.md
README.md
config/hardware_observations.json
matlab/run_offline_tests.m
frontend/index.html
matlab/tests/TestMapRoom.m
matlab/map_room.m
matlab/README.md
frontend/acquisition.css
backend/experiment_log.py
backend/server.py
backend/acquisition.py
frontend/acquisition.js
backend/tests/test_acquisition.py
frontend/tests/acquisition.test.cjs
DATA_COLLECTION.md'''.splitlines()
scoped_files = {
    'web_acquisition_qa': '''rdk-backend-tests.log
frontend-tests.log
rdk-backend-tests-rerun.log
browser_fixture.py
browser_qa.cjs
software-fixture-acquisition.png
software-fixture-acquisition-mobile.png
browser-summary.json
rdk-source-boundary.json
source-integrity.json
source_changes.diff
QA_REPORT.md
deliverable-validation.json'''.splitlines(),
    'course_audit': '''check_material_sources.py
SOURCE_CHECKS.md
COURSE_EVIDENCE_GAP_CHECKLIST_ZH.md
RETURN_TO_SITE_EVIDENCE_CHECKLIST_ZH.md
AUDIT_SELF_CHECK.md'''.splitlines(),
    'matlab_qa': '''baseline/map_room.m.before.txt
baseline/README.md.before.txt
QA_REPORT.md
mh_lint.log
changes.diff
source_hashes.sha256
tests_inventory.txt
environment.log
self_check.log'''.splitlines(),
}
paths = [PROJECT + p for p in project_files]
paths += [RESUME + scope + '/' + name for scope, names in scoped_files.items() for name in names]
paths += [RESUME + 'CONTINUE_HERE.md', RESUME + 'RECOVERY_RECORD.md']
paths = sorted(set(paths))
assert all((ROOT / p).is_file() for p in paths)
blobs = {p: (ROOT / p).read_bytes() for p in paths}
integrity = json.loads(blobs[RESUME + 'web_acquisition_qa/source-integrity.json'])
for p, digest in integrity['sha256'].items():
    assert hashlib.sha256(blobs[p]).hexdigest() == digest, p

primary = [
    ('继续入口与交接', RESUME + 'CONTINUE_HERE.md'),
    ('网页CSV：功能、测试与部署边界', RESUME + 'web_acquisition_qa/QA_REPORT.md'),
    ('课程要求与证据差距', RESUME + 'course_audit/COURSE_EVIDENCE_GAP_CHECKLIST_ZH.md'),
    ('返场检查表（当前全部暂缓）', RESUME + 'course_audit/RETURN_TO_SITE_EVIDENCE_CHECKLIST_ZH.md'),
    ('MATLAB：改动、静态检查与未执行边界', RESUME + 'matlab_qa/QA_REPORT.md'),
    ('采集操作与未来部署说明', PROJECT + 'DATA_COLLECTION.md'),
    ('MATLAB使用说明', PROJECT + 'matlab/README.md'),
]
ids = {p: 'file-' + str(i) for i, p in enumerate(paths)}


def link(label, target):
    url = urlsplit(target)
    if url.scheme in ('http', 'https'):
        return '<a href="%s" target="_blank" rel="noopener noreferrer">%s ↗</a>' % (html.escape(target, quote=True), html.escape(label))
    key = unquote(url.path)
    if key in ids:
        return '<a href="#%s">%s</a>' % (ids[key], html.escape(label))
    return '<a class="original" href="#not-included" title="%s">%s〔原始资料〕</a>' % (html.escape(target, quote=True), html.escape(label))


def inline(text):
    pattern = r'\[([^\]]+)\]\(<([^>]+)>\)|`([^`]+)`|\*\*([^*]+)\*\*'
    result = []; end = 0
    for match in re.finditer(pattern, text):
        result.append(html.escape(text[end:match.start()]))
        if match[1] is not None:
            result.append(link(match[1], match[2]))
        elif match[3] is not None:
            result.append('<code>' + html.escape(match[3]) + '</code>')
        else:
            result.append('<strong>' + html.escape(match[4]) + '</strong>')
        end = match.end()
    result.append(html.escape(text[end:]))
    return ''.join(result)


def markdown(text):
    lines = text.splitlines(); blocks = []; i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1; continue
        if line.startswith('```'):
            code = []; i += 1
            while i < len(lines) and not lines[i].startswith('```'):
                code.append(lines[i]); i += 1
            blocks.append('<pre><code>' + html.escape('\n'.join(code)) + '</code></pre>')
            i += 1; continue
        if line.startswith('|'):
            table = []
            while i < len(lines) and lines[i].startswith('|'):
                table.append([cell.strip() for cell in lines[i].strip().strip('|').split('|')]); i += 1
            rendered = []
            for j, cells in enumerate(table):
                if all(re.fullmatch(r'[:\- ]+', c) for c in cells):
                    continue
                tag = 'th' if j == 0 else 'td'
                rendered.append('<tr>' + ''.join('<%s>%s</%s>' % (tag, inline(c), tag) for c in cells) + '</tr>')
            blocks.append('<div class="table-scroll"><table>' + ''.join(rendered) + '</table></div>')
            continue
        heading = re.match(r'^(#{1,6})\s+(.+)', line)
        if heading:
            n = min(len(heading[1]) + 1, 6)
            blocks.append('<h%d>%s</h%d>' % (n, inline(heading[2]), n))
        elif re.match(r'^\s*[-*]\s+', line):
            blocks.append('<p class="bullet">• ' + inline(re.sub(r'^\s*[-*]\s+', '', line)) + '</p>')
        else:
            blocks.append('<p>' + inline(line) + '</p>')
        i += 1
    return '\n'.join(blocks)

css = '''*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:#f3f5f7;color:#1e2934;font:15px/1.8 system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif}main{max-width:1180px;margin:auto;padding:30px 22px}header,article,section{background:white;border:1px solid #dce3e8;border-radius:14px;padding:25px;margin:0 0 22px}h1{font-size:27px;margin:0 0 12px}h2{font-size:23px}h3{font-size:19px}a{color:#16665c;text-decoration:underline;text-underline-offset:3px}nav{display:flex;gap:10px;flex-wrap:wrap}nav a{padding:8px 12px;background:#edf4ef;border-radius:6px}.notice{background:#fff4df;border-left:5px solid #c68e37;padding:14px}.muted,.original{color:#687783;font-size:13px}.table-scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:14px}th,td{border:1px solid #dce3e8;padding:9px;vertical-align:top;text-align:left}th{background:#f0f5f1}tr:nth-child(even){background:#fafbfd}pre{overflow:auto;background:#f3f5f7;padding:15px;border-radius:7px;line-height:1.6}code{font-family:ui-monospace,Menlo,monospace;font-size:.9em;overflow-wrap:anywhere}p{overflow-wrap:anywhere}.bullet{margin:6px 0 6px 14px}details{border-top:1px solid #e0e7eb;padding:12px 0}summary{cursor:pointer;font-weight:600;overflow-wrap:anywhere}img{max-width:100%;height:auto;border:1px solid #dce3e8;border-radius:8px}.badge{display:inline-block;padding:3px 9px;background:#edf4ef;border-radius:5px;margin-right:8px}@media(max-width:600px){main{padding:12px}header,article,section{padding:17px}h1{font-size:23px}th,td{min-width:130px}pre{font-size:12px}}'''
parts = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>RBE201TC 离线成果总览</title><style>' + css + '</style><body><main>',
         '<header id="top"><h1>RBE201TC 离线三项成果</h1><p>这是可直接用浏览器打开的单文件总览。报告、源码与日志均已嵌入，不需要Markdown软件、MATLAB或本地服务器才能阅读。</p><p class="notice"><strong>生产未部署，板上原控制服务未被本轮替换或重启。</strong><br>后端98/98、前端65/65和真实浏览器隔离流程通过；MATLAB只完成静态检查，32项测试实际执行0项。没有新增正式房间观测或地图。</p><p><span class="badge">原项目增量，不重做</span><span class="badge">不发实物运动指令</span><span class="badge">现场事项继续暂缓</span></p><nav>']
parts += ['<a href="#%s">%s</a>' % (ids[p], html.escape(title)) for title, p in primary]
parts += ['<a href="#source-files">源码与完整验证资料</a></nav><p class="muted">内容反映2026-11-04已记录的开发与验证结果，不代表打开本文件时重新探测了机器人在线状态。</p></header>']
for title, p in primary:
    parts.append('<article id="%s"><h2>%s</h2><p class="muted">%s</p>%s<p><a href="#top">回到目录</a></p></article>' % (ids[p], html.escape(title), html.escape(p), markdown(blobs[p].decode('utf-8'))))
parts.append('<section id="source-files"><h2>源码、原始Markdown与完整验证资料</h2><p>下面是本增量包收录的所有文件。源码只作为文本展示，不会在本页执行；图片明确为软件夹具。</p>')
primary_paths = {p for _, p in primary}
for p in paths:
    if p in primary_paths:
        continue
    data = blobs[p]
    if p.endswith('.png'):
        content = '<p class="notice">纯软件夹具，非板上页面、非实物测量，不用于课程房间地图。</p><img alt="软件夹具界面截图" src="data:image/png;base64,%s">' % base64.b64encode(data).decode()
    else:
        content = '<pre><code>' + html.escape(data.decode('utf-8')) + '</code></pre>'
    parts.append('<details id="%s"><summary>%s</summary>%s</details>' % (ids[p], html.escape(p), content))
parts += ['</section><section id="not-included"><h2>关于标记为“原始资料”的引用</h2><p>本包收录本轮离线报告、软件源码、配置、测试和验证日志，不重复打包最初的课程PDF、完整旧交接包及历史实物bench原件。它们的引用在原Markdown内保留以便追溯；需要查原件时，请使用你原有的交接资料。这里不会把缺少的原始资料替换为伪造内容。</p><p>ZIP解压后请先打开根目录的 <code>OPEN_FIRST.html</code>，而不是单独依赖旧Markdown的工作区相对链接。</p><a href="#top">回到目录</a></section></main></body></html>']
page = '\n'.join(parts).encode('utf-8')
html_path = OUT / 'RBE201TC_OFFLINE_RESULTS_20261104.html'
zip_path = OUT / 'RBE201TC_OFFLINE_RESULTS_20261104.zip'
with html_path.open('xb') as stream:
    stream.write(page)
manifest = {p: hashlib.sha256(data).hexdigest() for p, data in blobs.items()}
prefix = 'RBE201TC_OFFLINE_RESULTS_20261104/'
with zipfile.ZipFile(zip_path, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
    archive.writestr(prefix + 'OPEN_FIRST.html', page)
    archive.writestr(prefix + 'README_先看这里.txt', '先打开 OPEN_FIRST.html；无需额外软件即可阅读全部报告、源码与日志。\n这是离线增量源码与验证资料包，不是自动部署或机器人固件包。\n生产未部署；现场任务暂缓；MATLAB 32项测试实际执行0项。\n原始课程资料和历史实物bench不在此包重复收录。\n')
    archive.writestr(prefix + 'FILES_SHA256.json', json.dumps(manifest, ensure_ascii=False, indent=2))
    for p, data in blobs.items():
        archive.writestr(prefix + p, data)
with zipfile.ZipFile(zip_path) as archive:
    assert archive.testzip() is None
    assert archive.read(prefix + 'OPEN_FIRST.html') == page
    for p, digest in manifest.items():
        assert hashlib.sha256(archive.read(prefix + p)).hexdigest() == digest
assert '<script' not in page.decode().lower()
print(json.dumps({'html': str(html_path), 'zip': str(zip_path), 'source_and_evidence_files': len(blobs), 'zip_members': len(blobs) + 3, 'html_bytes': len(page), 'zip_bytes': zip_path.stat().st_size, 'all_archive_hashes_verified': True}, ensure_ascii=False, indent=2))
