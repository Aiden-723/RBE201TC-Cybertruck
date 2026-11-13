#!/usr/bin/env python3
"""Integrate real instrument samples; no battery-capacity or power guesses."""
import argparse
import csv
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path


def numeric(value,name,minimum=0):
    try:v=float(value)
    except (ValueError,TypeError):raise ValueError('缺少有效数值：'+name)
    if not math.isfinite(v) or v<minimum:raise ValueError('数值无效：'+name)
    return v


def analyze(path,max_gap):
    max_gap=numeric(max_gap,'max_gap')
    if max_gap<=0:raise ValueError('必须明确仪器可接受的最大采样间隔')
    raw=Path(path).read_bytes()
    import io
    reader=csv.DictReader(io.StringIO(raw.decode('utf-8-sig'),newline=''));rows=list(reader)
    if len(rows)<2:raise ValueError('至少需要两条真实功率/电压电流测量')
    samples=[];identities=set();last_timestamp=None;seen=set();methods=set()
    for i,row in enumerate(rows,2):
        if row.get('MissingOrFailureReason','').strip():raise ValueError('第%d行测量缺失或失败，不能跨缺口积分'%i)
        keys=('EnergyRunID','InstrumentID','ChannelAndMeasurementBoundary','RawInstrumentFileLink')
        if any(not row.get(k,'').strip() for k in keys):raise ValueError('第%d行缺少运行/仪器/测量边界/原始来源'%i)
        identities.add(tuple(row[k].strip() for k in keys[:3]))
        number=numeric(row.get('SampleNumber'),'SampleNumber')
        if not number.is_integer() or number<1 or number in seen:raise ValueError('采样编号无效或重复')
        seen.add(number)
        elapsed=numeric(row.get('Elapsed_s'),'Elapsed_s')
        try:timestamp=datetime.fromisoformat(row['TimestampWithTZ'])
        except (KeyError,ValueError):raise ValueError('采样时间无效')
        if timestamp.tzinfo is None:raise ValueError('采样时间必须包含时区')
        if last_timestamp and timestamp<=last_timestamp:raise ValueError('采样时间倒序或重复')
        last_timestamp=timestamp
        if row.get('DataClassMeasuredOrCalculated','').strip() not in ('measured','calculated'):
            raise ValueError('数据类别必须明确为measured或calculated')
        if row.get('Power_W','').strip():
            power=numeric(row['Power_W'],'Power_W');methods.add('input_power_w')
        else:
            power=numeric(row.get('Voltage_V'),'Voltage_V')*numeric(row.get('Current_A'),'Current_A');methods.add('voltage_times_current')
        if samples:
            gap=elapsed-samples[-1][0]
            if gap<=0 or gap>max_gap:raise ValueError('采样时间不递增或间隔超过允许范围；不能补造功率')
        samples.append((elapsed,power))
    if len(identities)!=1:raise ValueError('不同运行/仪器/测量边界不能混合积分')
    joules=sum((p0+p1)*.5*(t1-t0) for (t0,p0),(t1,p1) in zip(samples,samples[1:]))
    duration=samples[-1][0]-samples[0][0]
    run,instrument,boundary=next(iter(identities))
    return dict(EnergyRunID=run,InstrumentID=instrument,MeasurementBoundary=boundary,
                sample_count=len(samples),duration_s=duration,calculated_energy_Wh=joules/3600,
                mean_power_W=joules/duration,peak_sampled_power_W=max(p for _,p in samples),
                method='trapezoidal integration of nonnegative power over supplied elapsed time',
                power_sources=sorted(methods),input_sha256=hashlib.sha256(raw).hexdigest(),max_gap_s=max_gap,
                limitations='仅覆盖首末样本之间、指定仪器和供电边界；不是整车全寿命能耗、实际续航或碳排放。未捕获的峰值、仪器误差和采样稀疏误差需另行评价。没有电流/功率记录时不估算Wh。')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('input',type=Path);p.add_argument('--max-gap',type=float,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    try:
        result=analyze(a.input,a.max_gap)
        a.output.parent.mkdir(parents=True,exist_ok=True)
        with a.output.open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2,allow_nan=False)
    except (ValueError,OSError) as e:p.exit(2,str(e)+'\n')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
