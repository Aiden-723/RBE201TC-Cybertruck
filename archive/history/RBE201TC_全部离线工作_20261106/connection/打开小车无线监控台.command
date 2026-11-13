#!/bin/sh
set -eu
CONNECTION_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
"/opt/homebrew/opt/python@3.14/bin/python3.14" "$CONNECTION_DIR/supervise_wifi_connection.py"
printf '%s\n' '正在等待小车联网和监控程序启动…'
ATTEMPT=0
while [ "$ATTEMPT" -lt 45 ]; do
    if /usr/bin/curl --noproxy '*' --silent --fail --max-time 2 http://127.0.0.1:8765/api/v1/health >/dev/null; then
        /usr/bin/open http://127.0.0.1:8765/
        exit 0
    fi
    ATTEMPT=$((ATTEMPT + 1))
    sleep 1
done
printf '%s\n' '还在等待小车。请确认主板供电正常、电脑和小车连着同一个 Autoboat 路由器；后台会继续重连。'
exit 1
