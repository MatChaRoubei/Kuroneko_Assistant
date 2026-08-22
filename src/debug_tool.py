"""黑猫语音助手 - 独立诊断工具 (debug_tool.py)

扫描项目运行所需的全部软/硬件条件，输出一份可读的健康报告。
可用于：打包后无法启动、语音识别/TTS/AI 对话异常、依赖缺失等排障。

使用方式：
  - 源码运行：  python src/debug_tool.py
  - 打包后运行：VoiceAssistant.exe --debug
    （main.py 检测到 --debug 会直接调用 run_diagnostics 并退出）
  - 托盘菜单：  「🔧 运行诊断」会以后台方式启动上述 --debug 进程

无控制台(windowed)运行时，报告会写入 exe 旁的 debug_report.txt 并尝试用
默认程序打开。
"""
import sys
import os
import json
import re
import platform
import importlib
import urllib.request
import urllib.error
from pathlib import Path

# 兼容源码运行与打包运行：确保项目根在 sys.path（与 main.py 一致）
if getattr(sys, 'frozen', False):
    _ROOT = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(sys.executable)))
else:
    _ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

STATUS = {'OK': '[OK]', 'WARN': '[WARN]', 'FAIL': '[FAIL]', 'INFO': '[INFO]'}

_ollama_host = os.environ.get('OLLAMA_HOST', 'http://localhost:11434').rstrip('/')


def _now():
    try:
        import datetime
        return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        return ''


def _get_resource_root():
    try:
        from src.config import get_resource_root
        return get_resource_root()
    except Exception:
        return _ROOT


def _dep_import(name):
    try:
        importlib.import_module(name)
        return True, ''
    except Exception as e:
        return False, str(e)[:140]


# --------------------------------------------------------------------------
# 各检查项
# --------------------------------------------------------------------------
def check_environment():
    rows = []
    ver = sys.version.split()
    rows.append(('Python 版本', 'INFO', f'{ver[0]} ({ver[1]})'))
    rows.append(('运行平台', 'INFO', f'{sys.platform} / {platform.version()}'))
    rows.append(('可执行文件', 'INFO', sys.executable))
    rows.append(('工作目录', 'INFO', os.getcwd()))
    rows.append(('是否打包(frozen)', 'INFO', '是' if getattr(sys, 'frozen', False) else '否(源码)'))
    rows.append(('资源根目录', 'INFO', _get_resource_root()))
    return '运行环境', rows


def check_dependencies():
    rows = []
    deps = [
        ('numpy', '核心数值计算'),
        ('yaml', '配置解析(PyYAML)'),
        ('sounddevice', '音频采集/播放'),
        ('SpeechRecognition', 'Google STT 回退(可选)'),
        ('pyttsx3', '机械音 TTS 兜底'),
        ('win10toast', '系统通知'),
        ('ollama', '本地大模型对话'),
        ('edge_tts', '在线神经语音'),
        ('pystray', '系统托盘'),
        ('pyautogui', '键鼠自动化(抖音)'),
        ('jieba', '中文分词(NLU)'),
        ('pypinyin', '拼音匹配(唤醒)'),
        ('rapidfuzz', '模糊匹配'),
        ('pycaw', '系统音量控制'),
        ('comtypes', 'Windows COM'),
        ('PIL', '图像处理(托盘图标)'),
        ('ddgs', '联网搜索(DuckDuckGo)'),
        ('fake_useragent', '搜索 UA'),
        ('sentencepiece', 'SenseVoice 依赖'),
        ('sherpa_onnx', '本地离线识别/TTS'),
        ('lxml', '搜索结果解析'),
        ('aiohttp', '异步网络'),
        ('tkinter', 'GUI'),
    ]
    missing = []
    for name, desc in deps:
        ok, err = _dep_import(name)
        if ok:
            rows.append((f'{name}（{desc}）', 'OK', '已安装'))
        else:
            rows.append((f'{name}（{desc}）', 'WARN', '缺失: ' + err))
            missing.append(name)
    if missing:
        rows.append(('缺失依赖汇总', 'WARN',
                     '、'.join(missing) + ' —— 部分功能将降级或不可用'))
    else:
        rows.append(('依赖完整性', 'OK', '全部关键依赖已安装'))
    return '依赖检查', rows


def check_config():
    rows = []
    try:
        from src.config import load_config, get_resource_root
        cfg = load_config()
        rows.append(('配置加载', 'OK', '成功'))
        yaml_path = os.path.join(get_resource_root(), 'config', 'config.yaml')
        rows.append(('config.yaml 存在', 'OK' if os.path.exists(yaml_path) else 'WARN', yaml_path))
        info_keys = ('wake_words', 'language', 'tts_engine', 'speech_engine',
                     'ollama_model')
        for key in info_keys:
            if key in cfg:
                val = cfg[key]
                if key == 'wake_words':
                    val = f'{len(val)} 个: {val}'
                rows.append((f'配置项 {key}', 'INFO', str(val)[:200]))
            else:
                rows.append((f'配置项 {key}', 'WARN', '未设置(使用默认值)'))
        for key in ('intents_path', 'app_map_path'):
            p = cfg.get(key)
            if p:
                rows.append((f'路径存在 {key}', 'OK' if os.path.exists(p) else 'FAIL', p))
        lf = cfg.get('log_file')
        if lf:
            d = os.path.dirname(lf) or '.'
            rows.append(('日志目录可写', 'OK' if os.access(d, os.W_OK) else 'WARN', d))
    except Exception as e:
        rows.append(('配置加载', 'FAIL', str(e)[:240]))
    return '配置检查', rows


def _find_model_files():
    rr = _get_resource_root()
    sense = os.path.join(rr, 'models', 'sense_voice')
    vits = os.path.join(rr, 'models', 'vits-melo-tts-zh_en')
    kws = os.path.join(rr, 'models', 'kws')
    sense_model = None
    for m in ('model.int8.onnx', 'model_q8.onnx', 'model.onnx'):
        if os.path.exists(os.path.join(sense, m)):
            sense_model = m
            break
    sense_tokens = os.path.exists(os.path.join(sense, 'tokens.txt'))
    vits_model = os.path.exists(os.path.join(vits, 'model.onnx'))
    vits_tokens = os.path.exists(os.path.join(vits, 'tokens.txt'))
    return {
        'sense': (sense_model, sense_tokens, sense),
        'vits': (vits_model, vits_tokens, vits),
        'kws': os.path.isdir(kws),
    }


def check_models():
    rows = []
    res = _find_model_files()
    sm, st, sp = res['sense']
    if sm and st:
        rows.append(('本地识别模型 (SenseVoice)', 'OK', f'{sm} + tokens.txt @ {sp}'))
    else:
        rows.append(('本地识别模型 (SenseVoice)', 'WARN',
                     f'不完整(model={sm}, tokens={st}) @ {sp} —— 将回退 Google STT(需联网)'))
    vm, vt, vp = res['vits']
    if vm and vt:
        rows.append(('本地神经语音 (VITS Melo)', 'OK', f'model.onnx + tokens.txt @ {vp}'))
    elif vm or vt:
        rows.append(('本地神经语音 (VITS Melo)', 'WARN',
                     f'不完整(model={vm}, tokens={vt}) @ {vp} —— 离线语音降级'))
    else:
        rows.append(('本地神经语音 (VITS Melo)', 'WARN',
                     f'未找到 @ {vp} —— 离线无神经语音'))
    rows.append(('KWS 唤醒词模型', 'INFO', '存在' if res['kws'] else '未配置(使用 STT+拼音回退)'))
    return '模型文件检查', rows


def check_audio():
    rows = []
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        inputs = [d for d in devices if (d.get('max_input_channels') or 0) > 0]
        rows.append(('音频设备总数', 'INFO', str(len(devices))))
        rows.append(('输入设备(麦克风)数', 'OK' if inputs else 'FAIL', str(len(inputs))))
        try:
            dev_id = sd.default.device
            if isinstance(dev_id, (list, tuple)):
                dev_id = dev_id[0]
            dev = sd.query_devices(dev_id)
            rows.append(('默认输入设备', 'INFO',
                         f"{dev['name']} @ {int(dev['default_samplerate'])}Hz"))
        except Exception as e:
            rows.append(('默认输入设备', 'WARN', f'查询失败: {e}'))
        if not inputs:
            rows.append(('麦克风', 'FAIL', '未发现任何输入设备，语音识别不可用'))
    except Exception as e:
        rows.append(('音频模块', 'FAIL', f'sounddevice 不可用: {e}'))
    return '音频/麦克风检查', rows


def check_ollama():
    rows = []
    ok, err = _dep_import('ollama')
    if not ok:
        rows.append(('ollama 库', 'WARN', f'未安装: {err} —— AI 对话不可用'))
        return 'Ollama 检查', rows
    rows.append(('ollama 库', 'OK', '已安装'))
    try:
        req = urllib.request.Request(_ollama_host + '/api/tags')
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        models = [m['name'] for m in data.get('models', [])]
        rows.append(('Ollama 服务', 'OK', f'已连接，{len(models)} 个模型'))
        for n in models:
            rows.append(('  模型', 'INFO', n))
        try:
            from src.config import load_config
            configured = load_config().get('ollama_model') or ''
            if configured:
                rows.append(('配置模型存在', 'OK' if configured in models else 'WARN', configured))
            else:
                rows.append(('配置模型', 'INFO', '未指定，将自动检测'))
        except Exception:
            pass
        toolish = [n for n in models if any(k in n.lower() for k in
                  ('qwen', 'deepseek', 'llama3', 'llama-3', 'mistral', 'gemma', 'phi'))]
        if toolish:
            rows.append(('建议工具调用模型', 'INFO', '、'.join(toolish[:6])))
    except urllib.error.URLError:
        rows.append(('Ollama 服务', 'WARN',
                     f'未运行或不可达({_ollama_host})：AI 对话不可用，本地指令不受影响'))
    except Exception as e:
        rows.append(('Ollama 查询', 'WARN', f'查询失败: {e}'))
    return 'Ollama 检查', rows


def check_tts():
    rows = []
    ok, err = _dep_import('edge_tts')
    rows.append(('Edge 在线语音', 'OK' if ok else 'WARN',
                 '可用(需联网)' if ok else '缺失: ' + err))
    ok2, err2 = _dep_import('sherpa_onnx')
    res = _find_model_files()
    vm, vt, _ = res['vits']
    if ok2 and vm and vt:
        rows.append(('VITS 本地神经语音', 'OK', '引擎+模型齐全(离线可用)'))
    elif ok2:
        rows.append(('VITS 本地神经语音', 'WARN', '引擎在但模型缺失'))
    else:
        rows.append(('VITS 本地神经语音', 'WARN', '引擎缺失: ' + err2))
    ok3, err3 = _dep_import('pyttsx3')
    if ok3:
        try:
            import pyttsx3
            pyttsx3.init()
            rows.append(('pyttsx3 机械音', 'OK', '初始化成功(离线兜底)'))
        except Exception as ex:
            rows.append(('pyttsx3 机械音', 'WARN', f'初始化失败: {ex}'))
    else:
        rows.append(('pyttsx3 机械音', 'WARN', '缺失: ' + err3))
    if sys.platform == 'win32':
        rows.append(('Windows SAPI', 'OK', '可用(零依赖兜底)'))
    else:
        rows.append(('Windows SAPI', 'INFO', '非 Windows，不可用'))
    return 'TTS 语音引擎检查', rows


def check_intents():
    rows = []
    expect_map = {
        '打开记事本': 'open_app',
        '设置音量50': 'set_volume',
        '搜索一下今天天气': 'web_search',
        '清理空间': 'cleanup_space',
        '记住我喜欢喝咖啡': 'remember',
        '忘记我喜欢喝咖啡': 'forget',
        '关机': 'systemShutdown',
        '你好黑猫': 'unknown',
    }
    try:
        from src.intents import IntentParser
        from src.config import load_config
        p = IntentParser(load_config().get('intents_path'))
        for q, expect in expect_map.items():
            name, slots = p.parse(q)
            ok = (name == expect)
            status = 'OK' if ok else 'WARN'
            detail = f'-> {name}  slots={slots}' + ('' if ok else f' (期望 {expect})')
            rows.append((f'解析「{q}」', status, detail))
    except Exception as e:
        rows.append(('意图解析', 'FAIL', str(e)[:240]))
    return '意图解析自测', rows


def check_modules():
    rows = []
    mods = [
        'src.config', 'src.logger', 'src.intents', 'src.plugins', 'src.cleaner',
        'src.stop', 'src.feedback', 'src.recognize', 'src.tray', 'src.gui',
        'src.executor', 'src.main',
        'src.nlu.phonetic_corrector', 'src.nlu.fuzzy_regex', 'src.nlu.rules',
        'src.nlu.wake_word_detector', 'src.nlu.douyin_controller',
    ]
    for m in mods:
        ok, err = _dep_import(m)
        rows.append((f'导入 {m}', 'OK' if ok else 'FAIL',
                     '成功' if ok else '失败: ' + err))
    return '模块导入自检', rows


def check_memory():
    rows = []
    rr = _get_resource_root()
    for f in ('chat_memory.json', 'long_term_memory.json'):
        p = os.path.join(rr, f)
        if os.path.exists(p):
            try:
                with open(p, encoding='utf-8') as fh:
                    data = json.load(fh)
                n = len(data) if isinstance(data, (list, dict)) else '?'
                rows.append((f, 'INFO', f'存在，约 {os.path.getsize(p)} 字节，条目 {n}'))
            except Exception as e:
                rows.append((f, 'WARN', f'存在但解析失败: {e}'))
        else:
            rows.append((f, 'INFO', '不存在(首次运行后创建)'))
    return '记忆文件检查', rows


def check_version():
    rows = []
    p = os.path.join(_ROOT, 'version_info.txt')
    if os.path.exists(p):
        try:
            txt = open(p, encoding='utf-8').read()
            fv = re.search(r"FileVersion',\s*'([^']+)'", txt)
            pv = re.search(r"ProductVersion',\s*'([^']+)'", txt)
            rows.append(('文件版本 FileVersion', 'INFO', fv.group(1) if fv else '?'))
            rows.append(('产品版本 ProductVersion', 'INFO', pv.group(1) if pv else '?'))
        except Exception as e:
            rows.append(('版本信息', 'WARN', str(e)))
    else:
        rows.append(('version_info.txt', 'WARN', '未找到'))
    return '版本信息', rows


# --------------------------------------------------------------------------
# 汇总与输出
# --------------------------------------------------------------------------
def run_diagnostics(cli=False, write_file=True):
    sections = [
        check_environment(),
        check_dependencies(),
        check_config(),
        check_models(),
        check_audio(),
        check_ollama(),
        check_tts(),
        check_intents(),
        check_modules(),
        check_memory(),
        check_version(),
    ]
    lines = []
    lines.append('=' * 64)
    lines.append('黑猫语音助手 - 诊断报告')
    lines.append('生成时间: ' + _now())
    lines.append('=' * 64)
    fail = warn = 0
    for title, rows in sections:
        lines.append('')
        lines.append(f'## {title}')
        lines.append('-' * 48)
        for label, st, detail in rows:
            lines.append(f'{STATUS.get(st, st):8} {label}: {detail}')
            if st == 'FAIL':
                fail += 1
            elif st == 'WARN':
                warn += 1
    lines.append('')
    lines.append('-' * 48)
    lines.append(f'汇总: FAIL={fail}  WARN={warn}')
    lines.append('=' * 64)
    text = '\n'.join(lines)

    console_available = not (getattr(sys, 'frozen', False) and sys.stdout is None)
    if cli or console_available:
        try:
            print(text)
        except Exception:
            pass
    if write_file:
        _write_report(text)
    return text


def _write_report(text):
    try:
        if getattr(sys, 'frozen', False):
            out = os.path.join(os.path.dirname(os.path.abspath(sys.executable)),
                               'debug_report.txt')
        else:
            out = os.path.join(os.getcwd(), 'debug_report.txt')
        with open(out, 'w', encoding='utf-8') as f:
            f.write(text)
        if sys.platform == 'win32':
            try:
                os.startfile(out)
            except Exception:
                pass
        try:
            print(f'[诊断] 报告已写入: {out}')
        except Exception:
            pass
    except Exception as e:
        try:
            print(f'[诊断] 写入报告失败: {e}')
        except Exception:
            pass


if __name__ == '__main__':
    run_diagnostics(cli=True)
