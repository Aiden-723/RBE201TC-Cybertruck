#!/bin/sh
set -eu
APP="$HOME/Desktop/小车控制台.app"
if [ ! -d "$APP" ]; then
    printf '%s\n' '桌面的小车控制台程序尚未安装。'
    exit 1
fi
/usr/bin/open "$APP"
printf '%s\n' '正在打开小车控制台；关闭该程序会断开连接，不设置开机自启。'
