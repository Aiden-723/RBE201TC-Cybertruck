# 循迹参考标定：只读工具与当前边界

## 当前真实状态

- 用户已确认四轮前后左右方向正确，电机电池已拔下，继续保持传感器调试。
- 白纸参考已完成：[22次白底新读数](<outputs/RBE201TC_RESUME_20261104/line_white_reference_20261104T114543_45c11c.json>) 全部为 X1–X4 `[0,0,0,0]`，对应 BOARD 针脚 `[15,18,16,11]`。
- 用户没有黑胶带，明确选择稍后再测。**没有黑底实测、没有确认物理左右顺序，不能设置 BLACK_LEVEL 或启用自动循迹。**
- 不要求重做白底。若后续更改安装高度、光照、阈值或探头位置，则原参考的适用性需要重新检查。

## 增量工具

[校验逻辑](<backend/line_calibration.py>) 是纯数据函数；[命令行工具](<tools/line_calibration.py>) 只 GET 既有状态，或离线读取JSON。不打开 GPIO/UART，不发控制 POST，不改变运行服务或按键行为。生成的配置是**待整合记录**，绝不自动部署或驱动。

### 离线检查已有白底，不连接机器人

在本项目根目录：

```bash
python3 tools/line_calibration.py check --surface white_paper \
  --input /实际路径/line_white_reference.json
```

只确认该单色参考稳定；输出 `polarity_verified=false` 是正确状态，因为单独白底无法确定黑线极性。不能把“白底全0”等同于已检测到黑线。

### 有实际黑胶带、用户摆放就绪后再采集

保持正常安装/离地高度、光照，黑胶带区要覆盖四探头正下方，不能用手或屏幕黑色替代实际路线材料。电机电池保持拔下。

```bash
python3 tools/line_calibration.py capture --surface black_tape \
  --setup '填写实际高度、材料、光照和电机断电状态' \
  --output /新的证据目录/black_reference.json
```

默认采样窗3.2秒，至少需要15个不同且连续递增的新快照；这是**参考记录窗口，不是运动时长限制**。逐路必须在该窗口稳定，实际针脚一致，数据年龄小于2秒，无错误。失败参考也保存原始样本和错误，不覆盖旧参考，不自动重试或默认为0/1。

### 黑白都通过后生成待整合配置

```bash
python3 tools/line_calibration.py build \
  --white /真实白底参考.json --black /真实黑底参考.json \
  --output /新目录/line_calibration_staged.json
```

会重新核对每个实际样本，不相信输入文件自带的“stable”总结。每路黑白必须相反；同电平、闪变、过期、重复缓存、针脚变化、非0/1、时区缺失或不同主机记录会拒绝。支持每路极性不同，不能强制套一个未经验证的全局常量。

物理从左到右顺序默认空。只有现场逐路确认后，才可以给 `build` 添加 `--left-to-right` 并填四个实际通道名；不要抄默认 X1 X2 X3 X4 当成实测。缺少顺序时即使黑白通过，`configuration_complete` 仍为false；归一化函数拒绝为自动控制提供假定顺序。

所有新输出拒绝覆盖已有路径。配置始终标明 `applied_to_live_service=false`、`autonomous_route_verified=false`；即便配置完整，也不是自动循迹、停车或鲁棒性已经通过的证明。

## 后续整合与验证

1. 等实际黑胶带就绪，完成黑底和探头物理左右顺序。
2. 在现有单一采样/电机所有者上整合手动与自动，不能并行启动原课程程序抢资源。
3. 先短路线反复验证，再逐步增加弯道、横向标记、光照变化和丢线停车；记录真实成功/失败和设置。
4. 任何自动路线实测都需用户另行安排现场、电机电源和安全空间；当前不擅自试转。

自动循迹、正式坐标CSV和房间地图仍未完成。本工具的测试在 [测试文件](<backend/tests/test_line_calibration.py>) 中，只用明确标记的软件夹具，不是实物黑底标定数据。
