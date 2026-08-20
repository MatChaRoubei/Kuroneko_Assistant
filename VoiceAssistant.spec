# -*- mode: python ; coding: utf-8 -*-

import os

# SPECPATH 为 spec 文件所在目录（项目根）
project_root = SPECPATH

# 单文件 exe 运行时解压目录：放到 exe 旁边的 temp 目录（D 盘），
# 避免每次运行都往 C 盘系统临时目录解压约 0.5GB 资源
runtime_tmpdir = os.path.join(SPECPATH, 'dist', 'temp')
os.makedirs(runtime_tmpdir, exist_ok=True)

a = Analysis(
    [os.path.join(project_root, 'src', 'main.py')],
    pathex=[project_root],
    binaries=[],
    datas=[
        (os.path.join(project_root, 'icon.ico'), '.'),
        (os.path.join(project_root, 'firstUI.png'), '.'),
        (os.path.join(project_root, 'config'), 'config'),
        (os.path.join(project_root, 'data'), 'data'),
        (os.path.join(project_root, 'plugins'), 'plugins'),
        # 只打包本地离线识别模型必需的文件，排除测试音频
        (os.path.join(project_root, 'models', 'sense_voice', 'model.int8.onnx'), os.path.join('models', 'sense_voice')),
        (os.path.join(project_root, 'models', 'sense_voice', 'tokens.txt'), os.path.join('models', 'sense_voice')),
        # 本地神经语音（TTS）模型，离线回退用
        (os.path.join(project_root, 'models', 'vits-melo-tts-zh_en'), os.path.join('models', 'vits-melo-tts-zh_en')),
    ],
    hiddenimports=[
        'numpy',
        'sounddevice',
        'SpeechRecognition',
        'pyyaml',
        'win10toast',
        'pyttsx3',
        'pyttsx3.drivers',
        'pyttsx3.drivers.sapi5',
        'pyttsx3.drivers.nsss',
        'pyttsx3.drivers.espeak',
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
        'aiohttp',
        'ddgs',
        'lxml',
        'lxml.etree',
        'lxml.html',
        'primp',
        'fake_useragent',
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
    a.binaries,
    a.datas,
    [],
    name='VoiceAssistant',
    icon=os.path.join(project_root, 'icon.ico'),
    version=os.path.join(project_root, 'version_info.txt'),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=runtime_tmpdir,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)


