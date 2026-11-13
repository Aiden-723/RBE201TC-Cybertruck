#!/usr/bin/env python3
"""Read-only course CSV audit. Writes a separate HTML/JSON report; never cleans originals."""
import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import html
import io
import json
import math
from pathlib import Path
import re

CORE = ('RunID', 'Detection point', 'Trigger time', 'Result time', 'Humidity_%RH', 'Temperature_°C', 'Status', 'Explanation')
STATES = {'pending', 'success', 'sensor_failure', 'request_failure', 'timeout', 'cancelled'}


def finite(text, low=-math.inf, high=math.inf):
    try:
        value = float(text)
        return value if math.isfinite(value) and low <= value <= high else None
    except (ValueError, TypeError, OverflowError):
        return None


def event_number(text):
    try:
        value = Decimal(text)
        if value.is_finite() and 0 < value < 2**53 and value == value.to_integral_value():
            return int(value)
    except (InvalidOperation, ValueError, TypeError):
        pass
    return None


def moment(text):
    # Match the existing MATLAB input contract, including milliseconds and timezone.
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}(?:Z|[+-]\d{2}:\d{2})', text or ''):
        return None
    if text[-1] != 'Z' and (int(text[-5:-3]) > 23 or int(text[-2:]) > 59):
        return None
    try:
        return datetime.fromisoformat(text[:-1] + '+00:00' if text.endswith('Z') else text)
    except ValueError:
        return None


def read_csv(path):
    data = path.read_bytes()
    reader = csv.DictReader(io.StringIO(data.decode('utf-8-sig'), newline=''))
    fields = reader.fieldnames or []
    return data, fields, list(reader)


def load_plan(path):
    _, fields, rows = read_csv(path)
    required = {'StationID', 'X_m', 'Y_m', 'SensorHeight_m'}
    if not required.issubset(fields) or not rows:
        raise ValueError('站点表为空或缺少 StationID / X_m / Y_m / SensorHeight_m')
    plan = {}
    for row in rows:
        key = (row.get('StationID') or '').strip()
        xyz = [finite(row.get(k), low=0 if k=='SensorHeight_m' else -math.inf) for k in ('X_m','Y_m','SensorHeight_m')]
        if not key or key in plan or None in xyz:
            raise ValueError('站点表存在重复编号或无效坐标/高度')
        plan[key] = xyz
    return plan


def audit(inputs, station_plan=None):
    files = set()
    for item in inputs:
        p = Path(item).expanduser().resolve()
        if not p.exists():
            raise ValueError('找不到输入：'+str(p))
        files.update(p.rglob('temperature_points_*.csv') if p.is_dir() else [p])
    files = sorted(files)
    issues, rows, file_info, manifests = [], [], [], {}
    def issue(code, message, severity='warning', entry=None, path='', run=''):
        issues.append(dict(code=code, message=message, severity=severity,
                           file=entry['file'] if entry else str(path),
                           row=entry['line'] if entry else None,
                           run_id=entry['run'] if entry else run))
        if entry and severity == 'error':
            entry['eligible'] = False
    if not files:
        issue('no_files', '没有找到原始 temperature_points_*.csv 文件。', 'error')
    for path in files:
        try:
            raw, fields, source_rows = read_csv(path)
        except (OSError, UnicodeError, csv.Error) as exc:
            issue('unreadable_csv', str(exc), 'error', path=path)
            continue
        digest = hashlib.sha256(raw).hexdigest()
        info = dict(file=str(path), sha256=digest, row_count=len(source_rows), manifest_integrity='unverified')
        file_info.append(info)
        if len(fields) != len(set(fields)) or not set(CORE).issubset(fields):
            issue('invalid_columns', '缺少课程必需列或存在重名列。', 'error', path=path)
            continue
        manifest = None
        file_bad = False
        mp = path.parent/'run_manifest.json'
        if mp.exists():
            try:
                manifest = json.loads(mp.read_text(encoding='utf-8'))
                if not isinstance(manifest, dict):
                    raise ValueError('清单不是对象')
            except (OSError, ValueError) as exc:
                issue('bad_manifest', '清单无法读取：'+str(exc), 'error', path=path)
                file_bad = True
                info['manifest_integrity'] = 'failed'
        else:
            issue('missing_manifest', '缺少运行清单，无法核对原始文件完整性。', path=path)
        if manifest:
            for key, actual in [('raw_csv_sha256', digest), ('row_count', len(source_rows)),
                                ('status_counts', dict(Counter((r.get('Status') or '').strip() for r in source_rows)))]:
                if key in manifest and manifest[key] != actual:
                    issue('manifest_mismatch', '清单与CSV不一致：'+key, 'error', path=path)
                    file_bad = True
            if not manifest.get('closed_at'):
                issue('unclosed_run', '运行未正常结束；保留原样，不能当作完整采集。', path=path, run=manifest.get('RunID',''))
            elif not all(k in manifest for k in ('raw_csv_sha256','row_count','status_counts')):
                issue('legacy_manifest', '旧清单没有完整的哈希/行数/状态统计，无法完全校验。', path=path)
            elif not file_bad:
                info['manifest_integrity'] = 'verified'
            if file_bad:
                info['manifest_integrity'] = 'failed'
            manifests[str(path)] = manifest
        if not source_rows:
            issue('empty_run', 'CSV没有任何事件。', path=path)
        for line, raw_row in enumerate(source_rows, 2):
            row = {k:(v or '').strip() for k,v in raw_row.items() if k is not None}
            run = row.get('RunID','')
            purpose = row.get('RunPurpose','')
            entry = dict(file=str(path), line=line, data=row, run=run, point=event_number(row.get('Detection point')),
                         eligible=purpose=='room_stationary' and row.get('Status')=='success' and not file_bad)
            rows.append(entry)
            if None in raw_row or any(v is None for v in raw_row.values()):
                issue('malformed_row', 'CSV行的列数不完整或多出字段。', 'error', entry)
            if not run or entry['point'] is None:
                issue('invalid_identity', 'RunID为空或事件编号不是有效正整数。', 'error', entry)
            if manifest:
                for column, key in [('RunID','RunID'),('RunPurpose','purpose'),('GroupID','GroupID'),('RoomID','RoomID')]:
                    if key in manifest and row.get(column,'') != str(manifest[key]):
                        issue('manifest_identity', '记录身份与清单不一致：'+column, 'error', entry)
            status = row.get('Status','')
            if status not in STATES:
                issue('unknown_status', '未知记录状态：'+status, 'error', entry)
            elif status == 'pending':
                issue('pending', '记录尚无结果；可能是采集中或曾中断。', entry=entry)
                if manifest and manifest.get('closed_at'):
                    issue('closed_pending', '已关闭运行仍含pending。', 'error', entry)
            elif status != 'success':
                issue('unsuccessful', '本次无有效读数：'+status+'；'+row.get('Explanation',''), entry=entry)
            t, rh = row.get('Temperature_°C',''), row.get('Humidity_%RH','')
            if status != 'success' and (t or rh):
                issue('failure_has_values', '非成功记录带有温湿度数值，不能用于分析。', 'error', entry)
            if status == 'success' and (finite(t,-20,80) is None or finite(rh,0,100) is None):
                issue('invalid_measurement', '成功记录缺少有效温湿度数值。', 'error', entry)
            if status not in ('pending','success') and not row.get('Explanation'):
                issue('missing_explanation', '失败记录没有原因说明。', entry=entry)
            trigger, result = moment(row.get('Trigger time')), moment(row.get('Result time'))
            if trigger is None or (status != 'pending' and result is None):
                issue('invalid_time', '时间无效或缺少毫秒/时区。', 'error', entry)
            if trigger and result:
                delay = (result-trigger).total_seconds()
                stored = finite(row.get('Delay_seconds'),0)
                if delay < 0 or stored is None or abs(stored-delay) > .020000001:
                    issue('invalid_delay', '结果早于触发、等待时间缺失或与时间戳不一致。', 'error', entry)
            if purpose == 'room_stationary':
                if any(not row.get(k) for k in ('GroupID','RoomID','StationID')):
                    issue('missing_location_identity', '正式记录缺少组号、房间或站点编号。', 'error', entry)
                xyz = [finite(row.get(k),low=0 if k=='SensorHeight_m' else -math.inf) for k in ('X_m','Y_m','SensorHeight_m')]
                if None in xyz:
                    issue('missing_coordinates', '正式记录缺少有效X/Y坐标或传感器高度。', 'error', entry)
            elif purpose != 'bench_diagnostic':
                issue('unknown_purpose', '用途缺失或未知，不能认定为正式房间数据。', 'error', entry)
    events, stations, samples = defaultdict(list), defaultdict(list), defaultdict(list)
    for entry in rows:
        row = entry['data']
        if entry['run'] and entry['point'] is not None:
            events[(entry['run'],entry['point'])].append(entry)
        if row.get('RunPurpose')=='room_stationary':
            stations[(row.get('GroupID'),row.get('RoomID'),row.get('StationID'))].append(entry)
        if row.get('Status')=='success' and row.get('Sampled_at') and row.get('Sampler_monotonic'):
            samples[(entry['run'],row['Sampled_at'],row['Sampler_monotonic'])].append(entry)
    for entries in events.values():
        if len(entries)>1:
            for entry in entries:
                issue('duplicate_event', '同一RunID和事件编号重复（包括数值等价编号）。', 'error', entry)
    for entries in samples.values():
        if len(entries)>1 and len({e['point'] for e in entries})>1:
            for entry in entries:
                issue('reused_sample', '多个事件引用同一个采样结果；不能当作独立读数。', 'error', entry)
    for entries in stations.values():
        valid = [e for e in entries if all(finite(e['data'].get(k)) is not None for k in ('X_m','Y_m','SensorHeight_m'))]
        conflict = valid and any(max(float(e['data'][k]) for e in valid)-min(float(e['data'][k]) for e in valid) > .001000000001 for k in ('X_m','Y_m','SensorHeight_m'))
        if conflict:
            for entry in entries:
                issue('station_conflict', '同一站点坐标或高度相差超过1毫米，请核对原始记录。', 'error', entry)
    runs = []
    for run in sorted({r['run'] for r in rows}):
        entries = [e for e in rows if e['run']==run]
        purposes = sorted({e['data'].get('RunPurpose','') for e in entries})
        ids = sorted({e['point'] for e in entries if e['point'] is not None})
        missing_ranges = [(a+1,b-1) for a,b in zip([0]+ids,ids) if b>a+1]
        if missing_ranges:
            issue('event_gaps', '事件编号缺口：'+str(missing_ranges), run=run)
        identities = {(e['data'].get('RunPurpose'),e['data'].get('GroupID'),e['data'].get('RoomID')) for e in entries}
        if len(identities)>1:
            for entry in entries:
                issue('mixed_run_identity', '同一运行混合了不同用途、组号或房间。', 'error', entry)
        formal = 'room_stationary' in purposes
        plan = station_plan if formal else None
        if formal and plan is None:
            for entry in entries:
                m = manifests.get(entry['file'],{})
                specs = m.get('metadata',{}).get('stations',[]) if isinstance(m.get('metadata',{}),dict) else []
                if specs:
                    try:
                        if not isinstance(specs, list) or not all(isinstance(s, dict) for s in specs):
                            raise ValueError('invalid_station_plan')
                        candidate = {s['station_id']:[s['x_m'],s['y_m'],s['height_m']] for s in specs}
                        if len(candidate)!=len(specs) or any(not k or any(type(v) not in (int,float) or not math.isfinite(v) for v in xyz) or xyz[2]<0 for k,xyz in candidate.items()):
                            raise ValueError()
                        if plan is not None and plan != candidate:
                            raise ValueError()
                        plan = candidate
                    except (KeyError, TypeError, ValueError):
                        issue('invalid_station_plan', '清单中的站点计划无效或互相冲突。', 'error', run=run)
                        for entry in entries:
                            entry['eligible'] = False
                        plan = None
                        break
        visited = {e['data'].get('StationID','') for e in entries}
        if formal and plan is not None:
            for entry in entries:
                sid = entry['data'].get('StationID','')
                if sid not in plan:
                    issue('unexpected_station', '记录的站点不在指定计划内。', 'error', entry)
                else:
                    xyz = [finite(entry['data'].get(k)) for k in ('X_m','Y_m','SensorHeight_m')]
                    if None not in xyz and any(abs(a-b)>.001000000001 for a,b in zip(xyz,plan[sid])):
                        issue('plan_coordinate_mismatch', '记录坐标/高度与站点计划不一致。', 'error', entry)
        good = {e['data'].get('StationID','') for e in entries if e['eligible']}
        missing = sorted(set(plan)-visited) if plan is not None else None
        retry = sorted(set(plan)-good) if plan is not None else None
        if formal and plan is None:
            issue('coverage_unknown', '没有有效的预定站点表，无法判断漏测了哪些站点。', run=run)
        elif retry:
            issue('stations_need_measurement', '尚无合格读数的站点：'+', '.join(retry), run=run)
        runs.append(dict(run_id=run, purposes=purposes, rows=len(entries), statuses=dict(Counter(e['data'].get('Status','') for e in entries)),
                         candidate_room_rows=sum(e['eligible'] for e in entries), missing_stations=missing, stations_without_valid_reading=retry))
    summary = dict(files=len(file_info), rows=len(rows), statuses=dict(Counter(e['data'].get('Status','') for e in rows)),
                   errors=sum(i['severity']=='error' for i in issues), warnings=sum(i['severity']=='warning' for i in issues),
                   candidate_room_rows=sum(e['eligible'] for e in rows), bench_rows=sum(e['data'].get('RunPurpose')=='bench_diagnostic' for e in rows))
    return dict(generated_at=datetime.now().astimezone().isoformat(), summary=summary, files=file_info, runs=runs, issues=issues,
                limitations='只读预检查，不修改/补造原始值；候选记录不是地图验收结论。无站点计划时不判断覆盖完整。桌面联调不计入房间地图。传感器准确度和实际位置需实物验证。')


def render_report(report):
    esc = lambda x: html.escape(str(x), quote=True)
    s = report['summary']
    cards = ''.join('<div class="card"><strong>'+esc(v)+'</strong><span>'+label+'</span></div>' for label,v in [('原始记录',s['rows']),('需修查的错误',s['errors']),('失败 / 中断等提示',s['warnings']),('房间候选记录',s['candidate_room_rows'])])
    purpose_labels={'bench_diagnostic':'桌面联调','room_stationary':'房间定点测量'}
    status_labels={'success':'成功','sensor_failure':'传感器失败','request_failure':'请求失败','timeout':'超时','cancelled':'取消','pending':'未完成'}
    run_rows = ''.join('<tr>'+''.join('<td>'+esc(v)+'</td>' for v in [r['run_id'],', '.join(purpose_labels.get(p,p) for p in r['purposes']),r['rows'],' / '.join(status_labels.get(k,k)+' '+str(v) for k,v in r['statuses'].items()),'未提供计划 / 不适用' if r['stations_without_valid_reading'] is None else ', '.join(r['stations_without_valid_reading']) or '无'])+'</tr>' for r in report['runs'])
    problem_rows = ''.join('<tr>'+''.join('<td>'+esc(v)+'</td>' for v in ['错误' if i['severity']=='error' else '提示',i['message'],Path(i['file']).name if i['file'] else i['run_id'],i['row'] or '—'])+'</tr>' for i in report['issues'])
    file_rows = ''.join('<tr><td>'+esc(Path(f['file']).name)+'</td><td>'+esc({'verified':'核对通过','failed':'不一致','unverified':'未完整验证'}[f['manifest_integrity']])+'</td><td class="hash">'+f['sha256']+'</td></tr>' for f in report['files'])
    return '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>小车数据检查</title><style>
body{font:16px/1.65 system-ui,sans-serif;background:#f2f5f9;color:#172b40;margin:0;padding:32px}main{max-width:1150px;margin:auto}h1{margin:0}h2{margin-top:36px}p{color:#4c6177}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px}.card{background:white;padding:24px;border-radius:14px}.card strong{font-size:34px;display:block}.card span{color:#4c6177}.scroll{overflow:auto}table{border-collapse:collapse;background:white;width:100%;font-size:14px}th,td{padding:12px;text-align:left;border-bottom:1px solid #dce5ed;vertical-align:top}th{background:#e8eef5}.hash{font:11px monospace;word-break:break-all}.note{border-left:4px solid #277e99;padding:12px 18px;background:#e4f3f6}</style><main><h1>小车数据检查</h1><p>'''+esc(report['generated_at'])+''' · 原始数据保持不变</p><div class="cards">'''+cards+'''</div><p class="note">'''+esc(report['limitations'])+'''</p><h2>每次运行与待补测站点</h2><div class="scroll"><table><tr><th>运行</th><th>用途</th><th>记录数</th><th>结果</th><th>需要补测</th></tr>'''+run_rows+'''</table></div><h2>需要查看的记录</h2><div class="scroll"><table><tr><th>级别</th><th>说明</th><th>文件 / 运行</th><th>CSV行</th></tr>'''+(problem_rows or '<tr><td colspan="4">没有发现本工具覆盖的记录问题。</td></tr>')+'''</table></div><h2>原始文件完整性</h2><div class="scroll"><table><tr><th>文件</th><th>与清单核对</th><th>SHA-256</th></tr>'''+file_rows+'''</table></div></main></html>'''


def save_report(report, output):
    output = Path(output).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    (output/'report.html').write_text(render_report(report),encoding='utf-8')
    return output/'report.html'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('inputs',nargs='+',type=Path)
    parser.add_argument('--stations',type=Path,help='实测站点计划；不提供时尝试使用每次运行清单')
    parser.add_argument('--output',type=Path,required=True,help='新建报告目录，拒绝覆盖既有目录')
    args = parser.parse_args()
    try:
        report = audit(args.inputs,load_plan(args.stations) if args.stations else None)
        page = save_report(report,args.output)
    except (ValueError,OSError) as exc:
        parser.exit(2,str(exc)+'\n')
    print(json.dumps(report['summary'],ensure_ascii=False))
    print(page)
    return 1 if report['summary']['errors'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
