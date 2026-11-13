#!/bin/sh
set -eu
CONNECTION_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
CONFIG="$CONNECTION_DIR/autoboat_ssh_config"
if ! /usr/bin/ssh -F "$CONFIG" -O check rbe201tc-wifi >/dev/null 2>&1; then
    if ! /usr/bin/ssh -F "$CONFIG" -f -N rbe201tc-wifi; then
        printf '%s\n' '连接失败：请确认小车已供电、电脑连着 Autoboat 路由器。小车当前记录地址为 192.168.86.5，路由器重新分配地址后需要更新。'
        exit 1
    fi
fi
if /usr/bin/curl --noproxy '*' --silent --fail --max-time 5 http://127.0.0.1:8765/api/v1/health >/dev/null; then
    /usr/bin/open http://127.0.0.1:8765/
else
    printf '%s\n' '无线连接已建立，但监控服务没有响应。请让助手检查小车上的监控程序。'
    exit 1
fi
