# 学习通本地控制台 · MuMu / Windows

使用 MuMu 的 Android 页面识别、Qwen 本地模型与知识检索，串行处理目标群的随堂练习和投票，并核对回执、返回群聊继续监看。旧网页轮询和网页提交分支已移除。

[下载安装包](https://github.com/Tibefox/chaoxing-local-console/releases/tag/v0.1.0) · [完整安装流程](docs/INSTALL.md) · [源码构建](docs/BUILD.md)

## 当前能力

- 群页面监看、活动弹窗取消、练习/投票排队处理、提交回执核对及回群。
- 随堂练习选项 A～M、多种文本块与页面布局，本地模型选择答案。
- DPAPI 账号密码加密、首次本人登录核对、过期密码页单次恢复尝试；验证码交给本人。
- 桌面控制台、一键结束所属子进程和 MuMu、可编辑/取消的北京时间每周时段。
- 当前构建固定目标为“软件工程(唐班)”（兼容全角括号），默认周三、周五 18:00–19:51。

公开包只包含原创示例知识条目。请导入你有权使用的课件资料；示例知识不能替代课程知识。源码和发行物均不包含个人账号、密码、登录态、通知/模型密钥、真实课件和活动历史。

## 安装

下载同一 Release 的所有 `.part01`、`.part02`、`Merge-Package.ps1` 和 `Merge-Package.cmd` 到同一个文件夹，双击 CMD 合并并校验 ZIP，再完整解压并运行 `Setup.cmd`。
存在 D 盘时默认安装到 `D:\Apps\ChaoxingConsole`，否则使用当前用户 LocalAppData。
MuMu 与学习通为第三方专有软件，须通过官方渠道安装；公开包不重新分发其安装程序。首次配置的“安装 MuMu”会打开官方网站，在 MuMu 应用中心安装学习通后再配对。

## 验证范围

发布前已通过原生功能、登录、停止、队列、通知收件箱及安装/升级检查，并在本机独立目录检查随包 Python、Tk 与安装流程。
**完全干净 Windows 的端到端实测尚未完成**：本机 VMware 嵌套虚拟化预检受 Hyper-V/VBS 限制。当前发布属于预发布，首次登录、真实课堂活动及自动启停应在目标电脑复验。

仅用于本人获授权的课程活动或演示测试，遵守课程规则。对结果仍须核对；不要把未知页面自动授权给脚本。

## 结构

`app_runtime.py` 管理状态和单实例锁；`android_events.py` 只接收通知；`android-notifier/` 管理群导航、队列、原生页面处理、账号设置、MuMu 与每周时段；`pipeline.py`/`course_qa.py`/`local_model.py` 完成本地推理；`android-notifier/packaging/` 管理封装和安装。

## 许可

本项目原创代码采用 MIT。Qwen 权重采用 Apache-2.0，llama.cpp 采用 MIT，Python、ADB、VC++ 等遵循各自许可，详见发行包 `payload/licenses` 及 vendor 目录。
