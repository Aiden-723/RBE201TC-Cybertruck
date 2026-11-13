from pathlib import Path
import shutil,json,html,hashlib
base=Path.cwd();out=base/'outputs';evidence=out/'RBE201TC_OFFSITE_COMPLETE_20261106';bundle=out/'RBE201TC_全部离线工作_20261106'
bundle.mkdir(exist_ok=False)
ignore=shutil.ignore_patterns('__pycache__','*.pyc','.DS_Store','.git')
shutil.copytree(out/'RBE201TC_完整接手_20261106',bundle,dirs_exist_ok=True,ignore=ignore)
shutil.copytree(base/'work/device-console-v2',bundle/'project/device-console-v2',dirs_exist_ok=True,ignore=ignore)
shutil.copytree(evidence,bundle/'evidence/RBE201TC_OFFSITE_COMPLETE_20261106',dirs_exist_ok=True,ignore=ignore)
shutil.copy2(out/'RBE201TC_RESUME_20261105/CONTINUE_HERE.md',bundle/'CONTINUE_HERE.md')
shutil.copy2(evidence/'COMPLETE_RECORD.md',bundle/'reports/本次全部离线工作记录.md')
shutil.copy2(evidence/'AI_ASSISTANCE_ACTUAL.csv',bundle/'reports/AI辅助实际记录.csv')
shutil.copy2(evidence/'REQUIREMENT_STATUS.csv',bundle/'reports/要求与完成状态.csv')
shutil.copy2(evidence/'final_verification.json',bundle/'system_snapshot/final_offsite_verification.json')
intro='''# 最新入口：全部当前可做的离线工作 · 2026-11-06

本包优先于同日较早的RBE201TC_完整接手_20261106.zip。双击工作总览.html查看中文结果，再读CONTINUE_HERE.md。

本轮已将循线判断、横向顺序标记、自动停站任务、持久采集、异常处理和显式运行入口整合，完成本机/主板软件演练和回归，并已部署新版代码。默认不开启自动运动，真实标定、路线/站点及独立停稳输入缺失时拒绝启动。没有把模拟值、零指令或未知组员资料冒充实物证据。

最新主板发布为releases/offsite_complete_20261106，readonly-current已切换；旧bench发布保留。手动电机/键盘/硬件采样逻辑未改变，独立采样进程未重启。报告的automatic_stationary新模式只用于新自动任务日志，既有手动默认行为保持。

本机后端184通过/1Linux跳过，RDK后端185通过；工具两端各80通过；前端71通过。七种隔离任务演练都符合预期。真实自动行驶、教室地图、MATLAB实际执行、CAD/学生材料尚未验收，具体缺条件见工作总览和reports/本次全部离线工作记录.md。

最新项目在project/device-console-v2；用法见MISSION_INTEGRATION.md、DATA_CHECKS.md、ENERGY_PREPARATION.md。新模拟数据只在evidence/.../SOFTWARE_FIXTURE_NOT_COURSE_DATA内，真实旧数据在real_data；两者严禁混用。原始用户资料归档在original_inputs内，原字节保留。不是课程最终提交包，也不是系统SD卡镜像。

下面保留早先接手包的背景说明；涉及版本、自动任务代码状态以本节和CONTINUE_HERE.md为准。

---

'''
p=bundle/'00_先读.md';p.write_text(intro+p.read_text())
p=bundle/'01_恢复与继续工作.md';p.write_text('''> 最新发布为releases/offsite_complete_20261106；恢复必须保留未启用自动任务的状态，不创建假停稳文件。下方路径与步骤仍是项目级恢复说明。最新软件状态优先看00_先读.md和MISSION_INTEGRATION.md。

'''+p.read_text())
status=json.loads((evidence/'requirement_status.json').read_text());scenarios=json.loads((evidence/'scenario_summary.json').read_text())
esc=lambda x:html.escape(str(x),quote=True)
status_rows=''.join('<tr>'+''.join('<td>'+esc(v)+'</td>' for v in row)+'</tr>' for row in status)
names={'success':'两站正常采集','sensor_failure':'传感器校验失败','lost_line':'丢失黑线','missing_stationarity':'没有真实停稳确认','timeout':'采集超时','reboot':'采集时主板重启','emergency_stop':'中途急停'}
expected={'success':'completed','emergency_stop':'emergency_stopped'}
case_rows=''.join('<tr><td>'+names[r['scenario']]+'</td><td>'+('两站完成' if r['state']=='completed' else '停止任务，保留原因')+'</td><td>'+('符合预期' if r['state']==expected.get(r['scenario'],'failed') and not r['fault'] else '需要检查')+'</td></tr>' for r in scenarios)
page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>小车离线工作总览</title><style>
*{box-sizing:border-box}body{font:17px/1.75 system-ui,sans-serif;margin:0;background:#f2f5f8;color:#192d40;padding:24px}main{max-width:1080px;margin:auto}header{background:#143d50;color:white;border-radius:18px;padding:30px}h1{font-size:30px;margin:0 0 12px}header p{margin:0;color:#d8ebf4}h2{margin:32px 0 14px}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;margin-top:18px}.card{background:white;padding:22px;border-radius:14px}.card strong{display:block;font-size:25px;color:#126c70}.notice{background:#fff0d9;border-left:5px solid #bf8438;padding:16px 20px;margin:22px 0;border-radius:4px}.scroll{overflow:auto}table{width:100%;border-collapse:collapse;background:white;font-size:15px}td,th{text-align:left;padding:12px;vertical-align:top;border-bottom:1px solid #dce5ed}th{background:#e6edf3}td:first-child{min-width:150px}a{color:#006b8e}li{margin:8px 0}footer{font-size:14px;color:#52697b;margin-top:28px}@media(max-width:600px){body{padding:12px}header{padding:22px}h1{font-size:25px}table{min-width:650px}}</style><main>
<header><h1>小车：离线工作总览</h1><p>当前条件下的软件、模拟验证和交接资料已补齐。实车自动驾驶尚未启用。</p></header>
<div class="cards"><div class="card"><strong>整条任务流程已接通</strong>模拟走线 → 停站 → 采集 → 保存 → 下一站</div><div class="card"><strong>7 种场景已演练</strong>正常完成及故障停车都留下记录</div><div class="card"><strong>现有小车保持可用</strong>键盘驾驶、无线连接、传感器监控保留</div></div>
<div class="notice"><b>软件模拟通过 ≠ 实车验收。</b>模拟数据单独隔离，没有编造教室坐标、真实黑线标定或停稳信号。</div>
<h2>现在能直接打开的结果</h2><ul><li><a href="reports/data_check/report.html">真实历史数据检查报告</a>：12条记录，正常关闭文件校验通过，失败/取消/中断保留。</li><li><a href="reports/本次全部离线工作记录.md">本次完整工作记录</a>：具体实现、测试结果及条件边界。</li><li><a href="project/device-console-v2/MISSION_INTEGRATION.md">自动任务软件用法</a>：默认只检查配置，不会自行开车。</li><li><a href="project/device-console-v2/DATA_CHECKS.md">数据检查与稳定性工具</a>；<a href="project/device-console-v2/ENERGY_PREPARATION.md">能耗计算工具</a>。</li></ul>
<h2>完整演练结果（全部为软件夹具）</h2><div class="scroll"><table><tr><th>模拟情形</th><th>实际软件结果</th><th>判断</th></tr>'''+case_rows+'''</table></div>
<h2>每一项做到哪里</h2><div class="scroll"><table><tr><th>事项</th><th>当前结论</th><th>条件或边界</th></tr>'''+status_rows+'''</table></div>
<h2>接下来真正需要你提供的条件</h2><ol><li>黑胶带、实际探头左右顺序和可行驶的测试空间——这些可在家准备。</li><li>可验证的停稳反馈与真实安装参数——不能用“电机指令为零”冒充。</li><li>MATLAB环境，以及组号、组员贡献、CAD/制造和教师模板。</li><li>到教室后实测路线、站点坐标和温湿度数据。</li></ol>
<p>传感器上轮10分钟实测：233次中216成功、17校验失败，成功率92.7%。失败未补值；还需要实物排查，不能称完全稳定。</p>
<footer>验证：本机后端184通过/1项Linux跳过；RDK后端185通过；工具两端各80通过；前端71通过。没有新发实物非零电机命令。先读00_先读.md；本包是工程交接备份，不是课程最终提交或SD卡镜像。</footer></main></html>'''
(bundle/'工作总览.html').write_text(page)
# Small preview directory avoids exposing the archive's unrelated original inputs.
preview=evidence/'preview';preview.mkdir(exist_ok=True);(preview/'index.html').write_text(page)
print(bundle)
