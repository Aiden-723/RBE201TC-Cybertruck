# 2026-11-13 本地审计补丁（未部署，优先于下文历史）

小车已断电。当前本地有5份代码的未部署修复：手动请求排序、PWM停止路径、损坏驾驶记录加载、被动UART成对控制线、数据预检查。详见工作区outputs/RBE201TC_BOTTOM_UP_FIX_20261113/底层核查与修复结果.md和PATCH_MANIFEST.json；板上最后确认仍v5。启动器仍保留speed可转基线；实际两线TT没有编码器，归档固件已复现无反馈SPD输出积累饱和。此基线不代表正确限速或持续差速已成立。旧PWM不转原因未实物确认，禁止简单切换后宣称修好。不得套用旧固定哈希部署器。以下2026-11-06等条目是历史状态。

---

# 最新运行状态（2026-11-06；优先于下文历史）

当前已更新到离线任务整合发布版，自动任务代码和显式运行入口随包部署，但未启用实车自动驾驶。监控已加载新的记录来源标识支持；传感器进程未重启。新增程序的当前条件与使用边界见../MISSION_INTEGRATION.md。

- 当前发布仍为 `/home/sunrise/rbe201tc-sensor-studio/readonly-current` → `releases/offsite_complete_20261106`；目录名不代表现在永远只读。
- monitor 的ExecStart为 `/usr/bin/python3 -B -u deploy/start_monitor.py`。启动检测电机USB稳定by-id，存在则启用speed；未接则sensor-only。不是实时热插拔接管；串口故障不自动重连起转。
- 默认200，组合前右转(200,200,0,0)、前左转(0,0,200,200)，单独左右仍原地转向。用户已反馈加强后的转弯可用；这不是定量轮速/转弯半径标定。
- 电脑 `desktop/打开小车无线监控台.command` 启动当前登录会话的连接监督程序，掉电/掉线后重连。Mac重启/退出登录后再双击一次，不是LaunchAgent。不要另启竞争的8765转发。
- 主板物理断电可能丢钟，连接助手会在无活动采集且控制未armed、健康零指令时校时。采集中禁止改钟。不得再把写入RTC或软重启成功当作断电保时保证。
- Wi-Fi连接RBE201TC-Autoboat-5G关闭省电，`aic8800_fdrv ps_on=0 dpsm=0` 已持久配置；历史驱动卡死及恢复过程保留在接手记录，不能保证所有网络问题都永久解决。
- 新增 `tools/check_data.py`、`tools/observe_dht.py` 是离线检查/被动观察工具，不改变采样或控制。数据说明见 `../DATA_CHECKS.md`。
- 服务文件与当前源码、远端实态、历史数据应一起备份。下面固定哈希旧部署工具不可强行套用新版。回退前核对实际目录、无进行中采集，并保留所有experiments；不复用历史PID。

---
以下为最初sensor-only发布的历史记录：

# 当前桌面采集部署（2026-11-05）

这是用户不在教室、电机USB暂不连接时的独立部署。只启动三类传感器、监控页面与温湿度联调记录，不接管UART，不产生电机动作。方向键控制源码保持原样；接入电机的服务配置需要在用户恢复驱动板连接后另行调整，不能把本部署称为已完成正式站点/自动循迹上线。

## 运行位置与启动

- 本机连接配置：`desktop/autoboat_ssh_config`；使用其中 `rbe201tc-wifi`，当前DHCP地址192.168.86.5。
- 小车原项目：`/home/sunrise/rbe201tc-sensor-studio/device-console-v2`，没有覆盖。
- 当前发布指针：`/home/sunrise/rbe201tc-sensor-studio/readonly-current`。
- 本次发布：`/home/sunrise/rbe201tc-sensor-studio/releases/bench_20261105T140718Z`。
- 当前服务：`rbe201tc-sensors.service`（root，GPIO/相机独立工作进程）、`rbe201tc-monitor.service`（sunrise，仅127.0.0.1:8765，无motor-port）。
- 采样必须在 `hobot-modules-load` 和 `hobot-rc` 完成之后开始。实测过早初始化相机可导致管线错误并持续无图；不能仅用systemctl active作为相机健康判断。
- 一类传感器只有一个持锁采样进程；不要手动再运行另一个hardware_worker/DHT/相机测试程序。
- 新的权威PID来源是 `systemctl show rbe201tc-monitor -p MainPID` 和 `rbe201tc-sensors`；旧手动启动的web.pid/sensors.pid不再是当前依据。
- 桌面界面仍为 http://127.0.0.1:8765/，通过SSH转发访问，未把控制端口开放到局域网或公网。

## 数据

数据位于小车 `/home/sunrise/rbe201tc-sensor-studio/experiments/bench_diagnostic/<RunID>/`。文件为`temperature_points_<RunID>.csv`、`run_manifest.json`。网页“新建联调运行→记录下一条结果→结束当前运行→下载CSV和运行清单”可使用。

服务端在没有motor对象时选择sensor_only策略，客户端不能请求覆盖。每次采样仍核对RDK身份、时钟、下一个新发布结果、传感器计数/时间以及明确disabled的电机接口。缺失control字段不算disabled，发现电机接口启用则记录失败；不伪造四路0或已停稳。

正式用途在sensor_only服务中直接拒绝。CSV的RunPurpose始终为bench_diagnostic，Motion_evidence明确actual_motion_unknown。MATLAB原地图程序排除全部bench数据。失败和取消的温湿度留空。

网页刷新可继续查看当前运行；正常关闭保存最终hash与行数。进程意外退出时，已经落盘的pending原样保留并标为中断，不自动改成成功或续采。故意中断测试形成的记录也保留，不与正常完整运行混淆。

## 验证与回退

真实证据位于本机 `outputs/RBE201TC_RESUME_20261105/bench_release/`。本轮Mac后端163项中162通过、1项Linux跳过；RDK后端163全部通过；网页66项通过。MATLAB仍为0/32执行，用户已确认暂时无环境。

源码备份`source_before.tgz`、首次候选`source_candidate.tgz`、source_manifest.json和服务安装脚本/日志保留。旧`tools/deploy_web_acquisition.py`是2026-11-04固定哈希、固定电机模式的历史部署工具，**不适用于这个只读发布目录，也不要修改哈希让它强行通过**。

如需回退：先确认没有进行中的采集；停止并禁用这两个本次新增服务，待整组采样进程退出，再用原项目目录恢复单份采样器和旧只读网页。保留experiments不动。原手动命令和PID身份证据在板上`recovery-20261105-services/before.json`；旧PID不可直接复用。恢复旧手动网页不能提供本次新CSV功能。

Wi-Fi自动连接与开机服务已配置；实际重启/相机、记录恢复的最新结果以对应验收JSON为准，不能把一次软重启扩展成所有断电场景都已验证。板上现有NTP服务未在本次安装/修改；曾把当前系统时间写入RTC。采集时发现时间异常应先结束运行再校时。


Motor USB role repair (2026-11-13): install 99-rbe201tc-usb-role.rules and rbe201tc-motor-reconnect.service before using start_monitor.py on this robot. The observed motor adapter is 1a86:7522; the new IMU is 1a86:7523. The shared by-id name is ambiguous and must not be used for motor control. The add-event helper only try-restarts the existing monitor; it never arms or resumes motion.

IMU quarter-turn policy (2026-11-13): start_monitor passes the dedicated IMU alias and deploy/imu_route.json. The read-only reader is part of the existing monitor, not another motor owner. Calibration sets a software origin and restarts route progress; Each explicit Start begins a new trial from the first corner. v2 turns relative to actual entry heading and resumes the next straight only after fresh centered line samples. The actual physical 90-degree turn and line landing remain to be tested. See outputs/RBE201TC_IMU_ROUTE_20261113/RESULT.md for evidence and rollback.

2026-11-13 v4: two fresh WHITE samples confirm corner departure; the fixed entry-relative 90-degree target is closed-loop, with stopped-heading verification and signed small corrections. Internal precision output bypasses startup assist only during endpoint braking/correction. See outputs/RBE201TC_TURN_CLOSED_LOOP_20261113/RESULT.md; real TT correction motion and physical accuracy remain untested.

2026-11-13 v5: ten fresh WHITE samples confirm departure. Nonzero turns observe IMU change over .3s; less than 1.5 degrees adds 20 effort, capped at200, while visible rotation tapers it back. Fine10 is now a base target, not the adaptive cap. Braking and final angle confirmation never ramp effort. See outputs/RBE201TC_TURN_TORQUE_20261113/RESULT.md; physical behavior still requires testing.

2026-11-13 line-only test: deploy/imu_route.json controller=line selects RouteLineFollower, independent of IMU calibration/data. Ten fresh WHITE samples start the configured turn; partial line returns to feedback, two fresh CENTER samples complete the corner. Existing IMU controller is retained for a stopped-process configuration rollback. See outputs/RBE201TC_LINE_ONLY_20261113/RESULT.md. Board is restricted to Autoboat-5G; wireless data and desktop forwarding were verified after reboot with the car Ethernet unplugged.
