# 源码与构建

需要 Windows x64、含 Tk 的 Python 3.14、Android SDK build-tools/android.jar/JDK（构建两个辅助 APK）。只使用 Python 标准库，无 pip 依赖。

1. 将 Qwen3-4B-Instruct-2507 的兼容 GGUF 放入 models/chat-verified.gguf；将 llama.cpp Windows Vulkan 发行物放入 vendor/llama，将 Android platform-tools 放入 vendor/adb，保留它们的许可。
2. 将 config 下四个 JSON 复制到 runtime/android。将 materials/slides.example.jsonl 复制为 materials/slides.jsonl，或提供有权使用的知识库。
3. 检查 android-notifier/build.py 与 ui-reader/build_reader.py 的 SDK/JDK 路径，运行它们，输出 runtime/android-build/course-notifier.apk 和 ui-reader/course-ui-reader.apk。签名私钥仅本机保留。
4. 将微软官方 VC_redist.x64.exe 放入 runtime/packaging-cache。公开构建不需要 MuMu/学习通安装程序。
5. 运行 python android-notifier/packaging/build_package.py --output 新目录 --no-archive，再运行 archive_package.py 新目录。大于 2 GiB 的包按 Release 合并脚本的方式分卷。

测试：python -m unittest discover -s android-notifier -p "test_*.py"；python -m unittest test_android_events；python -m unittest discover -s android-notifier/packaging -p "test_*.py"。

第一次没有登录或配对前，不要直接启动接收。不要提交 runtime、模型密钥、DPAPI 容器、原始课件或私钥。

联网包：保留完整 manifest（包括模型哈希），将 download_model.py 复制到 Setup.cmd 同目录；Setup.cmd 先运行随包 python/python.exe -I download_model.py，成功后再启动 install.py。生成发行 ZIP 时只排除 models/chat-verified.gguf 文件，其他文件保留。下载器会在执行清单校验之前恢复该文件。
