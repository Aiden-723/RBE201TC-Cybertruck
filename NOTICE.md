# 来源与材料说明

- 本项目在用户提供的RBE201TC课程源码包基础上开发；reference/course_sources和backend/native保留该来源，未将原课程代码宣称为本项目原创。
- RDK相机和GPIO接口基于目标系统自带库与[D-Robotics官方说明](https://d-robotics.github.io/rdk_doc/en/Basic_Application/pydev_demo_sample/RDK_X5/mipi_camera_sample/)。Hobot.GPIO、hobot_vio、OpenCV、NumPy及pySerial不随仓库打包。
- 电机协议参考[亚博四路电机驱动文档](https://www.yahboom.net/study/MD520/14104)和[Yahboom官方电机驱动模块仓库](https://github.com/YahboomTechnology/4-Channel-Motor-Drive-Module)。未公开分发厂家完整网页或原始固件二进制。
- frontend/cybertruck-photo.jpg是用户提供的网络参考图，用于复现其要求的界面效果；原作者与具体许可尚未核实，不主张其著作权，也不对该图片授予再许可。
- tools/tests/fixtures/legacy_audited_source.zip仅包含历史软件测试夹具，不是实际课程测量数据。
- 部分历史代码以文件哈希固定版本。公开路径泛化之后的差异在docs/SOURCE_MANIFEST.json标明；不能靠替换哈希强行部署旧版本。

本仓库用于项目协作，未统一选择MIT/Apache等许可。各部分既有权属和许可保持原状；如后续正式开源，应由作者确认各部分授权和第三方资产许可。
