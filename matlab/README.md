# MATLAB 房间温湿度地图（尚待真实 MATLAB 执行及正式数据）

**2026-11-11状态：37个软件测试已写，MATLAB实际执行0个。** 本机PATH及有界安装位置未找到MATLAB入口；只进行了源码复核、测试准备和MISS_HIT 0.9.44静态检查。静态通过不代表数值、MATLAB API、图形导出或夹具清理已通过运行验证。

当前[地图脚本](<matlab/map_room.m>)要求MATLAB R2020b+。本轮只修现有输入/输出可靠性；**没有加入房间底图、边界mask或露点，没有修改统计、用途筛选和绘图方法，没有生成正式地图。** 本轮证据见[MATLAB QA](<outputs/RBE201TC_MATLAB_ENERGY_20261111/matlab/QA_REPORT.md>)和[环境探针](<outputs/RBE201TC_MATLAB_ENERGY_20261111/matlab/environment_probes.json>)。

## 1. 输入与调用

根据[采集说明](<DATA_COLLECTION.md>)提供真实站点记录。当前实现只分析`room_stationary`，不接受无位置的bench数据，也未在本轮扩展为移动采样分析。

```matlab
addpath('/实际路径/device-console-v2/matlab');
rawFiles = ["/真实数据路径/RunA/temperature_points_RunA.csv"; ...
            "/真实数据路径/RunB/temperature_points_RunB.csv"];
result = map_room(rawFiles, "/一个尚不存在的目录/room_analysis_01");
```

也可传入原始RunID子目录所在目录，递归查找`temperature_points_*.csv`。不要重复传备份副本。输出必须是新的、非空、非缺失、非纯空白路径；拒绝既有文件/目录。创建目录失败或`mkdir`报告目录已存在时，不继续写入。**这不是对恶意并发文件系统的事务保证。** 原始CSV不修改，分析目录放在正式raw之外。

## 2. 校验与统计定义

- 只接受`Status=success`、`RunPurpose=room_stationary`，且具有GroupID、RoomID、StationID、有限米制X/Y/非负高度和有效温湿度。身份文本不得为空、全空白或含首尾空白，不静默修复。
- 所有必需字段按字符串导入，保留ISO时间与事件编号原文。缺坐标等列报`RBE:Schema`，不猜旧数据位置。
- 事件编号先按**十进制/科学计数文本精确判整数**，再转binary64，必须正且小于`flintmax`。`1`、`01`、`1.0`、`100e-2`和`.01e2`是同一编号；同一RunID不得重复，报`RBE:DuplicateEvent`。`1.0000000000000001`等真实小数不能因浮点舍入冒充整数；极大指数不会构造巨型补零数组。允许数值文本外部空白和正号，不接受复数记法。原始文本不重写。
- 时间必须为记录器毫秒ISO-8601：`yyyy-MM-ddTHH:mm:ss.SSSZ`或`yyyy-MM-ddTHH:mm:ss.SSS±HH:mm`。结果不得早于触发；非负有限`Delay_seconds`与时间差的误差门槛为0.02秒，加1ns舍入裕量以包含20ms边界。这不是时钟精度或响应时间声明。
- 软件筛选温度-20～80°C、RH 0～100%；有限真实0可接受，缺值/失败绝不补0。该边界不是传感器量程或精度标定结论。坐标、温湿度和延迟中的非零虚部排除。
- bench、pending、失败、超时、取消、未知状态和无效行进入排除表。无有效房间观测报`RBE:NoValidRoomData`，只留排除审计，不产图。
- 小组/房间不得混用。有效观测的高度跨度、同站点X或Y跨度不得超过0.001m；边界有机器舍入裕量，不代表测量达到1mm精度。不同StationID最终X/Y完全相同也拒绝。
- 每RunID/StationID先求事件均值，再对本站点的运行均值等权汇总；触发更多次不增加该run的权重，未测站点/run不补0。平均等待时间同样两级等权。
- `N_runs`为本站点有效运行数、`N_events`为事件数。SD是**运行均值之间的n-1样本SD**，不是传感器精度。只有一个有效run时SD为NaN。
- 摘要分组排序，接受/排除事件保留输入顺序和来源。排除保留首个命中原因：身份、状态、用途、位置、传感值、时间；只用筛选后的有效事件检查组/房间/高度/位置一致性。

## 3. 原有输出和覆盖边界

成功分析输出：`accepted_events.csv`、`excluded_rows.csv`、`run_station_means.csv`、`station_summary.csv`、温度/湿度PNG和FIG以及`analysis.mat`。

**目录存在不代表分析成功。** 混组、混高度、位置矛盾等失败也可能留下排除审计；失败目录不能原样重用。

少于3个非共线站点只画实测点；否则原有natural插值仅在测点凸包内，外部NaN/透明，禁止外推。凸包不是房间边界，未建模墙、家具或可达区。应另外说明真实路线/站点、未覆盖区域、安装高度、坐标测量方法、环境变化、DHT响应和不确定性。该算法阈值不是课程最低测点数。

## 4. 软件测试：37个已写，0个实际MATLAB执行

[测试入口](<matlab/run_offline_tests.m>)与[测试类](<matlab/tests/TestMapRoom.m>)无需RDK或房间数据，需MATLAB R2020b+、内置`matlab.unittest`和可用的不可见图窗导出。

```matlab
addpath('/实际路径/device-console-v2/matlab');
results = run_offline_tests();
disp(table(results));
```

```bash
matlab -batch "addpath('/实际路径/device-console-v2/matlab'); results = run_offline_tests(); disp(table(results));"
```

入口仅在真实runner返回TestResult后打印`RBE_MATLAB_TEST_SUMMARY=`单行JSON，含MATLAB版本、实际测试总数、Passed/Failed/Incomplete计数；之后仍调用`assertSuccess(results)`使失败或未完成batch失败。**这段新入口也未在MATLAB实际运行，不能由源码标记推断已经执行。**

原32项涵盖：重复/无效身份、bench与非success、缺列和坏数值、混组/房间/高度/位置、等权统计和SD、时间/时区、实测0、少点/共线不插值、两种FIG凸包外透明、原CSV不变和既有输出不覆盖。

本轮新增5项：浮点会舍入成整数的小数与极端指数拒绝；精确整数文本/指数别名接受；科学计数别名重复拒绝；空/缺失/纯空白输出在读取输入前拒绝；父路径为文件导致创建失败时原数据不变。

**隔离：**每项使用`TemporaryFolderFixture`及明确的`SOFTWARE_FIXTURE_NOT_COURSE_DATA`身份/说明；合成CSV和图/MAT只属于临时夹具树。测试未向正式raw写入样本。正常清理由框架负责，异常退出可能残留；夹具不是真实课程观测。即使以后37项全通过，也不替代实车测量和正式地图。

## 5. 仅离线静态检查

```bash
/Users/your-user/.local/bin/uv --offline tool run --from miss-hit mh_lint \
  --matlab 2020b --input-encoding utf-8 --ignore-config --brief \
  matlab/map_room.m \
  matlab/run_offline_tests.m \
  matlab/tests/TestMapRoom.m
```

[本轮静态原始日志](<outputs/RBE201TC_MATLAB_ENERGY_20261111/matlab_static_lead_validation.log>)只证明3个M文件的静态结果；[历史QA](<outputs/RBE201TC_RESUME_20261104/matlab_qa/QA_REPORT.md>)不作为本轮新测试执行证据。
