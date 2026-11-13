from pathlib import Path
import shutil,json,hashlib,zipfile,subprocess,io,tarfile
base=Path.cwd();out=base/'outputs';evidence=out/'RBE201TC_RESUME_20261105/home_completion'
bundle=out/'RBE201TC_完整接手_20261106'
bundle.mkdir(exist_ok=False)
def copytree(source,dest):
 shutil.copytree(source,dest,ignore=shutil.ignore_patterns('__pycache__','*.pyc','.DS_Store','.git','*.sock'),dirs_exist_ok=True)
def copy(source,dest):
 dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,dest)
copytree(base/'work/device-console-v2',bundle/'project/device-console-v2')
copy(evidence/'rdk_snapshot/deployed_source/backend/native/dht11_server',bundle/'project/device-console-v2/backend/native/dht11_server')
for name in ('autoboat_ssh_config','known_hosts','maintain_wifi_connection.py','supervise_wifi_connection.py','sync_clock_before_connect.py','打开小车无线监控台.command'):
 copy(base/'work/rdk_connection'/name,bundle/'connection'/name)
copytree(base/'work/rdk_connection/tests',bundle/'connection/tests')
copytree(evidence/'rdk_snapshot/real_data',bundle/'real_data')
copytree(evidence/'dht_stability',bundle/'diagnostics/dht_stability')
copytree(evidence/'data_report',bundle/'reports/data_check')
copy(evidence/'RESULTS.md',bundle/'reports/本次完成记录.md')
copytree(evidence/'rdk_snapshot/system',bundle/'system_snapshot')
for folder in ('RBE201TC_RESUME_20261104','RBE201TC_RESUME_20261105'):
 copytree(out/folder,bundle/'evidence'/folder)
copy(out/'RBE201TC_RESUME_20261105/CONTINUE_HERE.md',bundle/'CONTINUE_HERE.md')
copy(out/'RBE201TC_HANDOFF_20261104/COURSE_REQUIREMENTS.md',bundle/'COURSE_REQUIREMENTS.md')
originals=[Path('/Users/your-user/Documents/Codex/2026-11-05/ni/outputs/RBE201TC_Harness原始资料与项目备份.zip'),Path('/Users/your-user/Documents/Codex/2026-11-05/ni/outputs/RBE201TC_完整性核对与接手指引.md'),out/'RBE201TC_FULL_HANDOFF_20261104.zip']
for p in originals:copy(p,bundle/'original_inputs'/p.name)
(bundle/'00_先读.md').write_text('''# 小车最新完整接手包 · 2026-11-06

先读本文件，再读CONTINUE_HERE.md；历史压缩包/README中的旧状态不能覆盖最新说明。

## 现在能做什么

- 主板连接Autoboat-5G，电脑通过同一路由器访问。主板Wi-Fi和两项服务开机启动；Mac连接监督程序在本次登录会话中自动重连。电脑重启后双击原工作目录的打开小车无线监控台.command。它不是LaunchAgent。
- 相机目标采集20fps/网页15fps；四路循线原始读数和DHT11在线。网页按方向键/WASD按住多久转多久，松开停车；组合前右转左200/右0，单独左右原地转向。用户已反馈转弯可用；编码器/定量轮速没有验收。
- USB电机驱动板在服务启动时存在则开启speed控制，不存在则sensor-only；不是热插拔自动接管，不自动重连故障串口起转。
- 人工停稳站点记录、失败保留、运行恢复、CSV下载已实现。桌面联调数据不会计为正式房间地图。
- 新增离线数据检查工具与10分钟真实DHT稳定性报告。233次中216成功/17校验失败，成功率92.704%，仍有偶发读错。无丢发布/过期/计数器重置，不代表准确度测量。

## 直接查看

1. reports/data_check/report.html：双击打开现有真实采集记录检查。
2. reports/本次完成记录.md：本次结果、证据和未完成项。
3. project/device-console-v2/tools/检查采集数据.command：双击后可输入数据目录；直接回车检查本包real_data，输出报告单独保存在Documents/RBE201TC数据检查。
4. diagnostics/dht_stability：本次原始快照、233条派生诊断CSV、汇总与原始哈希。不是课程站点CSV，没有编造坐标。

## 包内内容

- project/device-console-v2：最新源码、测试、说明、MATLAB代码、配置、ARM版DHT辅助程序。
- connection：当前电脑的连接/重连/校时助手与公钥指纹。配置含原电脑的绝对路径；换电脑须调整路径并另行授权SSH。
- real_data：从当前RDK下载的两次真实历史运行，包含正常结束和故意中断测试，原样保留。
- system_snapshot：实际systemd服务、Wi-Fi省电配置、无密码的Wi-Fi概要。
- evidence：11月4日/5日各轮真实验证记录，旧版本与失败过程保留。
- original_inputs：用户提供的最新Harness原始资料包、完整性指引与11月4日完整接手包，原字节保存。课件、设备照片、早期源码和旧对话资料在这些原始包内。
- MANIFEST_SHA256.json：本包每个文件的校验值；verify_backup.py用于只读核对。

这是项目级恢复备份，不是整张SD卡系统镜像。SSH私钥仍保留在原电脑~/.ssh/rbe201tc_autoboat_ed25519，没有打包；系统镜像/驱动安装包、MATLAB授权不在本包。

## 下一步边界

没有胶带时可以继续软件准备；不能声称已完成自主循线。真实黑线/探头顺序、自动路线/站点整车集成、教室站点坐标与正式测量、MATLAB实际运行、CAD及组员真实资料仍待完成。不得补造实验数据。课件视频/讲解AI限制见COURSE_REQUIREMENTS.md。

按用户习惯：不要增加电机启用勾选、固定长按时限或重复确认。已有四轮方向和可用手动转弯反馈，不重复要求从头做。新聊天先只读核对在线状态，再按具体任务操作；不要套用旧固定hash部署器或历史PID。
''',encoding='utf-8')
(bundle/'01_恢复与继续工作.md').write_text('''# 恢复与继续工作

## 原电脑、原主板仍可运行

先使用原工作目录 `/Users/your-user/Documents/Codex/2026-09-18/di/work/rdk_connection/打开小车无线监控台.command`；网页 http://127.0.0.1:8765/ 。电脑和小车在Autoboat路由器网络中，当前主板192.168.86.5，DHCP地址可能变化。

连接命令：

```sh
ssh -F /Users/your-user/Documents/Codex/2026-09-18/di/work/rdk_connection/autoboat_ssh_config rbe201tc-wifi
```

不要另起抢占8765的转发。Mac重启后需启动一次监督程序。主板断电可能丢钟，现有连接预检在健康、未armed、零指令、无采集中时校正；采集中不改钟。

## 仅项目文件损坏，需要恢复

先用verify_backup.py核对本包；另备份主板当前experiments和代码。恢复操作会中断网页，必须确认没有正在采集/驾驶，然后停止rbe201tc-monitor与rbe201tc-sensors，等待整个采样服务退出。不要启动第二个GPIO/相机/串口所有者。

将project/device-console-v2复制到主板的新发布目录 `/home/sunrise/rbe201tc-sensor-studio/releases/restore_实际时间/`，保持sunrise可读；不要覆盖experiments。确认ARM辅助程序可执行（必要时chmod +x backend/native/dht11_server），或在RDK使用gcc从native C源重新编译。将readonly-current指向这个新目录。system_snapshot中两份真实service可用于核对/恢复 `/etc/systemd/system/` 中相应服务；daemon-reload后启用并启动两项服务。保留此前发布指针作为回退。

检查两项服务active不够：必须查看新鲜相机图像、DHT/循线时间戳、CSV历史；电机USB存在时启动器应为speed并初始化零指令。不自动发非零动作。失败时恢复旧发布指针和原服务文件，保留所有实验数据。tools/deploy_web_acquisition.py是固定历史hash的旧工具，不能用于这次恢复。

## 换电脑或系统卡

本包不是SD镜像。需要已有兼容的RDK X5系统、hobot_vio/Hobot.GPIO/OpenCV、pyserial以及系统采样依赖。恢复系统驱动后再部署工程；不能用一份ZIP代替系统安装。

connection/autoboat_ssh_config含原电脑绝对路径。换电脑时调整IdentityFile/UserKnownHostsFile/ControlPath，使用该电脑有权使用的SSH密钥；不要把旧私钥复制给新聊天。启动.command使用原电脑Homebrew Python路径，换机器须换成可用python3。Wi-Fi密码不从系统快照导出，重新入网可使用用户已知的网络信息。不能开启控制端口到公网来绕过SSH。

## 独立检查数据

```sh
python3 project/device-console-v2/tools/check_data.py real_data --output /一个新的报告目录
python3 verify_backup.py
```

即使出现失败、取消或中断提示，原始数据也要保留。MATLAB代码在project/device-console-v2/matlab；32项测试未实际执行，不以静态检查替代。
''',encoding='utf-8')
(bundle/'verify_backup.py').write_text('''from pathlib import Path
import hashlib,json,sys
root=Path(__file__).resolve().parent
manifest=json.loads((root/'MANIFEST_SHA256.json').read_text())
failed=[]
for name,expected in manifest.items():
 p=(root/name).resolve()
 if root not in p.parents or not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=expected:failed.append(name)
print('核对文件数：',len(manifest),'不一致：',len(failed))
for name in failed:print(name)
sys.exit(bool(failed))
''',encoding='utf-8')
# Manifest is computed after evidence and docs are complete; it deliberately excludes itself.
manifest={p.relative_to(bundle).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(bundle.rglob('*')) if p.is_file()}
(bundle/'MANIFEST_SHA256.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
archive=out/(bundle.name+'.zip')
with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6,strict_timestamps=False) as z:
 for p in sorted(bundle.rglob('*')):
  if p.is_file():z.write(p,bundle.name+'/'+p.relative_to(bundle).as_posix(),compress_type=zipfile.ZIP_STORED if p.suffix in ('.zip','.tgz','.png','.jpg') else zipfile.ZIP_DEFLATED)
with zipfile.ZipFile(archive) as z:
 assert z.testzip() is None
 for name,digest in manifest.items():assert hashlib.sha256(z.read(bundle.name+'/'+name)).hexdigest()==digest
sha=hashlib.sha256(archive.read_bytes()).hexdigest()
(archive.with_suffix('.zip.sha256')).write_text(sha+'  '+archive.name+'\n')
result={'archive':str(archive),'bytes':archive.stat().st_size,'file_count':len(manifest)+1,'sha256':sha,'zip_crc_test':'passed','all_member_hashes':'passed'}
(evidence/'backup_verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
print(json.dumps(result,ensure_ascii=False))
