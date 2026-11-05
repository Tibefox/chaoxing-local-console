"""Paths and hardware selection for the packaged, per-user Windows installation."""
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parent
INSTALL = ROOT.parent
CONFIG = ROOT / 'runtime' / 'deployment.json'


def config():
    try:
        return json.loads(CONFIG.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return {}


def bundled(relative):
    return INSTALL / relative


def manager_candidates():
    paths = []
    saved = config().get('mumu_manager')
    if saved:
        paths.append(Path(saved))
    try:
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for branch in (r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall',
                           r'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall'):
                try:
                    with winreg.OpenKey(hive, branch) as key:
                        for index in range(winreg.QueryInfoKey(key)[0]):
                            try:
                                with winreg.OpenKey(key, winreg.EnumKey(key, index)) as item:
                                    name = str(winreg.QueryValueEx(item, 'DisplayName')[0])
                                    if 'mumu' not in name.lower():
                                        continue
                                    location = Path(winreg.QueryValueEx(item, 'InstallLocation')[0])
                                    paths.extend((location/'nx_main/MuMuManager.exe', location/'MuMuManager.exe'))
                            except OSError:
                                pass
                except OSError:
                    pass
    except ImportError:
        pass
    for drive in 'CDEFG':
        for base in ('Program Files/Netease/MuMu', 'Program Files/NetEase/MuMuPlayer', 'MuMu'):
            paths.append(Path(drive+':/')/base/'nx_main/MuMuManager.exe')
    return list(dict.fromkeys(p for p in paths if p.is_file()))


def manager_path():
    found = manager_candidates()
    return found[0] if found else Path(config().get('mumu_manager') or 'MuMuManager.exe')


def model_device():
    value = config().get('model_device', 'none')
    if value != 'none' and not re.fullmatch(r'Vulkan\d+', value):
        raise ValueError('模型设备配置无效，请运行首次配置')
    return value


def model_layers():
    return '0' if model_device() == 'none' else '99'


def detect_model_devices():
    result = subprocess.run([str(bundled('vendor/llama/llama-server.exe')), '--list-devices'],
                            capture_output=True, timeout=25,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    output = (result.stdout + result.stderr).decode('utf-8', 'replace')
    if result.returncode:
        raise RuntimeError('模型运行库无法启动，请安装随包的 VC++ 运行库后重试')
    devices = re.findall(r'^\s*(Vulkan\d+):\s*(.+)$', output, re.M)
    # Prefer a dedicated card over an iGPU reporting shared system RAM as VRAM.
    devices.sort(key=lambda pair: (not bool(re.search(r'RTX|GTX|Radeon RX|Arc A|Arc B', pair[1], re.I)), pair[0]))
    return devices


def configured():
    return config().get('setup_complete') is True and (ROOT/'runtime/android/device.json').is_file()
