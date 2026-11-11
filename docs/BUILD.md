# 源码与构建

需要 Windows x64、含 Tk 的 Python 3.14，以及 Android SDK build-tools、android.jar、JDK（构建两个辅助 APK）。运行时代码只使用 Python 标准库，无 pip 依赖。先阅读 [安装流程](INSTALL.md) 和 [验证范围](VERIFICATION-2026-11-06.md)。

## 准备构建输入

1. 将安装文档指定的 GGUF 放入 `models/chat-verified.gguf`，校验相同 SHA-256。将 llama.cpp Windows Vulkan 发行物放入 `vendor/llama`，将 Android platform-tools 放入 `vendor/adb`，保留各自许可。
2. 将 `materials/slides.example.jsonl` 复制为 `materials/slides.jsonl`。公开发行只能使用该示例，不能误打包个人课程资料。默认配置从 `config` 读取，无需复制个人 runtime。
3. 检查 `android-notifier/build.py` 与 `android-notifier/ui-reader/build_reader.py` 的 SDK/JDK 路径，运行它们，输出 `runtime/android-build/course-notifier.apk` 和 `runtime/android-build/ui-reader/course-ui-reader.apk`。签名私钥仅本机保留。
4. 将微软官方 `VC_redist.x64.exe` 放入 `runtime/packaging-cache`。公开构建不需要 MuMu 或学习通安装程序。

```powershell
python -m unittest discover -s android-notifier -p "test_*.py"
python -m unittest test_android_events
python -m unittest discover -s android-notifier/packaging -p "test_*.py"
python android-notifier/packaging/build_package.py --output D:\Release\Chaoxing-Online-v0.1.6 --no-archive
```

输出目录必须不存在。构建器使用明确的文件清单，拒绝私有 runtime、DPAPI、数据库和密钥文件。构建后仍须人工核对源码、知识库及许可证；白名单不等于任意文件内容都可公开。

## 联网发行 ZIP

完整载荷含模型，用于本地安装校验；GitHub 联网 ZIP 只排除模型本体，保留 manifest 中的模型条目。以下命令在源码根目录执行，按实际情况修改第一行输出路径：

```powershell
$releaseDir = 'D:\Release\Chaoxing-Online-v0.1.6'
Copy-Item android-notifier/packaging/download_model.py "$releaseDir\download_model.py"
Copy-Item LICENSE "$releaseDir\LICENSE"
@'
@echo off
setlocal
cd /d "%~dp0"
"%~dp0payload\python\python.exe" -I "%~dp0download_model.py"
if errorlevel 1 (
  pause
  exit /b 1
)
start "" "%~dp0payload\python\pythonw.exe" -I "%~dp0install.py"
'@ | Set-Content "$releaseDir\Setup.cmd" -Encoding ascii
& "$releaseDir\payload\python\python.exe" -I "$releaseDir\install.py" --verify-only
if ($LASTEXITCODE -ne 0) { throw '载荷校验失败' }
@'
import hashlib, pathlib, sys, zipfile
root = pathlib.Path(sys.argv[1])
archive = root.with_name(root.name + '.zip')
with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED, compresslevel=6) as output:
    for file in sorted(root.rglob('*')):
        if not file.is_file() or '__pycache__' in file.parts:
            continue
        relative = file.relative_to(root).as_posix()
        if relative != 'payload/models/chat-verified.gguf':
            output.write(file, root.name + '/' + relative)
with zipfile.ZipFile(archive) as check:
    assert check.testzip() is None
with archive.open('rb') as stream:
    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
archive.with_suffix('.zip.sha256').write_text(digest + '  ' + archive.name + '\n', encoding='ascii')
print(archive, digest)
'@ | python - "$releaseDir"
```

不要在输出目录加入凭据、个人配置或测试记录。重新解压 ZIP、通过 `download_model.py` 恢复模型后运行 `Verify.cmd`，再在独立目录验证安装；不要在当前接收端运行期间启动另一个实例。

如果只需要内部完整离线包，可对未改动的完整构建目录运行 `python android-notifier/packaging/archive_package.py 完整目录`。含模型的 ZIP 可能超过 GitHub 单个 Release 资产的大小限制；本仓库公开 Release 使用上述联网包，不提供旧式分卷合并脚本。

## 发布检查

确认 README、使用说明、manifest、构建器版本及 Release 标签一致，运行全部回归和载荷校验。公开仓库与 ZIP 不得包含账号、登录态、通知或模型密钥、真实课件、活动历史或签名私钥。第一次没有登录或配对前，不要直接启动接收。
