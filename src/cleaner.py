"""磁盘空间清理模块（杀空间）。

负责把临时文件重定向到应用所在盘（D 盘），并在后台定期清理
PyInstaller 单文件 exe 的解压残留与临时音频。全程不阻塞主线程、
不误删正在使用的文件，从而与语音监听无缝衔接，保证没有漏洞。
"""
import os
import sys
import glob
import shutil
import threading
import time
from pathlib import Path


_SYSTEM_TEMP = None    # 重定向前保存的系统临时目录（通常是 C 盘）
_TEMP_DIR = None       # 重定向后的应用临时目录（D 盘）
_active_files = set()  # 正在使用、禁止删除的文件路径
_active_lock = threading.Lock()


def get_app_root():
    """返回可写目录：打包运行 -> exe 所在目录；源码运行 -> 项目根"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return str(Path(__file__).resolve().parent.parent)


def setup_temp_dir():
    """把临时文件目录重定向到应用所在盘（D 盘），返回该目录路径。"""
    global _SYSTEM_TEMP, _TEMP_DIR
    try:
        _SYSTEM_TEMP = os.environ.get('TEMP') or os.environ.get('TMP')
    except Exception:
        _SYSTEM_TEMP = None
    if not _SYSTEM_TEMP:
        try:
            import tempfile
            _SYSTEM_TEMP = tempfile.gettempdir()
        except Exception:
            _SYSTEM_TEMP = None

    temp_dir = os.path.join(get_app_root(), 'temp')
    try:
        os.makedirs(temp_dir, exist_ok=True)
        if not os.access(temp_dir, os.W_OK):
            raise OSError('目录不可写')
    except Exception:
        _TEMP_DIR = _SYSTEM_TEMP  # 回退到系统临时目录
        return _TEMP_DIR

    os.environ['TMP'] = temp_dir
    os.environ['TEMP'] = temp_dir
    try:
        import tempfile
        tempfile.tempdir = temp_dir  # 让 tempfile 库也使用 D 盘目录
    except Exception:
        pass
    _TEMP_DIR = temp_dir
    return _TEMP_DIR


def register_active_file(path):
    """标记一个临时文件正在使用，清理时跳过它。"""
    if not path:
        return
    try:
        with _active_lock:
            _active_files.add(os.path.realpath(path))
    except Exception:
        pass


def unregister_active_file(path):
    """解除文件的使用标记。"""
    if not path:
        return
    try:
        with _active_lock:
            _active_files.discard(os.path.realpath(path))
    except Exception:
        pass


def _is_active(path):
    try:
        real = os.path.realpath(path)
    except Exception:
        return False
    with _active_lock:
        return real in _active_files


def _dir_size(path):
    """计算目录总大小（字节），失败返回 0。"""
    try:
        total = 0
        for dirpath, _dirs, filenames in os.walk(path):
            for fn in filenames:
                fp = os.path.join(dirpath, fn)
                try:
                    total += os.path.getsize(fp)
                except Exception:
                    pass
        return total
    except Exception:
        return 0


def cleanup_temp_files():
    """清理残留的临时文件，返回 (清理数量, 释放字节数)。

    只清理明确的临时内容，跳过当前进程正在使用的 _MEI 目录和
    标记为活跃（正在播放）的文件，保证与录音/播放/监听互不干扰。
    """
    current = getattr(sys, '_MEIPASS', None)
    if current:
        current = os.path.realpath(current).lower()

    targets = set()
    for root in (_TEMP_DIR, _SYSTEM_TEMP):
        if root and os.path.isdir(root):
            targets.add(os.path.realpath(root))

    cleaned = 0
    freed = 0
    for root in targets:
        # 1) PyInstaller 单文件 exe 的解压残留 _MEI*
        for d in glob.glob(os.path.join(root, '_MEI*')):
            try:
                if current and os.path.realpath(d).lower() == current:
                    continue  # 跳过当前进程正在使用的目录
                if not os.path.isdir(d):
                    continue
                freed += _dir_size(d)
                shutil.rmtree(d, ignore_errors=True)
                cleaned += 1
            except Exception:
                continue
        # 2) TTS 临时音频等（跳过正在播放的文件）
        for f in glob.glob(os.path.join(root, 'assistant_tts*.mp3')):
            try:
                if _is_active(f):
                    continue
                freed += os.path.getsize(f)
                os.remove(f)
                cleaned += 1
            except Exception:
                continue
    return cleaned, freed


def cleanup_now():
    """立即清理一次（供"清理空间"语音指令调用）。"""
    return cleanup_temp_files()


def start_periodic_cleanup(interval_seconds=1800):
    """后台定时清理，不阻塞主线程（与监听无缝衔接）。"""
    def _worker():
        while True:
            time.sleep(interval_seconds)
            try:
                cleanup_temp_files()
            except Exception:
                pass

    try:
        threading.Thread(target=_worker, daemon=True, name='temp-cleaner').start()
        return True
    except Exception:
        return False


# 模块导入时立即把临时目录重定向到应用所在盘（D 盘）
_TEMP_DIR = setup_temp_dir()
