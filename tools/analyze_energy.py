#!/usr/bin/env python3
"""Integrate declared measured/calculated power samples, without instrument I/O.

Results are calculations over the supplied interval, not verified measurements,
battery capacity, runtime, full-run completeness, or carbon-emission estimates.
"""
import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import tempfile


REQUIRED_FIELDS = frozenset((
    'EnergyRunID', 'SampleNumber', 'TimestampWithTZ', 'Elapsed_s', 'Voltage_V',
    'Current_A', 'Power_W', 'ChannelAndMeasurementBoundary',
    'DataClassMeasuredOrCalculated', 'InstrumentID', 'RawInstrumentFileLink',
    'MissingOrFailureReason'))
OPTIONAL_FIELDS = frozenset(('AccumulatedEnergy_Wh', 'Notes'))
STAMP = re.compile(r'\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}:\d{2}'
                   r'(?:\.\d{1,6})?(?:[Zz]|[+-]\d{2}:\d{2})\Z')


def decimal_number(value, name, minimum=0):
    """Exact supplied decimal for time checks; reject bool/nonfinite/float loss."""
    if isinstance(value, bool):
        raise ValueError('数值不能是布尔值：' + name)
    text = str(value).strip()
    if not text or len(text) > 128:
        raise ValueError('缺少有效数值或数值文本过长：' + name)
    try:
        number = Decimal(text)
        converted = float(number)
    except (InvalidOperation, ValueError, TypeError, OverflowError) as exc:
        raise ValueError('缺少有效数值：' + name) from exc
    if (not number.is_finite() or not math.isfinite(converted) or number < minimum
            or number != 0 and converted == 0):
        raise ValueError('数值无效、溢出或下溢：' + name)
    return number


def numeric(value, name, minimum=0):
    return float(decimal_number(value, name, minimum))


def finite_result(value, name, *, nonzero=False):
    if not math.isfinite(value) or nonzero and value == 0:
        raise ValueError('计算溢出或下溢：' + name)
    return value


def timestamp(value):
    # Reject silent truncation beyond datetime's microsecond resolution.
    if not STAMP.fullmatch(value):
        raise ValueError('采样时间须含日期、秒、最多6位小数和明确时区')
    if value[-1] not in ('Z', 'z') and (int(value[-5:-3]) > 23 or int(value[-2:]) > 59):
        raise ValueError('采样时区偏移的小时或分钟无效')
    normalized = value[:-1] + '+00:00' if value[-1] in ('Z', 'z') else value
    try:
        result = datetime.fromisoformat(normalized).astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise ValueError('采样时间或时区无效') from exc
    return result


def seconds(delta):
    return Decimal(delta.days * 86400 + delta.seconds) + Decimal(delta.microseconds) / 1000000


def check_quotes(text):
    # csv.reader(strict=True) still accepts quotes embedded in unquoted fields.
    # Enforce CSV quote placement while retaining quoted commas/newlines/"".
    state = 'start'
    for character in text:
        if state == 'quoted':
            if character == '"':
                state = 'closed'
        elif state == 'closed':
            if character == '"':
                state = 'quoted'
            elif character in ',\r\n':
                state = 'start'
            else:
                raise ValueError('CSV闭引号后含非分隔符')
        elif character == '"':
            if state != 'start':
                raise ValueError('CSV未引用字段中含未转义引号')
            state = 'quoted'
        elif character in ',\r\n':
            state = 'start'
        else:
            state = 'unquoted'
    if state == 'quoted':
        raise ValueError('CSV引号未闭合')


def parse_rows(raw):
    try:
        text = raw.decode('utf-8-sig')
        check_quotes(text)
        reader = csv.reader(io.StringIO(text, newline=''), strict=True)
        header = next(reader, None)
        if (not header or len(header) != len(set(header)) or
                not REQUIRED_FIELDS <= set(header) or
                not set(header) <= REQUIRED_FIELDS | OPTIONAL_FIELDS):
            raise ValueError('CSV表头重复、缺少必需字段或含未知字段')
        rows = []
        for values in reader:
            if len(values) != len(header):
                raise ValueError('CSV第%d物理行字段数不符或含空白记录' % reader.line_num)
            row = {name: value.strip() for name, value in zip(header, values)}
            if any('\x00' in value for value in row.values()):
                raise ValueError('CSV不能含NUL字符')
            rows.append(row)
    except (csv.Error, UnicodeError) as exc:
        raise ValueError('CSV编码或引号结构无效：' + str(exc)) from exc
    if len(rows) < 2:
        raise ValueError('至少需要两条明确类别的功率/电压电流样本')
    return rows


def analyze(path, max_gap, *, time_tolerance=0):
    """Read only. max_gap is elapsed-time gap; tolerance checks both clocks.

    Sample numbers must strictly increase as exact positive integers; skipped
    numbers are permitted (e.g. downsampling), reported, not treated as proof of
    missing/complete measurements. Explicit failure rows and over-gap intervals
    are rejected, not interpolated or silently excluded.
    """
    gap_limit = decimal_number(max_gap, 'max_gap')
    tolerance = decimal_number(time_tolerance, 'time_tolerance')
    if gap_limit <= 0:
        raise ValueError('必须明确可接受的最大采样间隔')
    raw = Path(path).read_bytes()
    rows = parse_rows(raw)
    samples, times, numbers = [], [], []
    identities, links, methods, classes = set(), set(), set(), Counter()
    gap_intervals = 0
    # 128-character finite decimals plus binary64 exponents fit without rounding
    # time subtraction at this precision. No float epsilon is a clock tolerance.
    with localcontext() as context:
        context.prec = 768
        for index, row in enumerate(rows, 1):
            if row['MissingOrFailureReason']:
                raise ValueError('第%d条样本缺失或失败，不能跨缺口积分' % index)
            keys = ('EnergyRunID', 'InstrumentID', 'ChannelAndMeasurementBoundary', 'RawInstrumentFileLink')
            if any(not row[key] for key in keys):
                raise ValueError('第%d条缺少运行/仪器/测量边界/原始来源' % index)
            identities.add(tuple(row[key] for key in keys[:3]))
            links.add(row['RawInstrumentFileLink'])  # A declaration, never followed/read.
            text_number = row['SampleNumber']
            if not re.fullmatch(r'[1-9][0-9]{0,99}', text_number):
                raise ValueError('采样编号须为不超过100位的精确正整数')
            number = int(text_number)
            if numbers and number <= numbers[-1]:
                raise ValueError('采样编号须唯一且严格递增；不排序或合并输入')
            if numbers and number != numbers[-1] + 1:
                gap_intervals += 1
            elapsed = decimal_number(row['Elapsed_s'], 'Elapsed_s')
            stamp = timestamp(row['TimestampWithTZ'])
            category = row['DataClassMeasuredOrCalculated']
            if category not in ('measured', 'calculated'):
                raise ValueError('数据类别必须明确为measured或calculated；不自动认可估计输入')
            classes[category] += 1
            if row['Power_W']:
                power = numeric(row['Power_W'], 'Power_W')
                methods.add('input_power_w')
            else:
                voltage = numeric(row['Voltage_V'], 'Voltage_V')
                current = numeric(row['Current_A'], 'Current_A')
                power = finite_result(voltage * current, 'Voltage_V × Current_A',
                                      nonzero=voltage != 0 and current != 0)
                methods.add('voltage_times_current')
            if samples:
                gap = elapsed - samples[-1][0]
                if gap <= 0 or gap > gap_limit:
                    raise ValueError('Elapsed_s不递增或间隔超过max-gap；不能补造功率')
                if stamp <= times[-1]:
                    raise ValueError('采样绝对时间倒序或重复')
                if (abs(seconds(stamp - times[-1]) - gap) > tolerance or
                        abs(seconds(stamp - times[0]) - (elapsed - samples[0][0])) > tolerance):
                    raise ValueError('TimestampWithTZ与Elapsed_s相邻或累计时差超过time-tolerance')
            samples.append((elapsed, power)); times.append(stamp); numbers.append(number)
        if len(identities) != 1:
            raise ValueError('不同运行/仪器/测量边界不能混合积分')
        areas = []
        for (t0, p0), (t1, p1) in zip(samples, samples[1:]):
            # Nonnegative endpoints: avoid (p0+p1) overflowing spuriously.
            mean = finite_result(min(p0, p1) + (max(p0, p1) - min(p0, p1)) * .5,
                                 '区间平均功率', nonzero=p0 != 0 or p1 != 0)
            gap = finite_result(float(t1 - t0), '区间时长', nonzero=True)
            areas.append(finite_result(mean * gap, '区间能量', nonzero=mean != 0))
        try:
            joules = finite_result(math.fsum(areas), '总能量')
        except OverflowError as exc:
            raise ValueError('积分总能量溢出') from exc
        duration = finite_result(float(samples[-1][0] - samples[0][0]), '总时长', nonzero=True)
    energy = finite_result(joules / 3600, 'Wh', nonzero=joules != 0)
    mean_power = finite_result(joules / duration, '平均功率', nonzero=joules != 0)
    run, instrument, boundary = next(iter(identities))
    return dict(
        EnergyRunID=run, InstrumentID=instrument, MeasurementBoundary=boundary,
        sample_count=len(samples), duration_s=duration, calculated_energy_Wh=energy,
        mean_power_W=mean_power, peak_sampled_power_W=max(power for _, power in samples),
        method='trapezoidal integration of nonnegative power over supplied elapsed time',
        power_sources=sorted(methods), input_sha256=hashlib.sha256(raw).hexdigest(),
        max_gap_s=float(gap_limit), time_tolerance_s=float(tolerance),
        input_data_class=next(iter(classes)) if len(classes) == 1 else 'mixed',
        input_data_class_counts=dict(sorted(classes.items())), result_data_class='calculated',
        input_measurement_verified=False, raw_instrument_file_links=sorted(links),
        coverage=dict(first_sample_number=numbers[0], last_sample_number=numbers[-1],
                      sample_number_gap_intervals=gap_intervals, full_run_verified=False,
                      start_elapsed_s=float(samples[0][0]), end_elapsed_s=float(samples[-1][0]),
                      start_timestamp_utc=times[0].isoformat(), end_timestamp_utc=times[-1].isoformat()),
        limitations='仅计算首末样本之间、声明仪器和供电边界的能耗；measured/calculated是输入声明，未核实仪器或来源真实性。'
                    '编号缺口允许降采样但不证明完整；不补造缺失功率，失败行或过大时间缺口拒绝。'
                    'time-tolerance仅为使用者明确的时轴一致性阈值，不是仪器精度。'
                    '未捕获峰值、仪器误差和采样稀疏误差需另评。不是全寿命能耗、容量、续航或碳排放；无功率/电流记录不猜Wh。')


def save_new_json(path, result):
    """Serialize first; publish complete fsynced file exclusively, never replace.

    Hard-link publication is atomic/no-clobber on supporting local filesystems.
    Unsupported filesystems fail closed. This is not a directory-fsync/power-loss
    durability guarantee. No original input or existing result is deleted.
    """
    body = (json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')
    destination = Path(path).absolute()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        descriptor, temporary = tempfile.mkstemp(prefix='.energy-', dir=destination.parent)
        with os.fdopen(descriptor, 'wb') as output:
            output.write(body)
            output.flush()
            os.fsync(output.fileno())
        os.link(temporary, destination)  # Fails if target exists, including symlinks.
    finally:
        if temporary is not None:
            candidate = Path(temporary)
            if candidate.parent == destination.parent and candidate.name.startswith('.energy-'):
                candidate.unlink()
    return body


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('--max-gap', type=str, required=True, help='显式最大Elapsed_s采样间隔，秒')
    parser.add_argument('--time-tolerance', type=str, default='0',
                        help='时轴相邻/累计差允许秒数，默认0；不是仪器精度')
    parser.add_argument('--output', type=Path, required=True, help='只创建新JSON，不覆盖')
    args = parser.parse_args(argv)
    try:
        result = analyze(args.input, args.max_gap, time_tolerance=args.time_tolerance)
        body = save_new_json(args.output, result)
    except (ValueError, TypeError, OSError) as exc:
        parser.exit(2, str(exc) + '\n')
    print(body.decode('utf-8'), end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
