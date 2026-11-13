> 2026-11-06更新：下文描述最初纯状态机阶段。现已增加line_guidance、MissionRuntime/PassiveCapture/MissionLoop、独立localhost运行入口与完整隔离演练。最新见MISSION_INTEGRATION.md。当前仍未启用或验收实车自动驾驶，真实黑线/路线/停稳条件缺失。

# 自动停稳／定点任务：仅离线状态机准备

日期：2026-11-04。**没有接入生产服务、循迹控制器、采样器或实物执行器。当前仍不是可运行的自动任务功能，也不是自动循迹／正式房间测绘的验收证据。**

新增 [mission_plan.py](<backend/mission_plan.py>) 只验证输入、维护内存状态并返回不可执行的 `Intent` 数据；[测试](<backend/tests/test_mission_plan.py>) 中的全部站点、顺序、时间与参考值均为 `SOFTWARE_FIXTURE`，不是标定或现场数据。模块不提供 CLI、网络路由、线程、时钟读取、文件写入、串口／GPIO／电机／DHT 调用，也不导入这些依赖。未修改现有 [server](<backend/server.py>)、[acquisition](<backend/acquisition.py>)、[motor_control](<backend/motor_control.py>)、[control.js](<frontend/control.js>) 或 [hardware_worker](<backend/hardware_worker.py>)。

**既有手动长按规则保持不变：方向键／WASD 按住多久动多久、松开停车、空格急停；不增加按键总时限。** 本模块的 `mission_timeout` 等阈值仅是未接入的自动任务 reducer 输入，不约束或修改手动控制、原租约／看门狗规则。

## 1. 默认拒绝，不能用计划准备代替物理条件

`MissionPlanner()` 没有默认计划，`start()` 必须拒绝。即使显式构造 `MissionPlan`，仍须提供以下全部输入；任何缺项都不能由代码补造：

| 输入 | 验证与真实来源边界 |
|---|---|
| `mission_id / group_id / room_id` | 显式标识；任务、组、房间不能留空。任务标识须由未来适配器保证跨进程／重启不复用，模块没有全局登记簿。 |
| `StationPlan` 有序元组 | 每站明确 `station_id / marker_id / x_m / y_m / height_m`；坐标有限、非布尔，高度非负；站点和标记各自唯一。顺序只能来自实际路线／位置关联，不能把第几个黑块自动等同于第几个站点。 |
| `CalibrationEvidence` | 重用 [line_calibration](<backend/line_calibration.py>) 重新验证**原始白纸与黑胶带参考**、每通道对比及物理左右顺序；不接受仅有 `configuration_complete=True` 的汇总。物理顺序无默认值。 |
| 物理条件的证据标识 | 必须声明真实的左右顺序、路线／站点关联、标记识别、独立停稳确认、时间参数的证据索引；本模块不读取或签名验证这些材料。 |
| `Timing` 的全部字段 | `max_observation_age / max_update_gap / stop_timeout / settle_seconds / adapt_seconds / capture_timeout / mission_timeout` 均显式、有限、正数；**没有可复制的生产数值或默认值**。停稳／DHT 适应时间必须来自真实安装与环境验证。 |

真实 [站点表](<config/stations.csv>) 在本次读取时仍只有表头。没有生成正式任务配置，没有填入任何模拟坐标、黑白电平、左右顺序、车速或时间参数。

**结构验证不等于真实性鉴定。** 即使一份伪造声明能满足类型／字段检查，也不能获得真实标定、停稳或路线安全性；软件无法从输入字符串证明证据存在，更无法检测参考采集后安装位置、光照或材料是否改变。当前缺少现场条件，因此不能部署或启用自动运动。

## 2. 观察输入契约

`Observation` 是未来适配器提供的数据，不是本模块自己采集：

- `observed_at` 与方法参数 `now` 必须处于**同一个单调时钟域**，不得混入墙钟、设备间未转换的时钟或旧缓存。
- `sequence` 与 `observed_at` 都必须严格递增；输入陈旧／未来时间、回放、乱序、序号重置、时钟回退、更新间隔超限均锁存失败。
- `source_id` 必须匹配校准参考的主机标识，`physical_pins` 必须匹配既有通道；检查的是数据一致性，不是硬件身份认证。
- `line_values` 只经已验证标定检查／归一化，不自行推导转向、速度、PWM 或标记。`line_trackable` 与 `marker_id` 的真实生成、消抖、辨识及误检验证**尚未实现**。
- `zero_command` **不能代替** `stationary_confirmed`。后者必须由尚待设计／验证的独立物理条件提供；当前控制器输出为零不是车轮真正静止的证据，不能在适配器里直接复制这个布尔值。
- `control_healthy / emergency_stop` 必须为明确布尔值。`emergency_stop()` 方法不需要任何新传感器输入，便于未来高优先级停止通道使用；它目前仍仅返回抽象停车意图。

处于活动任务时，每次有效状态转换（包括 pending 确认、结果回调）都要带一份新的合格观察；缓存帧不得反复累计“已停稳”时间。

## 3. 状态与抽象意图

| 状态 | 转换条件 | 唯一可能的意图类别 |
|---|---|---|
| `idle` | 只有显式计划、原始标定和健康／可追踪／无旧标记起点才允许开始 | `follow_line` |
| `following` | 只接受下一计划站点的已识别标记；未知／乱序标记或丢线失败停车 | `follow_line`，到标记后 `hold_stop` |
| `stopping` | 在停车期限内同时观察零指令和独立物理停稳确认 | `hold_stop` |
| `settling` | 连续合格观察下完成实测停稳等待；失去停稳即失败，不悄悄重置后续跑 | `hold_stop` |
| `adapting` | 再完成独立环境适应等待，不能倒填成此前已等待 | `hold_stop`；完成时仅一次 `request_capture` |
| `awaiting_pending` | 等待记录器确认对应事件的 pending **已持久化** | `hold_stop` |
| `awaiting_result` | 等待对应站点／请求／事件的唯一持久终态 | `hold_stop` |
| 下一站或 `completed` | 仅匹配、及时且已持久提交的 `success` 才可推进；最后一站保持停车 | `follow_line` 或 `hold_stop` |
| `failed / cancelled / emergency_stopped` | 终态锁存，无 resume/retry/restart API | `hold_stop`；当时尚有采集请求才另外发一次 `cancel_capture` |

这些类别都不是电机命令或已执行动作。`Intent` 没有车速／轮速／PWM／温湿度字段；没有解释器会自动执行它们。`snapshot()` 始终包含 `integrated=False / executes_hardware=False / writes_records=False`。

同一标记持续出现时，不再创建第二次采集。一次成功之后，必须先有明确无标记的新观察，下一站才重新武装；未清除就出现下一标记、清除后重现旧标记都拒绝。离开已完成标记仍要求 `line_trackable=True`，不能把“忽略重复”当作丢线继续跑的许可。

## 4. 与持久记录器的未来接口，**目前未接入**

本模块遵循 [实验记录约束](<DATA_COLLECTION.md#L62-L84>) 的方向，但没有修改或包装 [RunLog](<backend/experiment_log.py>)，也没有把内存结果写成 CSV：

1. `request_capture` 只携带任务／站点／唯一请求标识。未来适配器必须使用原唯一记录器，建立请求到真实 RunID／event_id 的绑定；本模块没有 RunID 管理、运行打开／关闭或持久 outbox。
2. 记录器实际 pending 原子落盘后，适配器才可调用 `confirm_pending(..., durable=True)`。此回调**不触发额外采样**；真实“先 pending 后观察”仍由记录器保障。若未来记录器先返回终态却尚未完成该确认，规划器保守失败，不能用成功回调倒补这个次序。
3. 下一次新发布结果的观察仍须由已有采样路径完成，不新增 DHT／GPIO／UART 所有者，不把 `last_good` 或触发前缓存结果改成当前样本。
4. 只有真实记录器持久终态提交后才能 `accept_result(..., durable=True)`；匹配请求、事件、站点、期限，且观察仍新鲜／停稳才接受。重复与迟到回调不刷新活跃期限，不改写已接受终态，不完成后续站点。
5. `sensor_failure / request_failure / timeout / cancelled` 都让本任务停止、保留该终态类别，不自动重试到成功。温湿度空值、失败解释、SHA／清单、原子写盘和历史恢复**仍全部由现有记录器负责**；本模块不接受温湿度数值，因此不能造出成功行或补零。
6. 模块超时／取消／急停只发 `cancel_capture` 意图，**并不声称真实 CSV 已变 cancelled**。若记录器已进入 terminal commit 并返回 `result_committing`，未来适配器必须保留真实已提交终态并维持停车／人工核对，不能改写历史、伪称取消成功或自动继续。

## 5. 尚缺的整合／安全条件

- 真实路线与标记辨识／消抖、循迹转向律、停车反馈、环境适应验证都未完成；状态机不是循迹算法。
- 没有执行循环、后台定时器或硬实时保证；只有调用方法时才检查超时。调用线程完全停住时，本模块无法主动停车。未来必须依赖既有独立电机租约／看门狗与独立急停，不能替换或放宽它们。
- reducer 只支持单一所有者串行调用，没有线程锁。未来传感器、记录器和用户事件必须先经有界串行仲裁，不能让 worker 回调直接并发修改状态；其队列、饱和、提交仲裁、终态意图可靠交付都尚未实现。
- 没有生产鉴权、CSRF、控制所有权、多人操作冲突处理或 HTTP 路由；不能把测试中的抽象意图接到现有键盘控制旁边，形成第二个串口／电机所有者。
- 所有状态与 `outcomes` 只在内存，后者只是状态回执索引，不是实验记录。进程退出后禁止重建为“接着跑”；未来须有持久取消墓碑／恢复核对与全局唯一任务标识，防止旧回调重放。
- 输入健康、零指令、停稳、标记身份及 evidence_id 目前由调用者声明，不具备真实性或完整性认证；必须先有经过现场核验的适配器与安全评审。
- 本轮没有向真实 HTTP8765、SSH、UART、GPIO 或实体底盘发出请求，没有改变在线控制页面／原运动逻辑；不要求用户当前返场验证，也不把缺现场写成组员没有完成。

## 6. 实际验证与证据等级

只运行本机纯软件单测；全部虚构参数仅存在 [SOFTWARE_FIXTURE 测试](<backend/tests/test_mission_plan.py>)。实际命令、数量、退出码、日志与版本见 [任务QA](<outputs/RBE201TC_RESUME_20261104/offline_next_20261104/mission/QA_REPORT.md>)。复现入口：

```bash
# cwd: backend
python3 -B -W error -m unittest discover -s tests -p test_mission_plan.py -v
```

这只能证明被测软件转移与拒绝规则，不证明实物停车、采样时间精度、循迹成功率或正式场地完成。现场条件收齐后仍需先审查／整合，再在独立安全隔离流程中真实验证；本轮未提供启用自动运动或覆盖生产服务的步骤。
