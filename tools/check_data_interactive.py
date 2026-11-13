#!/usr/bin/env python3
"""Mac double-click entry; audits downloaded files, never connects to the robot."""
from datetime import datetime
from pathlib import Path
import shlex
import webbrowser
from check_data import audit, load_plan, save_report


def unpack(text):
    parts=shlex.split(text)
    if len(parts)!=1:
        raise ValueError('一次选择一个数据目录或文件。')
    return Path(parts[0]).expanduser()


def main():
    default=Path(__file__).resolve().parents[3]/'real_data'
    print('小车数据检查：只读取原始数据，报告另存。')
    print('把下载的原始数据文件夹拖进这里，再按回车。')
    if default.is_dir():
        print('直接回车：检查本备份包内的真实历史数据。')
    text=input('数据位置：').strip()
    source=unpack(text) if text else default
    plan_text=input('实测站点表（可选；没有就直接回车）：').strip()
    plan=load_plan(unpack(plan_text)) if plan_text else None
    report=audit([source],plan)
    out=Path.home()/'Documents'/'RBE201TC数据检查'/datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    page=save_report(report,out)
    print('检查完成。报告：'+str(page))
    print('错误：',report['summary']['errors'],'提示：',report['summary']['warnings'])
    webbrowser.open(page.as_uri())


if __name__=='__main__':
    try:
        main()
    except (OSError,ValueError,EOFError) as exc:
        print('未完成：',exc)
    input('按回车关闭此窗口。')
