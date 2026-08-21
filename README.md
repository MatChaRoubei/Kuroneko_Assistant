# 黑猫语音助手

一个常驻后台的 Windows 语音助手：持续监听唤醒词，听到「你好黑猫」后即可下达指令。支持语音控制 Windows、AI 大模型对话（Ollama）、磁盘清理等，带图形主窗口和系统托盘图标。

## 下载

预编译的单文件版本（含全部模型依赖，开箱即用）：

- **v0.0.3（当前版本）**：[VoiceAssistant.exe（约 455 MB，实际 477 MB）](https://github.com/MatChaRoubei/Kuroneko_Assistant/releases/download/v0.0.3/VoiceAssistant.exe)
  - 发布页（含变更说明）：<https://github.com/MatChaRoubei/Kuroneko_Assistant/releases/tag/v0.0.3>
  - 唤醒词稳健性修复 + 新增「停止词」，详见文末「v0.0.3」章节。
- **v0.0.2 性能优化与重构版**：[VoiceAssistant.exe（约 455 MB）](https://github.com/MatChaRoubei/Kuroneko_Assistant/releases/download/v0.0.2/VoiceAssistant.exe)
  - 发布页（含变更说明）：<https://github.com/MatChaRoubei/Kuroneko_Assistant/releases/tag/v0.0.2>
  - 该版本已做语音识别/播报/执行链路的性能优化与代码重构，详见文末「性能优化与重构」章节。

> 说明：打包产物体积较大（含 SenseVoice + VITS 等模型），不纳入 Git 仓库，仅作为 GitHub Release 附件分发。若要自行构建，见下文「打包」章节。

## 功能特性

### 唤醒与识别
- **持续监听唤醒词** — 滑动窗口持续识别，无需按键，高噪音环境下也能工作
- **本地离线识别** — Sherpa-onnx + SenseVoice，无需网络，自动检测可用引擎
- **ASR 纠错** — 自动修正语音识别错误（如"打一微信"→"打开微信"）
- **中文数字识别** — 支持"百分之五十"、"八十"等中文数字转阿拉伯数字

### AI 对话
- 接入 **Ollama** 本地大模型（如 qwen2.5-7b、deepseek-r1）
- 自动检测已部署的模型，也可在界面手动选择
- 支持工具调用（打开应用、查询时间、设置音量、搜索等）

### 界面与交互
- **图形主窗口** — 实时展示识别到的内容和助手输出
- **系统托盘图标** — 一眼看到运行状态，右键可退出
- **可视化设置面板** — 唤醒词、灵敏度、语音引擎、声音角色、大模型均可图形化配置

### 应用与系统控制
- 打开/关闭应用，自动扫描已安装程序
- 系统面板：设置、控制面板、任务管理器、网络/蓝牙/声音/显示等
- 系统操作：关机、重启、锁屏、睡眠、音量调节
- 定时提醒、文件保存

### 磁盘清理
- 临时文件自动重定向到应用所在盘，后台定时清理
- 说「清理空间 / 清理垃圾」可手动清理，不影响正在运行的监听

### 其它
- TTS 语音播报（本地 VITS / edge 在线神经语音 / 机械音兜底）
- Windows 通知
- 跨应用键盘自动化（抖音控制等）

## 支持的指令

| 指令示例 | 说明 |
|---------|------|
| 打开 记事本 | 打开记事本 |
| 关闭 计算器 | 关闭计算器 |
| 设置 音量 50 | 设置音量为 50% |
| 定时 5 分钟 | 设置 5 分钟定时提醒 |
| 存入文件夹 C:\test 内容 测试文本 | 保存文本到文件夹 |
| 列出应用 | 显示已注册应用列表 |
| 检查应用 VSCode | 查询应用是否已安装 |
| 打开设置 | 打开 Windows 设置面板 |
| 打开控制面板 | 打开控制面板 |
| 任务管理器 | 打开任务管理器 |
| 网络设置 | 打开网络设置 |
| 蓝牙设置 | 打开蓝牙设置 |
| 声音设置 | 打开声音设置 |
| 显示设置 | 打开显示设置 |
| 电源设置 | 打开电源设置 |
| 个性化设置 | 打开个性化/主题设置 |
| 应用设置 | 打开应用设置 |
| 任务栏设置 | 打开任务栏设置 |
| 投影设置 | 打开投影设置 |
| 截图 / 截屏 | 打开截图工具 |
| 清空回收站 | 清空回收站 |
| 打开浏览器 | 使用 Edge 打开浏览器 |
| 关机 | 系统 30 秒后关机 |
| 重启 | 系统 30 秒后重启 |
| 息屏 / 锁屏 | 锁定屏幕 |
| 休眠 / 睡眠 | 系统进入睡眠模式 |
| 抖音点赞 | 点赞/取消点赞 |
| 抖音收藏 | 收藏/取消收藏 |
| 抖音关注 | 关注/取消关注 |
| 抖音评论 | 评论 |
| 抖音全屏 | 全屏 |
| 抖音小窗 | 小窗模式 |
| 抖音暂停 | 暂停/播放 |
| 抖音快进 | 快进 |
| 抖音快退 | 快退 |
| 抖音上下滑 | 上下翻页 |
| 抖音弹幕 | 开启/关闭弹幕 |
| 抖音清屏 | 清屏 |
| 抖音自动连播 | 自动连播 |
| 抖音网页全屏 | 网页内全屏 |
| 抖音稍后再看 | 稍后再看 |
| 抖音不感兴趣 | 不感兴趣 |
| 抖音相关推荐 | 相关推荐 |
| 抖音作者主页 | 进入作者主页 |
| 抖音复制口令 | 复制分享口令 |
| 抖音音量加/减 | 音量调节 |
| 清理空间 / 清理垃圾 | 清理临时文件，释放磁盘空间 |
| 直接提问（如"介绍一下你自己"） | AI 对话（Ollama 大模型回答） |
| 搜索网页 | 搜索一下... |
| 退出 | 退出程序 |

## 环境要求

- Windows 10/11
- 麦克风（用于语音模式）
- Python 3.10+（仅源码运行需要）
- [Ollama](https://ollama.com)（可选，用于 AI 对话，需提前部署一个模型）

## 安装依赖（源码运行）

```powershell
# 基础依赖
pip install sounddevice SpeechRecognition pyyaml win10toast pyttsx3

# 本地语音识别与语音合成
pip install sherpa-onnx

# AI 对话 / 搜索 / 在线语音 / 界面
pip install ollama edge-tts pystray pillow ddgs fake_useragent

# 增强依赖
pip install pypinyin rapidfuzz pyautogui jieba pycaw
```

### 下载语音模型（本地识别必需）

**一键下载（推荐）**：

```powershell
python setup_models.py
```

自动下载并放置 SenseVoice（语音识别）和 VITS Melo TTS（语音合成）两个模型，已存在会自动跳过。

**手动下载**：

如需本地离线语音识别，需下载 sherpa-onnx 官方的 SenseVoice 模型：

1. 下载地址：https://github.com/k2-fsa/sherpa-onnx/releases
2. 寻找：`sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-*.tar.bz2`
3. 解压后得到 `model.int8.onnx` 和 `tokens.txt`
4. 将这两个文件放到 `models/sense_voice/` 目录

程序会自动检测模型是否可用：
- 有模型 → 使用 Sherpa-onnx 本地离线识别
- 无模型 → 自动切换到 Google STT（需要网络）

> **注意**：项目路径不能包含中文/非 ASCII 字符（如 `C:\我的项目`），否则 sherpa-onnx 无法加载模型。

## 运行

### 方式一：直接运行打包好的 exe（推荐）

双击 `dist\VoiceAssistant.exe`，会弹出图形主窗口并常驻系统托盘。

### 方式二：源码运行

```powershell
python src/main.py
```

## 使用方式

### 语音模式（默认）
程序启动后持续监听唤醒词，说 **唤醒词 + 指令** 即可，如：
- `你好黑猫打开记事本`
- `黑猫现在几点`
- 也可以先说「你好黑猫」，听到"我在"后再单独说指令

### 图形界面
- **主窗口**：实时展示"你说了什么"和"助手回复什么"
- **系统托盘图标**：右键可「模型设置 / 退出」
- **设置面板**：主窗口点「⚙ 设置」，可图形化配置唤醒词、灵敏度、语音引擎、声音角色、大模型

### 文本模式
输入 `0` 切换到文本模式，输入 `1` 切回语音模式（仅源码运行、有控制台时可用）

### ASR 纠错说明
语音识别可能将"打开微信"误识别为"打一微信"，纠错模块会自动修正后再解析，无需重复指令。

### 抖音控制说明
确保抖音桌面版已打开并处于前台状态，语音指令将模拟键盘快捷键操作。

## 配置文件

`config/config.yaml` 可配置以下选项（大部分也可以在图形设置面板里改）：

```yaml
wake_words:
  - 你好黑猫
  - 助手
  - 黑猫
  - 你好助手
language: zh-CN
speech_engine: sherpaonnx  # 语音识别引擎：sherpaonnx（本地）/ google（网络）
speech_model_path: models/sense_voice  # sherpa-onnx 模型路径
tts_engine: vits  # 语音合成引擎：vits（本地）/ edge（在线神经语音）/ pyttsx3（机械音）/ auto
tts_voice: zh-CN-XiaoxiaoNeural  # edge 语音角色：晓晓/云希/云扬 等
intents_path: data/intents.json
intent_descriptions_path: data/intent_descriptions.json
plugin_path: plugins
log_file: assistant.log
app_map_path: config/app_map.json

# 解析与增强
nlu_engine: fuzzy_regex
enable_asr_correction: true
enable_douyin_control: true

# 唤醒词检测
enable_wake_word_detector: false
wake_word_engine: text_fallback   # sherpaonnx_vad / porcupine / text_fallback
wake_word_sensitivity: 0.5
energy_threshold: 0.02  # 语音检测能量阈值，环境噪音大时调高

# 危险操作确认（关机/重启/休眠）
confirm_dangerous_actions: true

# Ollama 大模型（AI 对话），留空则自动检测已部署的模型
ollama_model: ''
```

> 用户手动选择的设置（唤醒词、灵敏度、引擎、声音角色、模型）会持久化到 exe 旁的 `settings.json` 和 `model_selection.json`，优先于 `config.yaml`。

## 项目结构

```
黑猫语音助手/
├── config/
│   ├── config.yaml        # 主配置文件
│   └── app_map.json       # 应用路径映射
├── data/
│   ├── intents.json        # 意图定义（含系统面板控制）
│   └── intent_descriptions.json  # 语义描述
├── models/
│   ├── sense_voice/        # 语音识别模型（本地识别必需）
│   │   ├── model.int8.onnx
│   │   └── tokens.txt
│   └── vits-melo-tts-zh_en/  # 本地语音合成模型
├── plugins/
│   └── example_plugin.py   # 插件示例
├── src/
│   ├── main.py            # 程序入口（语音监听主循环）
│   ├── config.py          # 配置加载
│   ├── executor.py        # 指令执行 + AI 对话（Ollama）
│   ├── intents.py         # 意图解析
│   ├── recognize.py       # 语音识别（持续监听 + Sherpa-onnx）
│   ├── feedback.py        # 反馈（TTS/通知）
│   ├── gui.py             # 图形主窗口 + 设置面板
│   ├── tray.py            # 系统托盘图标
│   ├── cleaner.py         # 磁盘清理模块
│   ├── stop.py            # 停止机制
│   ├── logger.py          # 日志
│   └── nlu/               # NLU 增强模块
│       ├── phonetic_corrector.py  # ASR 纠错
│       ├── fuzzy_regex.py         # 增强正则匹配
│       ├── rules.py               # 共享回退规则
│       ├── douyin_controller.py   # 抖音键盘控制
│       └── wake_word_detector.py  # VAD 唤醒词检测
├── firstUI.png           # 主窗口背景图
├── icon.ico / icon.png   # 图标
├── version_info.txt      # exe 版本信息
├── VoiceAssistant.spec   # PyInstaller 打包配置
├── dist/
│   └── VoiceAssistant.exe  # 打包好的可执行文件
├── README.md
└── 功能规划大纲.md
```

## 扩展插件

在 `plugins/` 目录下创建 `.py` 文件，需包含：

```python
intent_name = 'my_command'

def execute(slots):
    return '执行结果'
```

插件被加载后，通过意图名称自动触发。

## 扩展意图

编辑 `data/intents.json` 添加自定义意图模式：

```json
[
    {
        "name": "my_intent",
        "patterns": ["我的指令(.+)"],
        "slots": {"keyword": "(.+)"}
    }
]
```

## 打包

```powershell
pip install pyinstaller
pyinstaller VoiceAssistant.spec
```

打包产物在 `dist\VoiceAssistant.exe`（单文件、无控制台窗口、带图标和版本信息）。运行时的临时文件会自动重定向到 exe 旁的 `temp` 目录，并定期清理。

> **打包前准备**：本地语音识别依赖 `models/sense_voice/` 下的模型；若仓库不含模型，先运行 `python setup_models.py` 下载，或打包时把模型目录一并放入 `dist\` 对应路径。

## 性能优化与重构

本轮针对"体验不够流畅、代码粗糙"做了系统性优化，集中在语音链路与资源加载：

### 语音识别 / 唤醒（recognize.py）
- **VAD 端点检测替代固定时长录音**：指令捕获从「固定录 N 秒」改为 `listen_until_silence`，用户说完即停，唤醒后不再傻等整段时长。
- **唤醒监听改为流式短块检测**：常驻监听从「每轮录制固定片段再判断」改为 `iter_blocks` 流式读取 0.3s 短块并实时做 VAD，延迟从「整段录音」降到单块粒度，消除盲等。
- **静音跳过（RMS 门控）**：滑动窗口识别中，静音窗口直接跳过完整的 SenseVoice 解码，安静环境下 CPU 占用显著下降。
- **抽取共享 `_block_to_wav` 辅助函数**，Sherpa 与 Google 两条识别链路复用，去掉重复实现。

### 语音播报（feedback.py）
- **单后台工作线程 + 队列**：原来每条播报都新建线程（频繁创建销毁），现改为常驻 TTS 工作线程从队列顺序消费，避免线程抖动。
- **引擎可用性缓存**：断网时不再每条都重试在线 `edge` 引擎（每次多秒延迟），失败引擎进入冷却，直接走本地 VITS / 机械音兜底。

### 指令执行（executor.py）
- **Program Files 索引缓存**：打开未知应用不再每次 `os.walk` 全盘扫描 `C:\Program Files`，改为首次构建一次缓存索引后按名命中。
- **Ollama 模型检测加 TTL 缓存**：AI 对话不再每轮都调用 `ollama.list()`，自动检测结果缓存 60 秒，感知模型切换的同时避免频繁请求。

### 代码清理
- 移除 `SpeechRecognizer.listen_once` 中因合并产生的死代码分支（旧实现残留在函数尾部，永不执行）。
- `SpeechRecognizer.listen_with_wake_word` 复用模块级 `match_wake_word`，去掉与模块函数重复的模糊匹配逻辑。

## 开发历程

### v0.0.3（当前版本）
- **唤醒词稳健性修复**：
  - 识别匹配从「字符级模糊」升级为「拼音级匹配」，彻底解决同音字误判——「黑猫」与「黑毛」、「助手」与「朱手」拼音相同即等价，两字唤醒词（尤其「黑猫」）不再不稳定。
  - KWS 模型原生词表不含自定义短唤醒词时自动回退到 STT + 拼音匹配（而非强行用 KWS 永远检测不到），对短词更稳。
  - 两字及以下唤醒词放宽匹配阈值，避免「永远命中不了」。
- **新增停止词（重新监听，不退出程序）**：
  - 默认停止词：`停止`、`停下`、`闭嘴`、`别说了`、`打住`，可在 `config/config.yaml` 或 `settings.json` 的 `stop_words` 配置。
  - AI 生成 / TTS 播报期间说出停止词即可中断当前回答并静音，回到监听状态；空闲时说出停止词直接重新监听。
  - 文本模式按 `Esc` / `q` / `s` 键也可停止。

### v0.0.1
- 图形主窗口（展示识别与输出）+ 系统托盘图标
- 可视化设置面板（唤醒词、灵敏度、语音引擎、声音角色、大模型）
- 接入 Ollama 大模型对话，自动检测模型 + 手动选择
- 持续监听唤醒词（滑动窗口识别，高噪音环境友好）
- 磁盘清理模块（临时文件重定向 + 定时清理 + 语音指令）
- PyInstaller 打包（单文件 exe、图标、版本信息）

早期开发记录：
- 基础语音助手：应用管理、系统控制、文件操作、定时提醒、插件体系
- 本地离线语音识别（Sherpa-onnx + SenseVoice），自动检测语音引擎
- 系统面板控制、截图、投影、清空回收站
- NLU 增强（FuzzyRegex、ASR 纠错）、抖音键盘控制
- 中文数字识别、系统音量控制（pycaw）

## License

MIT
