# 传感器工作台后端

HTTP由普通用户运行。GPIO、DHT11和MIPI采集由显式启动的独立硬件进程负责，不随HTTP请求初始化硬件。默认不开启串口；显式指定--motor-port后提供按住式车轮测试。完整运行步骤见上级README。

## 接口

- GET /api/v1/health：HTTP进程状态和能力，不代表所有硬件功能正常。
- GET /api/v1/system：Linux主机内存、磁盘、负载、运行时长、板载温度区域。
- GET /api/v1/devices：设备节点以及当前采样状态。
- GET /api/v1/status：聚合health、system、devices、telemetry、sensors、host。
- GET /api/v1/camera/frame：最近的有效JPEG；无有效新鲜帧时返回503 JSON。
- GET /api/v1/control：测试能力、当前指令状态和同源控制密钥，不包含当前控制者会话。
- POST /api/v1/control：仅--motor-port开启时接受arm/drive/release/stop。要求回环Host、匹配Origin、X-Control-Key及现有API认证（若配置）。drive/release还要求会话和递增seq。只接受限定JSON参数，不接受任意串口内容。

arm由第一次按键自动触发，无需人工确认。松开、失焦或急停后，下次新按键可自动建立会话。启动时通过固定的$read_flash#和$read_vol#查询驱动板配置及电池电压，不改变持久参数，也不发送非零运动指令。control快照包含serial_diagnostics、drive_commands_sent、last_drive_age_seconds，区分页面是否发过运动指令和驱动板实际反馈。

MotorControl独立线程执行450ms心跳看门狗和60秒空闲停用，使用单调时钟；正常持续按住没有时长上限，max_hold_seconds为null。会话在失联、急停或故障后失效，迟到包不能重启。生产默认--motor-mode speed，发送$spd，页面默认设定200，允许80–220，沿用此前实际转动成功的课程测试；也支持显式pwm模式。outputs是通道指令，speed_setpoints或pwm依模式填写，另一项为null，command_mode明确区分。它们均不是实测轮速。左右侧遵循课程M1/M2、M3/M4映射，真实方向待核对。串口115200独占打开，写入超时80ms，保持DTR/RTS低，相邻包间隔至少30ms。不自动重连、不改驱动器持久配置；编码器未验证，不能据此声称闭环性能正常。

JSON保留schema_version 1.0、generated_at、data；新字段为兼容性扩展。
host来自实际hostname和/proc/device-tree/model，并提供clock_valid与设备时间。
系统时钟年份小于2025时标记异常，页面提示校时；数据新鲜度始终使用单调时钟。

## 采样结构

sensors下有line_sensors、dht11、camera。每项包含：

- status：disabled / starting / live / error / stale。
- value：当前有效值；失败或过期时为null。
- sampled_at：设备当前时区时间，受设备时钟正确性影响。
- monotonic、age_seconds：用于判断新鲜度，不受墙上时钟校正影响。
- last_good：上次成功读数及其原始采样时间；不将其冒充当前成功读数。
- successes、failures、error：采样结果与错误信息。

循线value为X1–X4四个原始0/1，保留physical_pins [15,18,16,11]。
polarity_verified为false：高低电平不能直接等价于已经校准的黑线和白底。
DHT11 value为temperature_c与humidity_pct，保留最多80个成功样本的近期历史。
DHT11校验失败时，前端仅在上次有效样本9秒内继续显示该值，并清楚标记“读取重试中 / 上次有效”。
相机value包含width、height、frames、fps，JPEG在固定camera.jpg文件中。
相机帧不以Base64放进JSON，前端单独串行请求JPEG并释放旧Blob URL。
640×360采集以20帧/秒为目标，按耗时补偿等待，并报告近期实测采集帧率。JPEG响应的X-Frame-Id与实际读取的原子替换文件对应，前端跳过重复帧。
网页按绝对时间点以15帧/秒为目标请求，页面显示实际加载的不重复帧率；采集端帧率可在提示中查看。实机10秒收到并加载150个不同帧，约15.0帧/秒。

缓存过期阈值：循线2秒，DHT11为9秒，相机3秒。失败不补零，停止采样或进程退出后会自动过期。
配置事实、节点发现、原始数据观测和功能验证分别记录。摄像头使用RDK媒体API，不依赖/dev/video节点。

## 进程与缓存

hardware_worker.py --kind all 启动三个独立子进程，各自采样，互不阻塞。
DHT11调用课程原始C程序，FIFO优先级50、CPU7，每次尝试后等待2.5秒。
相机通过实机已有hobot_vio和OpenCV采集，640×360 JPEG预览。
循线仅将15/18/16/11配置为输入。DHT11的单线读取协议会操作自己的DATA引脚，不操作电机。
每种采样器使用独占锁，重复启动会失败。仍须手动停止不遵守本锁的独立课程程序，再启动采样。
SIGTERM / Ctrl+C触发采样清理。HTTP重启不影响独立采样进程。
状态和最新帧应置于/dev/shm/rbe201tc-sensor-studio的RAM目录，不连续向SD卡写视频。
仅保存最新帧及有限历史，不录制视频。运行日志位于部署目录。

## 认证与网络

默认127.0.0.1:8765。推荐通过SSH本地端口转发访问。
非回环监听必须设置环境变量DEVICE_CONSOLE_TOKEN。
开启认证后，包括health、camera/frame在内的所有API都要求Authorization: Bearer。
静态/index.html、/styles.css、/app.js及/可公开加载，严格允许列表，不支持任意文件、执行、上传、路径穿越或静态符号链接。
前端令牌只保存在内存，不写入URL、日志或浏览器存储。
服务不提供TLS，不应直接暴露公网。HTTP写入方法返回405，无副作用。

## 测试

从本目录运行 python3 -W error -m unittest discover -s tests -v。
实机RDK X5 / Python3.10.12通过24项；另有13项前端测试通过。
覆盖原有解析、系统采集、串口模拟、HTTP安全、CLI退出，以及DHT响应校验、缓存过期、帧认证和传感器故障隔离。
测试夹具不是现场数据，不替代遮挡探头、黑白极性、安装高度和测量精度的实物验证。
