"""Read-only audit of supplied archives, DOCX worksheet and CSV evidence.
Writes only inside this course_audit directory; no device, network or motion access.
"""
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import csv
import hashlib
import json
import tarfile
import zipfile
from collections import Counter
from docx import Document

ROOT = Path('/Users/your-user/Documents/Codex/2026-09-18/di').resolve()
OUT = ROOT / 'outputs/RBE201TC_RESUME_20261104/course_audit'
assert OUT.resolve().is_relative_to(ROOT)

def link(path):
    rel = path.relative_to(ROOT).as_posix()
    return f'[{path.name}](<{rel}>)'

def clean(value):
    return str(value).replace('|', '\\|').replace('\n', '<br>')

lines = ['# 来源结构与材料存在性复核记录', '',
         f"检查时间：{datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')}。", '',
         '本记录由只读检查生成；不联系设备，不执行课程程序，不改写原始材料。归档成员只做名称与结构检查，不据此验证功能。', '',
         '## 1. 原始课程附件及文本快照校验', '',
         '| 来源 | 字节数 | SHA-256 |', '|---|---:|---|']
base = ROOT / 'outputs/RBE201TC_HANDOFF_20261104'
source_files = [
    base / 'original_project_20261102/lecture/RBE201TC_Lecture_20260907_stu.pdf',
    base / 'original_project_20261102/lecture/RBE201TC_Lecture_20260922_1.pdf',
    base / 'original_project_20261102/RBE201TC_RDK_Robot_Manual_AY2627 (1).pdf',
    base / 'original_project_20261102/RBE201TC_Annual_Carbon_Emissions_Student_Worksheet.docx',
    base / 'reference/course_text/RBE201TC_Lecture_20260907_stu.txt',
    base / 'reference/course_text/RBE201TC_Lecture_20260922_1.txt',
    base / 'reference/course_text/RBE201TC_RDK_Robot_Manual_AY2627 (1).txt',
]
for p in source_files:
    data = p.read_bytes()
    lines.append(f'| {link(p)} | {len(data)} | `{hashlib.sha256(data).hexdigest()}` |')
lines += ['', '哈希仅标识本次读到的文件字节；不单独证明文本提取准确、课程平台无更新或实物功能合格。', '',
          '## 2. 已有归档成员名称检查', '',
          '纳入工作区 glob 发现的全部 ZIP/TGZ 归档（未解包、未执行）。只读中央目录/成员名称；未做所有成员内容审读或全归档CRC验收。', '']
cad = {'.sldprt','.sldasm','.slddrw','.stl','.step','.stp','.iges','.igs','.dxf','.dwg','.3mf','.gcode'}
video = {'.mp4','.mov','.mkv','.avi','.m4v','.webm'}
latex = {'.tex','.bib','.cls','.sty'}
archives = sorted([p for p in ROOT.rglob('*') if p.is_file() and p.suffix.lower() in {'.zip','.tgz'}])
for p in archives:
    if p.suffix.lower() == '.zip':
        with zipfile.ZipFile(p) as z:
            names = [i.filename for i in z.infolist() if not i.is_dir()]
    else:
        with tarfile.open(p, 'r:gz') as t:
            names = [i.name for i in t.getmembers() if i.isfile()]
    candidates = [n for n in names if Path(n).suffix.lower() in cad | video | latex]
    lines += [f'### {link(p)}', '', f'- 文件成员数：{len(names)}。',
              f'- CAD/制造、视频、LaTeX 常见扩展名匹配数：{len(candidates)}。']
    if candidates:
        lines += ['- 匹配成员（归档内名，不是已展开的独立工作区文件）：', '', '```text', *candidates, '```']
    if p.name == 'Sources.zip':
        lines += ['- 课程程序归档成员：', '', '```text', *names, '```']
    lines.append('')
lines += ['不匹配常见扩展名不排除资料藏于其他文件名/外部磁盘/课程平台；不推断组员没有完成。', '',
          '## 3. 实际CSV与站点表内容检查', '',
          '| 证据 | 数据行 | 状态计数 | 用途 | 坐标完整行 | 运行清单原始CSV校验 |',
          '|---|---:|---|---|---:|---|']
for p in sorted((ROOT / 'outputs/RBE201TC_RESUME_20261104/bench_diagnostics').glob('*/temperature_points_*.csv')):
    with p.open(encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    statuses = dict(Counter(r['Status'] for r in rows))
    purpose = sorted({r.get('RunPurpose','') for r in rows})
    coords = sum(all(r.get(k,'') for k in ['X_m','Y_m','SensorHeight_m']) for r in rows)
    manifest = p.with_name('run_manifest.json')
    m = json.loads(manifest.read_text())
    digest = hashlib.sha256(p.read_bytes()).hexdigest()
    stored_hash = m.get('raw_csv_sha256')
    stored_rows = m.get('row_count')
    if stored_hash is None or stored_rows is None:
        checksum_state = '原清单未提供hash/行数，不作历史校验已通过声明'
    else:
        ok = digest == stored_hash and len(rows) == stored_rows
        checksum_state = '与现存原清单一致' if ok else '不一致'
        assert ok, p
    lines.append(f'| {link(p)} | {len(rows)} | `{statuses}` | `{purpose}` | {coords} | {checksum_state}；{link(manifest)}；本次SHA-256 `{digest}` |')
    assert all(not r.get('Temperature_°C') and not r.get('Humidity_%RH') for r in rows if r['Status'] != 'success'), p
stations = ROOT / 'work/device-console-v2/config/stations.csv'
with stations.open(encoding='utf-8-sig', newline='') as f:
    station_rows = list(csv.DictReader(f))
lines += ['', f'- {link(stations)}：实际数据行 {len(station_rows)}，仅表头；本检查不添加坐标。',
          '- 三个运行均为 bench_diagnostic，无完整米制站点坐标；不是正式场地采集，不用于证明自主循迹、全房间覆盖、精度或传感器物理响应时间。',
          '- 失败行温湿度字段为空已检查；状态分母仅属于这组记录事件，不是全系统/整车成功率。', '',
          '## 4. DOCX课堂练习表内容提取', '',
          f'来源：{link(source_files[3])}。通过 python-docx 读取正文段落与表格；未填写、未改写、未计算题目。',
          '这是一份年度个人生活排放的课堂练习表，不可当作机器人LCA或学生已经完成的个人报告。结构读取不重新认证分页。', '']
doc = Document(source_files[3])
for p in doc.paragraphs:
    if p.text.strip():
        lines.append(clean(p.text))
        lines.append('')
for i, table in enumerate(doc.tables, 1):
    lines += [f'### 原表格 {i}', '']
    width = max(len(row.cells) for row in table.rows)
    lines += ['| ' + ' | '.join(f'列{k+1}' for k in range(width)) + ' |',
              '|' + '|'.join('---' for _ in range(width)) + '|']
    for row in table.rows:
        vals = [clean(c.text) for c in row.cells]
        lines.append('| ' + ' | '.join(vals + [''] * (width - len(vals))) + ' |')
    lines.append('')
lines += ['## 5. 检查范围限制', '',
          '- 本次结构检查仅覆盖工作区可读文件和上述已发现归档；未访问学生/组员其他设备、云盘、学校平台或任意远端资料。',
          '- 未重新运行RDK、网页、MATLAB、SolidWorks或实体实验；本轮其他代理新增源码的运行与测试结论应由对应代理报告。',
          '- 本记录是材料管理证据，不是课程报告、个人LCA或考核视频/录制PPT内容。', '']
(OUT / 'SOURCE_CHECKS.md').write_text('\n'.join(lines), encoding='utf-8')
print(f'Source checks written; archives={len(archives)}, station_rows={len(station_rows)}, worksheet_tables={len(doc.tables)}')
