# -*- mode: python ; coding: utf-8 -*-

import os

# SPECPATH 为 spec 文件所在目录（项目根）
project_root = SPECPATH

# Folder distribution avoids unpacking hundreds of MB on every launch.
# Never embed the build machine's absolute path in runtime_tmpdir.
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# 打包的数据文件
datas = [
    (os.path.join(project_root, 'icon.ico'), '.'),
    (os.path.join(project_root, 'version_info.txt'), '.'),
    (os.path.join(project_root, 'models', 'vad', 'silero_vad.onnx'), os.path.join('models', 'vad')),
    (os.path.join(project_root, 'config'), 'config'),
    (os.path.join(project_root, 'data'), 'data'),
    (os.path.join(project_root, 'plugins'), 'plugins'),
    # 只打包本地离线识别模型必需的文件，排除测试音频
    (os.path.join(project_root, 'models', 'sense_voice', 'model.int8.onnx'), os.path.join('models', 'sense_voice')),
    (os.path.join(project_root, 'models', 'sense_voice', 'tokens.txt'), os.path.join('models', 'sense_voice')),
    # 本地神经语音（TTS）模型，离线回退用
    (os.path.join(project_root, 'models', 'vits-melo-tts-zh_en'), os.path.join('models', 'vits-melo-tts-zh_en')),
]
# 唤醒词检测模型（KWS，可选，模型文件存在才打包）
kws_dir = os.path.join(project_root, 'models', 'kws')
if os.path.isdir(kws_dir) and os.path.exists(os.path.join(kws_dir, 'tokens.txt')):
    datas.append((kws_dir, os.path.join('models', 'kws')))

datas += collect_data_files('fake_useragent')
datas += collect_data_files('jieba')

a = Analysis(
    [os.path.join(project_root, 'src', 'main.py')],
    pathex=[project_root],
    binaries=[],
    datas=datas,
    hiddenimports=[
        'numpy',
        'sounddevice',
        'speech_recognition',
        'yaml',
        'win10toast',
        'pyttsx3',
        'pyttsx3.drivers',
        'pyttsx3.drivers.sapi5',
        'win32com',
        'win32com.client',
        'pythoncom',
        'pywintypes',
        'pypinyin',
        'rapidfuzz',
        'pyautogui',
        'jieba',
        'pycaw',
        'comtypes',
        'comtypes.stream',
        'ollama',
        'edge_tts',
        'pystray',
        'pystray._win32',
        'tkinter',
        'sentencepiece',
        'aiohttp',
        'ddgs',
        'lxml',
        'lxml.etree',
        'lxml.html',
        'primp',
        'fake_useragent',
        'src.debug_tool',
        'src.executor',
        'src.recognize',
        'src.feedback',
        'src.nlu.phonetic_corrector',
        'src.nlu.fuzzy_regex',
        'sherpa_onnx',
        'sherpa_onnx.lib._sherpa_onnx',
        'PIL.ImageTk',
        # ===== 下面这些是 Python 标准库，需要显式声明 =====
        'wave',
        'audioop',
        'collections.abc',
        'email',
        'hashlib',
        'json',
        'logging',
        'os',
        're',
        'socket',
        'sys',
        'threading',
        'time',
        'traceback',
        'types',
        'typing',
        'warnings',
        'weakref',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='VoiceAssistant',
    icon=os.path.join(project_root, 'icon.ico'),
    version=os.path.join(project_root, 'version_info.txt'),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)



coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=False,
    name='VoiceAssistant',
)
