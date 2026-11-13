# macOS桌面应用与连接工具

`desktop_app.swift`是本地控制台应用；`build_desktop_app.py`在当前用户桌面构建新App，不覆盖已有App。连接监督、时钟前置、恢复助手和安装脚本保留完整源码；导入模块本身不会启动连接。

真实连接配置和known_hosts未公开。准备个人设置：

1. 复制autoboat_ssh_config.example为autoboat_ssh_config，填写自己的板端地址、账号和IdentityFile。
2. 使用自己的SSH密钥；通过实物或其他可信渠道核对主板SSH公钥后，将两种连接地址的可信主机记录写到本目录known_hosts。安装器要求两种地址对应同一块主板公钥，不能用示例公钥冒充。
3. 原项目安装器保留192.168.127.10与192.168.86.5地址合同；使用其他地址时必须同时检查安装器、恢复助手和配置，而非只改一个文本。
4. 阅读安装脚本后在Mac执行`python3 desktop/install_desktop_runtime.py --install`；构建使用`python3 desktop/build_desktop_app.py`。这些不是离线预览步骤，会在本机安装/构建应用。

公开版本把个人绝对桌面路径改为Path.home()；没有更改本地原工作区或已安装App。双击应用才进入连接流程；没有新增开机登录项目。

`install-wheel-console.py`是早期安装方式，供历史查阅；当前优先Swift应用。`DESKTOP_APP.md`保留原说明，其中硬件实态应以docs/CURRENT_STATUS.md为准。
