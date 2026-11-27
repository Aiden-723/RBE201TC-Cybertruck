# RBE201TC Cybertruck

RDK X5 四轮差速小车项目：中文单页控制台、键盘遥控、相机预览、四路循线、DHT11温湿度、IMU相对转弯、数据记录与MATLAB地图分析。

**当前版本使用 PWM 驱动，路线包括 15 个转弯。第 15 个尖角右转已纳入离线回归，现场循线效果以实车记录为准。**

## 从哪里开始

| 你要做什么 | 看哪里 |
|---|---|
| 了解当前进度和待解决问题 | [当前状态](docs/CURRENT_STATUS.md) |
| 在电脑上打开界面、运行测试 | [快速开始](docs/QUICKSTART.md) |
| 了解代码分类与调用关系 | [代码地图](docs/ARCHITECTURE.md) |
| 核对设备、探头顺序和电机协议 | [硬件说明](docs/HARDWARE.md) |
| 和朋友分工、提交修改 | [协作说明](CONTRIBUTING.md) |
| 构建Mac控制台应用、配置SSH | [桌面连接工具](desktop/README.md) |
| 查看原始课程测试代码 | [课程参考代码](reference/course_sources/README.md) |
| 查历史版本和调试脚本 | [历史代码索引](archive/README.md) |
| 看最近底层审计 | [2026-11-13审计摘要](docs/audit/2026-11-13/README.md) |

## 目录分类

```text
backend/        网页服务、电机控制、传感器读取、循线与任务程序
frontend/       原生HTML/CSS/JavaScript控制台，无前端构建步骤
deploy/         RDK服务、USB设备别名、当前弯道顺序
config/         站点模板和历史硬件/标定记录
tools/          CSV检查、采集、标定、能耗分析、任务演练
matlab/         温湿度地图及MATLAB测试
desktop/        macOS应用源码与SSH连接管理
reference/      原始课程程序与历史网页检查脚本
archive/        过去开发过程中的不同代码版本；不作为启动入口
docs/           当前文档、来源清单、审计及历史路径索引
.github/        离线测试CI、Issue和Pull Request模板
```

## 已有功能

- 方向键/WASD按住驾驶、松开停车；组合方向以同向差速转弯。
- 手动/自动模式切换、四探头实时显示、IMU角度与起点软件归零。
- 纯黑单页界面、Cybertruck图片轮胎指令动画、驾驶相机预览。
- 相机采集目标20 FPS，浏览器目标15 FPS；实际显示取决于硬件和网络。
- 独立采集进程、温湿度CSV、离线数据质量检查、功率积分、MATLAB空间分析。

## 当前重点

自动驾驶通过 `deploy/imu_route.json` 选择 `TrackImuFollower`，用四路探头循线。确认弯道后先停下并等待连续 3 个新全白样本，再以当时朝向为参考转向。第 15 弯在过横线时保持直行，重新居中后才允许触发尖角右转。该弯从相对 120° 开始接受重新压线，到 180° 仍找不到线就停车。

当前手动三档为 PWM 550、620、700，自动档为 PWM 600。数值是发给两线 TT 电机驱动板的指令。详细配置见[当前状态](docs/CURRENT_STATUS.md)。

## 电脑本地预览

```bash
git clone https://github.com/Aiden-723/RBE201TC-Cybertruck.git
cd RBE201TC-Cybertruck
python3 -B backend/server.py --port 8765
```

浏览器打开 <http://127.0.0.1:8765/>。该命令没有传入电机或传感器接口，只用于查看界面和只读服务；Mac上没有RDK硬件时相机、探头、IMU显示等待状态。

运行自动检查：

```bash
python3 scripts/check_all.py
```

Python后端使用标准库；Python 3.10+、Node.js 22+可运行主要离线测试。真实RDK还需目标系统自带的Hobot.GPIO、hobot_vio/OpenCV及pySerial等依赖，见[快速开始](docs/QUICKSTART.md)。

## 公开范围与来源

仓库保留当前代码和不同历史源码，历史重复文件通过索引指向同一份内容。没有上传SSH私钥、真实known_hosts/连接配置、Wi-Fi密码、聊天原文、个人照片、课堂PDF、实车原始日志或虚拟环境。桌面工具使用配置模板；个人绝对路径已在公开副本中泛化，原工作区未改变。

课程程序、厂家接口及车辆图片各有原来源，见[来源说明](NOTICE.md)。仓库未统一授予MIT等再许可；公开不改变第三方材料权属。
