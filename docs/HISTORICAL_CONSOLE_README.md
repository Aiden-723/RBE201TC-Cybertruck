> **2026-11-06离线工作补齐**：自动任务的引导/循环/持久采集/异常处理和显式运行入口已实现并进行软件验证，详见MISSION_INTEGRATION.md；新增能耗计算见ENERGY_PREPARATION.md。实车自动驾驶仍未启用，默认入口只检查配置，不能把模拟标定或假停稳信号用于真实执行。手动控制和三类硬件采样逻辑保持原样。

> **当前状态（2026-11-06）**：无线连接、断线重连和两个开机服务已上线。启动器检测到电机USB时开启原speed模式，未接时提供sensor-only联调；当前已恢复电机控制。方向组合键已支持，默认前右转指令为左200/右0，全部松开停车。下方只读/尚未开机启动/未修改方向控制等描述属于早期历史，勿按旧状态重新部署。最新运行说明见 `deploy/README.md`；新增离线数据检查与只读稳定性观察见 `DATA_CHECKS.md`。正式自主循迹、现场地图及MATLAB执行仍未完成。

> 2026-11-05 更新：当前实机使用 `readonly-current` 独立发布目录，通过 Autoboat-5G 无线SSH访问。桌面传感器记录已上线；`rbe201tc-sensors` 和 `rbe201tc-monitor` 已设置开机启动。当前不启用电机接口，仅开放 bench_diagnostic，不能作正式房间采集或自动循迹验收。下面早期部署记录保留其历史语境，最新说明见 `deploy/README.md`。

# RDK X5 传感器工作台

首页显示真实MIPI相机画面、DHT11环境温湿度、四路循线输入，并提供直接按键的架空车轮测试。默认启动不打开电机串口；只有命令行指定 --motor-port 才运行控制服务。页面不需要勾选或点击启用。原可选被动串口功能仍默认关闭，不能与电机控制同时启用。

新增人工停稳站点持久CSV与MATLAB分析工具，详见 [采集说明](<DATA_COLLECTION.md>)；可复用探头黑白参考记录/验收工具见 [标定说明](<CALIBRATION.md>)。这些增量工具不改变按键控制，不代表自动循迹或正式房间地图已经完成。

## 实机接口

- RDK X5 V1.0 / Ubuntu 22.04 / Python 3.10。
- 循线：Hobot.GPIO，BOARD物理针脚15、18、16、11，仅配置为输入。高低电平实时显示；黑白极性及探头实际左右位置需要用实物核对。
- DHT11：课程配套C程序，Linux GPIO401（物理针脚37）。每2.5秒后再次尝试，使用CPU7与FIFO优先级50。校验失败保留失败状态；页面明确标注9秒内的上次有效读数，绝不补零或伪造测量。
- 相机：使用实机已有hobot_vio/libsrcampy和OpenCV，CSI1/自动检测，640×360 JPEG预览。无需/dev/video节点。相机采集成功不等于完整视觉功能或循线已验证。
- 不记录视频；最新JPEG和有限历史放在RAM缓存目录。每类硬件独立子进程，故障不会阻塞HTTP。
- 640×360预览：采集目标20帧/秒，网页目标15帧/秒。网页按绝对时间点调度，统计实际加载的不重复帧，暂停或断网时不继续宣称实时帧率。实机测试10秒加载150个不同帧，约15.0帧/秒；该结果不代表其他分辨率或网络条件下的保证。
- 网页仍只监听127.0.0.1，通过SSH转发访问。JPEG接口沿用API认证，令牌不写入浏览器存储。

## 文件

backend/hardware_worker.py 为硬件采样进程，必须显式启动，要求已安装课程依赖。
backend/readings.py 只读取固定缓存文件，使用单调时钟判断过期。
backend/native/dht11_rdk_x5_gpio401.c 原样保留课程配套代码；不属于本监控台新算法。
backend/server.py 为普通用户HTTP服务；电机串口只在指定 --motor-port 启动时初始化。
backend/motor_control.py 负责按住式四路速度/PWM控制、独立看门狗和串口独占。
frontend/control.js 负责按住式键盘/触屏控制和松键停车。
frontend/ 为无外部依赖的中文界面。

## 运行

停止其他使用同一GPIO、相机、DHT11或电机串口的课程程序。传感器调试时电机保持断电；测试电机前把四个轮子架空，并确保随时能断开电机电源。

在本目录编译课程DHT11辅助程序：

    gcc backend/native/dht11_rdk_x5_gpio401.c -o backend/native/dht11_server -Wall

终端一显式启动采样（PATH按实际解压位置调整）：

    mkdir -p /dev/shm/rbe201tc-sensor-studio
    sudo python3 backend/hardware_worker.py \
      --state-dir /dev/shm/rbe201tc-sensor-studio \
      --dht-binary "$PWD/backend/native/dht11_server"

终端二以普通用户启动网页：

    python3 backend/server.py --port 8765 \
      --sensor-state-dir /dev/shm/rbe201tc-sensor-studio

电脑通过SSH转发：

    ssh -L 8765:127.0.0.1:8765 sunrise@192.168.127.10

浏览器打开 http://127.0.0.1:8765/ 。暂停只暂停网页刷新，不关闭后台采样。断开网络后，旧画面会明确标为过期。Ctrl+C可分别关闭采样和HTTP服务；不要与line_follower.py同时采样。

若用非回环地址监听，仍必须设置DEVICE_CONSOLE_TOKEN；不要直接暴露到公网。本交付不设置开机启动、不安装系统软件、不修改摄像头或GPIO系统配置。

## 键盘差速车轮测试

用以下启动参数替换上面的网页启动命令（不要同时运行两个网页进程）：

    python3 backend/server.py --port 8765 \
      --sensor-state-dir /dev/shm/rbe201tc-sensor-studio \
      --motor-port /dev/ttyUSB0

页面连接后直接按住方向键或WASD，不需要勾选或点击启用。第一次按下时自动建立控制会话；启动或载入页面不会自动转动。松键发停车指令；空格/Esc急停，下次重新按方向键即可继续。离开页面会停车，回来直接按键即可。屏幕方向按钮支持按住鼠标或触屏。M1–M4按钮每次只给一个通道正方向指令，用来确认位置和极性。

采用课程line_follower.py的默认映射：M1/M2左侧、M3/M4右侧。前进[+,+,+,+]，后退[-,-,-,-]，左转[-,-,+,+]，右转[+,+,-,-]。左右为原地差速转向，无转向轴。映射与正负方向尚未经过实物核对；先逐轮测试，再测试整车方向。

默认使用速度模式$spd，设定200，可调80–220，与交接资料中实际转动成功的课程测试保持一致；它不是实测轮速。可显式用--motor-mode pwm切换原始PWM模式。串口相邻帧至少间隔30ms，防止多条停止/运动指令拼成一帧。按住多久就转多久，没有连续转动时长上限；松键发送停车指令。每100ms左右续期一次，板端450ms未收到续期会尝试停车并撤销会话，迟到指令不能重新起转。60秒未操作也会停用。页面失焦、隐藏、关闭或通信失败会请求急停。暂停传感器刷新不等于停车，请使用红色急停。

控制只允许回环地址监听及SSH转发；POST需要同源Origin、随机控制密钥、有效会话及递增序号。串口写入失败锁定故障，不自动重连起转。停止依次发送PWM零、速度零和stop。串口成功写入不能证明实际停车；主板死机、断电或驱动器断连时，软件停车可能无法送达。如轮子持续转动，立即断开电机电源。

## 验证

在backend目录运行：

    python3 -W error -m unittest discover -s tests -v

在本目录运行：

    node --test frontend/tests/*.test.cjs

测试使用的夹具不是现场测量。实机验收另需检查三个传感器均有数据、移动相机/遮挡探头的响应，以及DHT11读数与采样时间。板载温度与DHT11环境温度分开显示。若设备日期错误，页面保留提示；记录课程测量数据前需校时。

直接按键版在RDK上39项后端测试、Mac上22项前端测试通过；电机测试使用模拟串口或虚拟串口，不代表实物运动已验证。浏览器已验证直接按方向键/WASD、单轮按钮、松键、空格急停后继续、失焦返回后继续、超过4秒持续按住、断网超时及桌面/手机布局。后端模拟持续按住5分钟仍继续发送指令，松开后停车。实机读回Motor_Version:1.7.3、Motor_type:1、Dead_Zone:1600、Pulse_Line:11、Pulse_Phase:30、wheel_diameter:67.000、电压11.7V；这些不证明编码器或实际轮速有效。JPEG响应携带同一实际文件对应的X-Frame-Id，浏览器重复收到同一帧时不重复计数。

## 实现参考

MIPI采集流程以目标机自带示例及D-Robotics官方接口说明为准：
https://d-robotics.github.io/rdk_doc/en/Basic_Application/pydev_demo_sample/RDK_X5/mipi_camera_sample/

原课程文件与旧版监控台保留在原位置；新版单独部署，可回退。
