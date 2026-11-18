# 学习通本地控制台 · Windows / MuMu

通过 MuMu 中的学习通群页面识别随堂练习和投票，检索本地课件并调用 Qwen 模型，串行处理活动、核对平台回执后返回群聊。仅用于本人获授权的课程活动或演示测试。

[下载 v0.1.6](https://github.com/Tibefox/chaoxing-local-console/releases/tag/v0.1.6) · [安装与升级](docs/INSTALL.md) · [源码构建](docs/BUILD.md) · [版本说明](docs/RELEASE-v0.1.6.md) · [验证记录](docs/VERIFICATION-2026-11-06.md)

## 安装入口

下载 Release 的 `Chaoxing-Online-v0.1.6.zip` 和 SHA-256 文件，校验、完整解压后运行 `Setup.cmd`。安装器会下载并校验约 2.5 GB 模型，随后进入配置；MuMu 和学习通通过官方渠道安装。首次配对、本人登录、通知权限及每周时段按[安装流程](docs/INSTALL.md)核对。

公开包只含原创示例知识库，不含个人账号、课件、密钥或活动历史。请导入有权使用的资料，示例内容不能替代课程知识。升级选择原安装目录，保留用户资料和已有时段，包括空时段列表。

## 功能与边界

- 群页面监看、练习/投票排队去重、单选 A～M 与判断题处理、提交回执核对及回群。
- Windows DPAPI 凭据保护、本人首次登录核对、验证码转人工；桌面启停与可编辑每周时段。
- 当前构建固定目标群“软件工程(唐班)”。新安装默认周三、周五 18:00–19:51；已有机器以实际配置为准。
- 开发分支在现有单选、判断和投票读题流程中支持文字图片 OCR。练习与投票各自使用原自动提交开关；图片归属、识别文字或选项控件无法核验时转人工。独立 OCR 窗口可用于只读预览。
- v0.1.6 沿用 v0.1.5 核心程序与已有验证。完全干净 Windows 的端到端验收、修复后的新真实投票仍未完成；MuMu 系统自身崩溃未修复。历史回执只证明提交，不代表教师判分正确，详见[验证记录](docs/VERIFICATION-2026-11-06.md)。

## 实现与代码

流程：群卡片 → 控件树读取 → BM25 检索课件 → 本地模型生成与校验 → 页面选择提交 → 平台回执 → 回群。

| 模块 | 职责 |
|---|---|
| `android-notifier/` | UiAutomation 页面读取、群导航、队列、原生页面处理、登录与每周启停 |
| `pipeline.py`、`course_qa.py`、`local_model.py` | JSON Schema 校验、JSONL/BM25 检索、Qwen3-4B-Instruct-2507 Q4_K_M 推理 |
| `app_runtime.py`、`android_events.py` | 状态与单实例锁、通知收件 |
| `android-notifier/packaging/` | 白名单构建、安装与升级 |

模型由 llama.cpp 加载，仅通过本机 `127.0.0.1:29686` 调用。课件位于 `materials/slides.jsonl`，实际答题每题最多检索 2 个片段，不使用向量数据库。活动过程记录仅保存在本机 `runtime`，排查时先脱敏。

## 许可

原创代码采用 MIT；Qwen 权重采用 Apache-2.0，llama.cpp 采用 MIT。其他组件遵循各自许可，见发行包 `payload/licenses` 与 vendor 目录。
