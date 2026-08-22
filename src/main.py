import sys
import os
from pathlib import Path


def _setup_import_paths():
    """配置 sys.path，兼容源码运行与 PyInstaller 打包运行"""
    if getattr(sys, 'frozen', False):
        # 打包运行：资源解压在 _MEIPASS，项目根即 _MEIPASS
        base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(sys.executable)))
    else:
        # 源码运行：项目根为 src 的上一级目录
        base = str(Path(__file__).resolve().parent.parent)
    if base not in sys.path:
        sys.path.insert(0, base)


_setup_import_paths()


# 判断是否为 windowed（无控制台）模式：需在重定向前判断
IS_WINDOWED = getattr(sys, 'frozen', False) and (sys.stdout is None or sys.stderr is None)

if IS_WINDOWED:
    # windowed 模式下把 print 重定向到日志，避免崩溃
    _log_path = os.path.join(
        os.path.dirname(os.path.abspath(sys.executable)), 'console.log'
    )
    try:
        _log_f = open(_log_path, 'a', encoding='utf-8', buffering=1)
        if sys.stdout is None:
            sys.stdout = _log_f
        if sys.stderr is None:
            sys.stderr = _log_f
    except Exception:
        pass

# 杀空间模块：导入即把临时目录重定向到 D 盘，并提供清理函数
from src.cleaner import (
    cleanup_temp_files,
    start_periodic_cleanup,
    cleanup_now,
)


# 统一使用 src 包绝对导入，兼容源码运行与打包运行
from src.config import load_config
from src.executor import execute_intent, configure_ollama
from src.feedback import say, notify, configure_tts, warmup_tts, say_sync, wait_speaking_done, say_wake
from src.stop import begin_output, end_output, start_stop_listener, start_barge_in_listener, request_stop, is_stop_requested
from src.intents import IntentParser
from src.logger import configure_logging, get_logger
from src.plugins import PluginManager
from src.recognize import SpeechRecognizer, SherpaONNXRecognizer, check_speech_dependencies, match_phrase
from src.nlu.phonetic_corrector import PhoneticCorrector
from src.nlu.fuzzy_regex import FuzzyRegexMatcher
from src.tray import start_tray
from src.gui import load_selected_model, append_asr, append_output, set_status, start_main_window, load_settings, get_wake_words, get_tts_engine, get_tts_voice, get_sensitivity, get_stop_words, get_tts_style, get_tts_rate, get_tts_pitch

# 打断（barge-in）总开关：播放/生成期间主人开口即中断当前播报。
# 设为 False 可关闭（仅保留停止词中断）。后续可接入 settings.json 由 GUI 控制。
BARGE_IN_ENABLED = True


def create_recognizer(cfg):
    """根据配置创建语音识别器，自动检测可用引擎"""
    import os

    # 获取模型路径（支持绝对路径和相对路径）
    model_path = cfg.get('speech_model_path', '../models/sense_voice')
    if not os.path.isabs(model_path):
        model_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), model_path)

    # 优先使用 INT8/Q8 量化版本
    model_file = None
    for m in ['model.int8.onnx', 'model_q8.onnx', 'model.onnx']:
        candidate = os.path.join(model_path, m)
        if os.path.exists(candidate):
            model_file = candidate
            break

    # 查找 tokens 文件
    tokens_file = None
    for t in ['tokens.txt', 'tokens (1).txt']:
        candidate = os.path.join(model_path, t)
        if os.path.exists(candidate):
            tokens_file = candidate
            break

    # 自动检测：优先使用本地 sherpa-onnx
    if model_file and tokens_file:
        try:
            engine = cfg.get('speech_engine', 'auto')
            if engine == 'google':
                print('强制使用 Google STT（需要网络）')
                return SpeechRecognizer(language=cfg.get('language', 'zh-CN'))
            print('自动选择：sherpa-onnx 本地离线识别')
            return SherpaONNXRecognizer(
                model_path=model_path,
                energy_threshold=cfg.get('energy_threshold', 0.02),
            )
        except Exception as e:
            print(f'sherpa-onnx 初始化失败: {e}，回退到 Google STT')
            return SpeechRecognizer(language=cfg.get('language', 'zh-CN'))
    else:
        print('未检测到 sherpa-onnx 模型，自动使用 Google STT（需要网络）')
        return SpeechRecognizer(language=cfg.get('language', 'zh-CN'))


def main():
    cleanup_temp_files()
    start_periodic_cleanup()
    print("Starting main...")
    cfg = load_config()
    print("Config loaded")
    # 读取用户设置（唤醒词、灵敏度、TTS 引擎）
    load_settings()
    # 配置 TTS 引擎（含感情风格/语速/音调，均来自 GUI 设置）
    configure_tts(get_tts_engine(), get_tts_voice(),
                  get_tts_style(), get_tts_rate(), get_tts_pitch())
    warmup_tts()
    configure_ollama(load_selected_model() or cfg.get('ollama_model'))
    configure_logging(cfg.get('log_file'))
    logger = get_logger()
    print("Logging configured")

    parser = IntentParser(cfg.get('intents_path'))
    plugin_manager = PluginManager(cfg.get('plugin_path'))

    from src.executor import load_app_map
    load_app_map(cfg.get('app_map_path'))

    # 初始化增强 NLU 组件
    corrector = None
    fuzzy_matcher = None
    if cfg.get('enable_asr_correction', True):
        try:
            from src.executor import APP_MAP as _APP_MAP
            corrector = PhoneticCorrector(
                intents_path=cfg.get('intents_path'),
                app_names=list(_APP_MAP.keys())
            )
            logger.info('ASR 纠错模块初始化成功')
        except Exception as e:
            logger.warning(f'ASR 纠错模块初始化失败: {e}')

    if cfg.get('nlu_engine') == 'fuzzy_regex':
        try:
            fuzzy_matcher = FuzzyRegexMatcher(cfg.get('intents_path'))
            logger.info('FuzzyRegex 匹配器初始化成功')
        except Exception as e:
            logger.warning(f'FuzzyRegex 匹配器初始化失败: {e}')

    # 初始化唤醒词检测器（VAD）
    wake_word_detector = None
    if cfg.get('enable_wake_word_detector', True):
        try:
            from src.nlu.wake_word_detector import create_wake_word_detector
            wake_word_detector = create_wake_word_detector(cfg)
            logger.info('唤醒词检测器初始化成功')
        except Exception as e:
            logger.warning(f'唤醒词检测器初始化失败: {e}')

    # 扫描本地程序目录并更新 APP_MAP
    from src.executor import scan_all_program_folders
    try:
        scanned = scan_all_program_folders()
        logger.info(f'扫描到 {len(scanned)} 个本地应用程序')
    except Exception as e:
        logger.warning(f'扫描本地应用程序失败: {e}')
        scanned = []

    dep_ok, dep_msg = check_speech_dependencies()
    voice_available = dep_ok
    recognizer = None
    # 延迟初始化语音识别器，避免启动卡住
    # if voice_available:
    #     try:
    #         recognizer = SpeechRecognizer(language=cfg.get('language', 'zh-CN'))
    #         logger.info('语音识别模块初始化成功')
    #     except Exception as e:
    #         voice_available = False
    #         logger.warning(f'语音识别初始化失败: {e}')

    if not voice_available:
        logger.warning('语音识别不可用: ' + dep_msg)

    logger.info('语音助手启动完成')
    print('语音模块检测:', dep_msg)

    # 启动时直接初始化语音识别器
    if voice_available:
        try:
            recognizer = create_recognizer(cfg)
            logger.info('语音识别模块初始化成功')
            print('语音识别初始化成功')
        except Exception as e:
            logger.warning(f'语音识别初始化失败: {e}')
            print(f'语音识别初始化失败，回退到 Google STT')
            try:
                from src.recognize import SpeechRecognizer
                recognizer = SpeechRecognizer(language=cfg.get('language', 'zh-CN'))
                logger.info('Google STT 初始化成功')
            except Exception as e2:
                logger.warning(f'Google STT 初始化失败: {e2}')
                recognizer = None

    # 直接进入语音模式
    print('欢迎使用 Windows 交互语音助手（输入”退出”结束）')
    print('>>> 语音模式已启用 <<<')
    use_voice = True

    # 启动系统托盘图标（显示运行状态，右键可退出）
    start_tray()

    # 启动主窗口（展示识别内容和输出内容）
    start_main_window()
    set_status('● 监听中，说「你好黑猫」唤醒')

    while True:
        try:
            if use_voice:
                # 等待上一轮播报结束，避免助手自己的声音（回声）被麦克风录入
                wait_speaking_done()

                if not voice_available or recognizer is None:
                    logger.warning('语音识别功能不可用')
                    say('语音识别不可用')
                    if IS_WINDOWED:
                        # 无控制台无法文本输入，稍作等待后重试语音初始化
                        import time as _time
                        _time.sleep(5)
                        continue
                    use_voice = False
                    continue

                # 优先使用专用唤醒词检测器（VAD）
                if wake_word_detector is not None:
                    ok, _ = recognizer.listen_await_wake_word(wake_word_detector)
                    if ok:
                        # VAD 检测到语音，唤醒成功，继续监听实际指令
                        say('我在')
                        # 等"我在"播完再录音，避免把助手自己的声音录进去（回声）
                        wait_speaking_done()
                        ok, query = recognizer.listen_once()
                        if not ok:
                            logger.warning(f'语音识别失败: {query}')
                            continue
                    else:
                        # 唤醒词检测超时，继续循环等待
                        continue
                else:
                    # 持续监听：听到唤醒词才反应（唤醒瞬间播报"我在"）
                    ok, query = recognizer.listen_with_kws(
                        get_wake_words(),
                        on_wake=lambda: (say_wake(), set_status('● 已唤醒，请说指令')),
                        sensitivity=get_sensitivity(),
                    )
                    if not ok:
                        logger.warning(f'语音识别失败: {query}')
                        continue
                print('识别内容：', query)
                append_asr(query)
            else:
                if IS_WINDOWED:
                    # 无控制台无法文本输入，强制回到语音监听
                    use_voice = True
                    continue
                query = input('请输入指令：').strip()

            if not query:
                continue

            if query in ('0', '1'):
                use_voice = (query == '1')
                if use_voice and voice_available and recognizer is None:
                    try:
                        recognizer = create_recognizer(cfg)
                        logger.info(f'语音识别模块初始化成功 (引擎: {cfg.get("speech_engine", "google")})')
                    except Exception as e:
                        voice_available = False
                        logger.warning(f'语音识别初始化失败: {e}')
                        say('语音识别初始化失败，请使用文本输入')
                        use_voice = False
                        continue
                logger.info(f'输入模式切换为: {"语音" if use_voice else "文本"}')
                say('已切换为' + ('语音' if use_voice else '文本') + '模式')
                continue

            if query in ('退出', '关闭', '拜拜'):
                say('再见')
                break

            # 停止词：重新监听（不退出程序，也不执行指令）
            hit_stop, sw = match_phrase(query, get_stop_words())
            if hit_stop:
                logger.info(f'命中停止词 [{sw}]，重新监听')
                continue

            # ASR 纠错
            if corrector:
                corrected = corrector.correct(query)
                if corrected != query:
                    logger.info(f'ASR纠错: {query} -> {corrected}')
                    query = corrected

            # 模糊匹配优先（如果启用）
            if fuzzy_matcher:
                intent_name, slots = fuzzy_matcher.match(query)
                if intent_name == 'open_app':
                    # 防止模糊匹配把系统面板误识别为应用
                    from src.executor import SYSTEM_PANEL_COMMANDS
                    app_name = slots.get('app_name', '')
                    if app_name in SYSTEM_PANEL_COMMANDS:
                        intent_name = 'open_system_panel'
                        slots = {'panel': app_name}
                        logger.info(f' FuzzyRegex矫正: {query} => intent={intent_name}, slots={slots}')
                    else:
                        logger.info(f'FuzzyRegex解析: {query} => intent={intent_name}, slots={slots}')
                elif intent_name != 'unknown':
                    logger.info(f'FuzzyRegex解析: {query} => intent={intent_name}, slots={slots}')
                else:
                    intent_name, slots = parser.parse(query)
                    logger.info(f'解析: {query} => intent={intent_name}, slots={slots}')
            else:
                intent_name, slots = parser.parse(query)
                logger.info(f'解析: {query} => intent={intent_name}, slots={slots}')

            plugin_ok, plugin_result = plugin_manager.try_execute(intent_name, slots)
            if plugin_ok:
                text = plugin_result
                logger.info(f'插件命令执行: {text}')
                say(text)
                notify('语音助手', text)
                continue

            if intent_name == 'unknown':
                # AI 生成：启动停止监听（语音说出停止词或按 Esc 键停止）
                # 监听覆盖"生成 + 播报"整个阶段，实现播放中可打断（barge-in）
                begin_output()
                start_stop_listener(use_voice, recognizer, get_stop_words())
                if BARGE_IN_ENABLED:
                    start_barge_in_listener(recognizer)
                result = execute_intent(intent_name, slots, query)
                # 等 TTS 队列播报完再结束输出，确保播放期间停止词/开口打断始终有效
                try:
                    from src.feedback import wait_speaking_done
                    wait_speaking_done()
                except Exception:
                    pass
                end_output()
            else:
                result = execute_intent(intent_name, slots, query)
            # 兼容返回格式：(success, message) 或 (success, message, streamed)
            if len(result) == 3:
                success, message, streamed = result
            else:
                success, message = result
                streamed = False
            if not success:
                logger.warning(message)
                append_output('执行失败：' + message)
                say('执行失败:' + message)
            else:
                logger.info(message)
                append_output(message)
                # 若 executor 已流式按句播报过，则不再整段念一遍，避免重复
                if not streamed:
                    say(message)
            notify('语音助手', message)
            set_status('● 监听中，说「你好黑猫」唤醒')

        except KeyboardInterrupt:
            say('已退出')
            break
        except Exception as e:
            logger.error(f'运行时异常: {e}')
            say('发生错误:' + str(e))

if __name__ == '__main__':
    if '--debug' in sys.argv:
        # 诊断模式：运行独立诊断工具并退出，不启动语音助手
        try:
            from src.debug_tool import run_diagnostics
            run_diagnostics(cli=True)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            print('[诊断] 运行失败:', exc)
        sys.exit(0)
    main()
