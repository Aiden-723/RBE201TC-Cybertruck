# 桌面手动小车控制台

## 用户操作

双击桌面的[小车控制台](</Users/your-user/Desktop/小车控制台.app>)。程序窗口等待安全连接，连通后自动打开原入口 <http://127.0.0.1:8765/>。

- 不创建开机项、登录项、LaunchAgent，也不要求DSH会话一直开着。
- 关闭程序窗口、点“退出并断开”或按⌘Q，停止这个程序自己建立的连接。浏览器页面本身不是连接程序；只关浏览器不会关闭连接窗口。
- 再次双击只显示已有窗口，不开第二条隧道。
- 小车未联网时，只在程序开着的期间重试；退出后不再重试。
- 这是现有网页的连接程序，不是另一个服务器，不改变默认速度200、按住即转、松开停车、空格急停。

## 持久文件与权限

[Swift源码](<desktop/desktop_app.swift>)、[可复现构建脚本](<desktop/build_desktop_app.py>)、[手动runtime安装脚本](<desktop/install_desktop_runtime.py>)均保留。构建脚本拒绝覆盖任何已存在App。

程序运行组件安装在用户Library/Application Support下，避免登录后的Documents隐私权限差异；不复制、读取私钥内容或放宽SSH验证。原先的[命令启动器](<desktop/打开小车无线监控台.command>)现在也只打开同一个桌面App，不另外启动后台进程。

```text
App：~/Desktop/小车控制台.app
运行组件：~/Library/Application Support/RBE201TC/connection
日志：~/Library/Logs/RBE201TC/desktop-app.log
连接日志：~/Library/Logs/RBE201TC/desktop-supervisor.log
私钥仍在：~/.ssh/rbe201tc_autoboat_ed25519
仅本机监听：127.0.0.1:8765
```

## 单例与退出边界

Python通过固定位置flock持有唯一连接；App只承认自己创建的Process、其独立进程组、匹配state的SSH子进程和lsof确认的唯一127.0.0.1:8765监听。HTTP健康检查禁止重定向，成功后再次确认连接归属才开浏览器。不会信任磁盘上可能在重启后复用的旧PID，不会杀未知端口占用者或接管其他程序。

退出只向本实例拥有的、经过getpgid核验的进程组发送SIGTERM。小车上原有网页/传感器服务继续按原项目运行，不属于Mac新增自启动项；关闭连接程序不会重启小车或改其系统服务。

## 电机冷启动检查

[连接前检查](<desktop/prepare_console_before_connect.py>)仅因用户打开App而运行，不在Mac启动时或小车开机时新增执行项。先用原有安全条件校时；已确认固件/电压回复时绝不复位电机板。

若两条应用回复均为空，且确认未驾驶、四路0、没有采集/提交/关闭作业、未发送过运动指令，才允许调用[已验证的原应用恢复助手](<desktop/recover_motor_application.py>)。同一个小车内核boot_id最多尝试一次，标记在小车/dev/shm，失败不循环复位。

恢复助手先核验两份完整原Flash备份一致及既定SHA256、STM32 PID0414和当前向量匹配，再用ROM GO启动现有应用；不擦除、不刷固件、不写OptionBytes、不解除保护。只暂停原网页电机服务以释放串口，finally恢复它；传感器服务不重启。会话中断会转为异常以执行finally。失效或活动状态时跳过维护，不擅自停车来强占控制。

## 网络事实

[严格SSH配置](<desktop/autoboat_ssh_config>)仅允许两个已有相同固定主机公钥的地址：有线192.168.127.10、Autoboat局域网192.168.86.5。只在重新连接时按顺序尝试，校时和转发使用同一认证目标；健康连接不因插线而抢占。没有扫描局域网、动态信任新密钥或修改Mac接口/路由。

网线插入不等于小车eth0已经有链路。本轮实际检查小车eth0为DOWN、wlan0为UP；因此当时通过Autoboat局域网的WiFi端到达小车，不把Mac网线接口UP误称为全程有线。
