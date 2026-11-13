# 快速开始

## 电脑预览与测试

安装Python3.10+和Node.js22+。后端离线测试和普通网页预览不需要安装RDK专用库，也不会打开GPIO或电机串口。

```bash
python3 -B backend/server.py --port 8765
# 浏览器打开 http://127.0.0.1:8765/
```

按Ctrl+C结束服务。界面无实时硬件数据是正常的，不能把等待状态当成真实车轮或传感器反馈。

```bash
python3 scripts/check_all.py
```

也可分别运行：

```bash
PYTHONPATH=backend:backend/tests python3 -B -m unittest discover -s backend/tests
PYTHONPATH=backend:tools python3 -B -m unittest discover -s tools/tests
node --test frontend/tests/*.test.cjs
PYTHONPATH=desktop python3 -B -m unittest discover -s desktop/tests
```

Windows用check_all.py，避免手写Unix环境变量语法。Linux的串口虚拟端口用例需pySerial；可安装requirements-test.txt。部分真实日志回放在没有私有原始日志时明确跳过。Node tests不运行browser-check.cjs；该文件是独立的旧Chrome界面检查工具。

## RDK环境

RDK系统已有镜像依赖包括Hobot.GPIO、hobot_vio/libsrcampy、OpenCV、NumPy；它们不通过普通Mac的pip安装。电机/IMU需要pySerial，C辅助程序需要gcc、util-linux里的chrt/taskset。

```bash
gcc -Wall -Wextra backend/native/dht11_rdk_x5_gpio401.c -o backend/native/dht11_server
```

`deploy/*.service`保留了这辆车的路径约定；新主板要按实际安装位置修改WorkingDirectory和辅助程序路径。当前速度协议与两线TT不匹配尚未解决，不能把克隆后启动全部服务当成运动验收。

接入既有小车前，先读CURRENT_STATUS.md；确认没有现存采样器和串口所有者冲突。部署不等于自动驾驶，页面打开或服务启动不应自动发起行驶。

## MATLAB

需要MATLAB R2020b+及matlab.unittest。调用：

```matlab
addpath('matlab');
run_offline_tests();
% 使用真实定点采集的CSV生成新输出目录：
% map_room("data/raw/某次运行", "analysis/新的目录");
```

当前没有MATLAB执行通过的证据。bench_diagnostic不能作为正式房间地图数据；实测站点坐标和测量高度不可猜填。
