# 一起开发

仓库主页的README和docs/CURRENT_STATUS.md是当前入口。历史脚本放archive，不要从历史包挑一个直接覆盖主程序。

## 两个人如何提交

仓库所有者可以在Settings → Collaborators邀请朋友。获邀接受后，双方都可以克隆同一仓库；没有写权限也可以Fork后提交Pull Request。

每项修改开一个分支，例如：

```bash
git clone https://github.com/Aiden-723/RBE201TC-Cybertruck.git
cd RBE201TC-Cybertruck
git switch -c fix/manual-steering
# 修改代码
python3 scripts/check_all.py
git add backend/motor_control.py backend/tests/test_motor_control.py
git commit -m "Fix sustained differential steering"
git push -u origin fix/manual-steering
```

然后在GitHub点Compare & pull request，让另一个人看过差异再合并。下一项工作前切回main并git pull，避免覆盖对方改动。

## 分工和交接

- 每个Issue写清现象、涉及文件、预期行为和验证方法。电机输出、循线、页面可以分开，但共同改变协议或左右约定时先约定。
- 共享实车时一次只有一人部署和控制电机；另一人先做离线工作。记录当前提交SHA，不混用两个版本。
- 改测试不能只是为了通过：保留原本行为合同；先用用例复现错误，再修实现。
- 不把串口目标当成实测轮速，不把模拟IMU角度当成真实90度，不把生成CSV当成真实测量。
- 自动目标最高20、前进纠偏四轮同向、保持单页UI、无驾驶结果填写弹窗，是当前需求。
- 不提交私钥、密码、真实连接配置、实车原始个人日志、课程PDF或虚拟环境。

## 完成一次修改

PR说明问题与改变后的行为、测试结果、是否部署以及实际车轮观察。需要实车确认时明写“未验证”，并在完成后更新CURRENT_STATUS.md；不要让后来者把代码存在当成硬件已成功。
