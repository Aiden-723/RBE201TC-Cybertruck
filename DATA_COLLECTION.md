> 2026-11-05 最新状态：网页CSV已在独立只读发布目录上线并通过真实桌面采集与下载核对。无电机接口时由服务端固定为 `sensor_only`，仅允许 `bench_diagnostic`；CSV明确记录运动状态未知，坐标留空，正式用途由后台拒绝。旧零指令检查继续用于启用电机的服务，CLI仍保持原严格策略。11条真实测试记录为6成功、4传感器失败、1取消；这些不是正式房间数据。下文2026-11-04“尚未上线”描述历史版本。

# 站点持久采集、网页增量与 MATLAB 分析

日期：2026-11-04。**用户当前不在教室／实际场地，所有现场事项暂缓。** 本轮完成离线软件与证据准备，不补造房间坐标，不要求重复已确认的四轮方向测试。

## 当前完成与部署状态

- 本机现有项目新增网页采集区：[界面](<frontend/index.html>)、[采集交互](<frontend/acquisition.js>)、[独立样式](<frontend/acquisition.css>)、[异步管理器](<backend/acquisition.py>)，复用 [持久记录模块](<backend/experiment_log.py>)。支持新运行、下一次新发布结果、失败／取消保留、历史与CSV／清单下载。
- [命令行记录器](<tools/record_stations.py>) 继续可用；[站点空表](<config/stations.csv>) 仍只有表头。网页与CLI都是人工停稳关联，不是自动循迹、原生DHT触发或自动定位。
- **板上正常服务未被本轮覆盖或重启。** 网页增量已在本机源码和隔离副本实现／验证，尚未上线到 [当前控制台](http://127.0.0.1:8765/)。浏览器截图明确是软件夹具，不是该在线页面或实物测量。
- 原 [键盘控制](<frontend/control.js>)、[电机实现](<backend/motor_control.py>) 和 [硬件采样器](<backend/hardware_worker.py>) 保持原hash。默认200，方向键/WASD按住多久转多久、松开停车、空格急停，不增加电机勾选、启用或总时限。
- 真实验证：RDK独立副本后端 **98/98**、Node前端 **65/65**、真实Chromium隔离流程通过。MATLAB **32个测试已编写但执行0项**，3个M文件仅通过静态检查。详见 [网页QA与部署边界](<outputs/RBE201TC_RESUME_20261104/web_acquisition_qa/QA_REPORT.md>)、[MATLAB QA](<outputs/RBE201TC_RESUME_20261104/matlab_qa/QA_REPORT.md>)。

## 1. 网页操作（未来部署后，不是现在要求现场操作）

1. 默认“无坐标联调”，填写真实说明后点“新建联调运行”；**加载页面不会自动采集或发送运动命令**。
2. 实物已停稳并适应环境后点“记录下一条结果”。先把同一事件持久写成pending，再观察现有采样器的新发布；等待期间不移动。
3. 有效、传感器失败、请求失败、超时、取消各自保留。失败温湿度留空，页面显示“—”，不复用`last_good`，不自动重试到成功。人工再次记录会新增事件，不抹掉旧失败。
4. 取消在终态提交仲裁前被接受，则保存cancelled；提交已开始时明确拒绝取消并提示结果正在落盘，不假称已取消。已完成的终态不改写。“结束当前运行”会等当前收尾并保存清单。
5. CSV与清单通过已认证的固定路径下载。运行中可下载**当前快照**，可能含pending；正常结束后清单才含最终CSV SHA-256与行数，勿把不同时间的两份快照当作最终配对。
6. 刷新页面从后台恢复记录，不靠浏览器本地存储。异常退出后的pending和未正常关闭状态保留，不替历史补造终态。网页显示最近50条事件，CSV保留完整事件。
7. 正式用途必须由实际组号、房间标识及实测StationID/X/Y/高度表创建；没有这些信息就用联调。**当前不要求去测量或填表，更不能填示例值。**

新建、采集和关闭的落盘都由唯一后台记录线程完成；HTTP状态与导出使用已提交的内存快照，不在请求里等传感器或fsync。最多500事件／运行、100个历史运行及16MiB历史常驻缓存，超出缓存的原文件仍留盘，不自动删除。历史加载发生在HTTP启动前；这里16MiB指缓存容量，不是全部候选文件读取总量。坏清单、NaN、截断CSV、失败带数值等不会冒充有效历史。

HTTP改为最多8个在途handler、8个排队连接，保留3秒请求期限；慢CSV下载不独占控制请求线程。**这不是硬实时保证**：8个handler饱和、网络断开或其他异常仍可拒绝／延迟连接，并触发原450ms看门狗停车。限制的是请求／日志资源，不是按键总时长。

## 2. 命令行联调：无位置的数据不能做房间地图

现有服务与控制所有者应在线。无需把CLI部署到RDK，在本机项目根目录运行：

```bash
python3 tools/record_stations.py --purpose bench_diagnostic \
  --output-dir data/bench_diagnostics \
  --notes '软件联调，无房间坐标，非正式测绘数据'
```

CLI通过现有HTTP GET观察；网页通过同一服务的内部快照观察，不向单线程HTTP回调、不启动额外UART/GPIO/相机所有者。新日志API的POST用于写日志，**不是运动控制POST**。观察适配器调用原有电机snapshot时，原有失效停车逻辑仍可能执行；不应字面宣称任何观察都绝不写串口。新采集器不生成非零电机命令。

两种途径都保守要求现有控制服务能核对零指令及健康状态。若仅配置采集目录而未运行原控制所有者，记录会以request_failure留空，不能伪造“stopped”绕过检查。零指令仍不是车轮真正静止的反馈。

三次历史实物bench：首份1 success（24.5°C/59%RH），第二份1 request_failure，最新10条为9 success（24.7°C/60%RH）＋1 sensor_failure。均无坐标、不构成房间成果或机器人成功率。见 [材料审计的CSV核查](<outputs/RBE201TC_RESUME_20261104/course_audit/COURSE_EVIDENCE_GAP_CHECKLIST_ZH.md#L54-L58>)。首份原清单没有raw_csv_sha256／row_count，不能倒称当时已有完整性校验；未修改历史原件。

## 3. 正式采集准备（返场后才做）

1. 人工定点不必等待黑胶带，但必须实际停稳、位置明确、传感器适应环境；不能把它当成自动行驶基线已经通过。黑底／物理顺序／自动路线另行验证，当前全部暂缓。
2. 核对Mac和RDK的日期、时区与实际时间；采集中不改钟。CLI要求两者相差不超过5秒，网页内部适配要求板上时钟健康；门槛不是采样定位精度声明。
3. 记录真实房间原点、X/Y轴、米制测量方法、传感器高度／安装位置、环境与操作干预。DHT11避开板上热源，现场检验适应时间，不声称瞬时测量。
4. 填 [站点空表](<config/stations.csv>)，列为StationID,X_m,Y_m,SensorHeight_m,Notes。网页可导入／粘贴该表；重复ID、空表和非有限坐标拒绝，缺值不补0。

CLI同一次RunID记录多个已停稳站点：

```bash
python3 tools/record_stations.py \
  --purpose room_stationary --stations config/stations.csv \
  --group-id 实际组号 --room-id 实际房间号 \
  --output-dir data/raw \
  --notes '实际坐标原点、测量方法、高度、环境、干预与版本说明'
```

输入实测站点ID，结果返回前保持位置；输入q正常结束。同一运行事件编号递增，重新运行产生新RunID、编号从1开始。默认最多等10秒的新结果；**这是采集等待期限，不是电机长按限制**。

## 4. 原始文件和证据规则

每次运行新建唯一目录，拒绝覆盖。CLI由`--output-dir`决定父目录；网页在固定后台目录下分为`bench_diagnostic/<RunID>/`与`room_stationary/<RunID>/`：

```text
<RunID>/
  temperature_points_<RunID>.csv
  run_manifest.json
```

前八列保留课程字段RunID、Detection point、Trigger time、Result time、Humidity_%RH、Temperature_°C、Status、Explanation；后追加用途、组／房间／站点、X/Y／高度、来源、采样发布时间／计数、等待时间、命令观察与说明。Detection point只是事件编号，不是坐标。

- pending先原子持久写入，再观察新结果；终态仍更新同一行。所有运行及失败均保留，清洗／关联纠错／分析另存，不改原始值。
- 网络失败、重启、时钟异常、被观察到的非零指令不变成有效观测；突然断电／强杀可留下pending，不能补造success。
- 写盘失败锁定记录错误，不能打印假成功。正常关闭清单含最终CSV SHA-256与行数；未正常关闭不能假装已校验完成。
- CSV用户文本按数据导入，不执行单元格表达式。正式归档先做原始备份，再核对清单和输入来源。
- 课程推荐3次完整房间重复测量，**不是本项目自行追加的硬性最低数**；不足则披露局限，不伪造重复。

## 5. 时间／位置关联边界

工具不向DHT辅助程序发送新的READ，只观察现有采样器下一次发布。Trigger time是记录请求开始，Result time是记录器收到结果，Sampled_at是采样器发布时间；**都不是已标定的物理采样瞬间**，底层读取可能早已开始。

只能人工停稳关联，不能把行驶中返回位置当触发位置。自动循迹应在现有唯一硬件所有者上整合明确事件／原生请求与延迟处理，不能并行运行老师程序抢GPIO/DHT/UART。

## 6. MATLAB

见 [MATLAB说明](<matlab/README.md>)、[地图脚本](<matlab/map_room.m>) 及 [32项测试入口](<matlab/run_offline_tests.m>)。只接受有效正式站点，保存排除原因；每运行／站点先平均，再对有效运行等权；单运行SD留NaN。标出实际点，至少3个非共线点才用natural/none凸包内插值，外部留白，不补点、不外推。

**MATLAB执行0/32**；3个M文件仅静态检查通过。无正式场地数据、无现场两图，不把软件夹具、bench读数或未执行脚本当课程成果。

## 7. 未来上线与回退边界

本轮没有覆盖生产目录。将来只有在确认实物停稳、无活动控制会话、做好原文件备份后，才考虑更新网页进程；采样器和原电机／控制文件不动，保持唯一串口所有者，**不能再开第二个控制服务**。

需增量更新的6个文件是 [server](<backend/server.py>)、[acquisition](<backend/acquisition.py>)、[experiment_log](<backend/experiment_log.py>)、[index](<frontend/index.html>)、[acquisition.js](<frontend/acquisition.js>)、[acquisition.css](<frontend/acquisition.css>)。这些新版本已在隔离副本验证，不代表已经运行于板上。

未来只在原启动参数末尾追加固定持久目录，例如`--experiment-dir /home/sunrise/rbe201tc-sensor-studio/data/web_captures`；保留原speed模式和稳定by-id串口路径。不传该参数时，新采集接口显示未配置，原控制不受影响；该参数不是电机启用步骤。此处只记录计划，不要求现在执行。

回退恢复备份的网页／入口与原启动行；不删除已经生成的CSV、清单或历史失败。新增独立模块可留盘而不加载。部署前后都不能把ready／输出0／单测通过扩大成实物停车、轮速精度、自主路线或房间地图验收。
