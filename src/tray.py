"""系统托盘图标模块。

提供一个常驻托盘图标，让用户一眼看到程序正在运行，
并通过右键菜单退出，无需打开任务管理器。
"""
import os
import sys
import threading


_icon = None


def _get_resource_root():
    """资源根目录：打包运行 -> _MEIPASS；源码运行 -> 项目根"""
    if getattr(sys, 'frozen', False):
        return getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(sys.executable)))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_image():
    """加载托盘图标（优先 icon.ico，回退生成简单色块）"""
    from PIL import Image
    root = _get_resource_root()
    for name in ('icon.ico', 'icon.png'):
        p = os.path.join(root, name)
        if os.path.exists(p):
            try:
                return Image.open(p)
            except Exception:
                continue
    return Image.new('RGB', (64, 64), (60, 60, 60))


def _on_exit(icon, item):
    """托盘菜单「退出」：停止图标并结束整个进程"""
    try:
        icon.stop()
    except Exception:
        pass
    # 主循环阻塞在录音上，直接结束进程最可靠（PyInstaller 父进程会清理临时目录）
    os._exit(0)


def _on_show(icon, item):
    """托盘菜单「状态」：弹系统通知提示"""
    try:
        from src.feedback import notify
        notify('黑猫语音助手', '正在后台运行，说「你好黑猫」唤醒')
    except Exception:
        pass


def _on_model_settings(icon, item):
    """托盘菜单「模型设置」：打开模型选择窗口"""
    try:
        from src.gui import show_model_selector
        show_model_selector()
    except Exception as e:
        print(f'[托盘] 打开模型设置失败: {e}')


def _on_show_window(icon, item):
    """托盘菜单「显示窗口」/左键点击：唤起主窗口"""
    try:
        from src.gui import show_window
        show_window()
    except Exception as e:
        print(f'[托盘] 显示窗口失败: {e}')


def _on_diagnose(icon, item):
    """托盘菜单「运行诊断」：后台启动诊断进程（--debug），报告写入 exe 旁 debug_report.txt 并打开"""
    try:
        import subprocess
        if getattr(sys, 'frozen', False):
            cmd = [sys.executable, '--debug']
        else:
            main_py = os.path.join(_get_resource_root(), 'src', 'main.py')
            cmd = [sys.executable, main_py, '--debug']
        subprocess.Popen(cmd, creationflags=0x08000000)  # CREATE_NO_WINDOW
    except Exception as e:
        print(f'[托盘] 启动诊断失败: {e}')


def start_tray():
    """启动托盘图标（后台线程），返回 icon 对象或 None"""
    global _icon
    try:
        import pystray
    except ImportError:
        return None

    try:
        image = _load_image()
        menu = pystray.Menu(
            pystray.MenuItem('黑猫语音助手（运行中）', None, enabled=False),
            pystray.MenuItem('显示窗口', _on_show_window, default=True),  # 左键单击唤起
            pystray.MenuItem('模型设置', _on_model_settings),
            pystray.MenuItem('说「你好黑猫」唤醒', _on_show),
            pystray.MenuItem('🔧 运行诊断', _on_diagnose),
            pystray.MenuItem('退出', _on_exit),
        )
        _icon = pystray.Icon('voice_assistant', image, '黑猫语音助手', menu)
        threading.Thread(target=_icon.run, daemon=True, name='tray').start()
        return _icon
    except Exception as e:
        print(f'[托盘] 启动失败: {e}')
        return None


def stop_tray():
    """停止托盘图标"""
    global _icon
    if _icon is not None:
        try:
            _icon.stop()
        except Exception:
            pass
        _icon = None
