#!/usr/bin/env python3
"""一键下载并放置语音模型。

自动下载 sherpa-onnx 的 SenseVoice（本地语音识别）和 VITS Melo TTS
（本地语音合成）模型，解压到 models/ 目录。

用法：
    python setup_models.py

模型较大（合计约 350MB），请保持网络畅通。已存在的模型会自动跳过。
"""
import os
import sys
import tarfile
import shutil
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))

# SenseVoice 本地语音识别模型（int8 量化，中英日韩粤多语言）
ASR_URL = (
    'https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/'
    'sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09.tar.bz2'
)
# VITS Melo TTS 本地语音合成模型（中英混读）
TTS_URL = (
    'https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/'
    'vits-melo-tts-zh_en.tar.bz2'
)
# 唤醒词检测模型（KeywordSpotter，低功耗，仅约 31MB）
KWS_URL = (
    'https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/'
    'sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01.tar.bz2'
)


def download(url, filename):
    """下载文件，显示进度；已存在则跳过"""
    dest = os.path.join(ROOT, filename)
    if os.path.exists(dest):
        print(f'[跳过] {filename} 已存在')
        return dest

    print(f'开始下载 {filename} ...')

    def hook(blocks, block_size, total):
        if total > 0:
            done = min(blocks * block_size, total)
            pct = done * 100 // total
            sys.stdout.write(
                f'\r  进度: {pct:3d}%  ({done // (1024 * 1024)} / {total // (1024 * 1024)} MB)'
            )
            sys.stdout.flush()

    urllib.request.urlretrieve(url, dest, reporthook=hook)
    print(f'\n下载完成: {filename}')
    return dest


def setup_sense_voice():
    """下载 SenseVoice 模型 -> models/sense_voice/"""
    dest = os.path.join(ROOT, 'models', 'sense_voice')
    if os.path.exists(os.path.join(dest, 'model.int8.onnx')):
        print('[OK] SenseVoice 模型已就绪')
        return

    tar_path = download(ASR_URL, '_sense_voice.tar.bz2')
    os.makedirs(dest, exist_ok=True)
    with tarfile.open(tar_path, 'r:bz2') as tf:
        for m in tf.getmembers():
            base = os.path.basename(m.name)
            if base in ('model.int8.onnx', 'tokens.txt') and m.isfile():
                with tf.extractfile(m) as src, \
                        open(os.path.join(dest, base), 'wb') as dst:
                    shutil.copyfileobj(src, dst)
    os.remove(tar_path)
    print('[OK] SenseVoice 模型放置完成')


def setup_vits():
    """下载 VITS TTS 模型 -> models/vits-melo-tts-zh_en/"""
    dest = os.path.join(ROOT, 'models', 'vits-melo-tts-zh_en')
    if os.path.exists(os.path.join(dest, 'model.onnx')):
        print('[OK] VITS TTS 模型已就绪')
        return

    tar_path = download(TTS_URL, '_vits.tar.bz2')
    os.makedirs(dest, exist_ok=True)
    with tarfile.open(tar_path, 'r:bz2') as tf:
        for m in tf.getmembers():
            name = m.name.split('/', 1)[-1]  # 去掉顶层目录
            if not name or not m.isfile():
                continue
            target = os.path.join(dest, name)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with tf.extractfile(m) as src, open(target, 'wb') as dst:
                shutil.copyfileobj(src, dst)
    os.remove(tar_path)
    print('[OK] VITS TTS 模型放置完成')


def setup_kws():
    """下载 KWS 唤醒词检测模型 -> models/kws/"""
    dest = os.path.join(ROOT, 'models', 'kws')
    if os.path.exists(os.path.join(dest, 'tokens.txt')):
        print('[OK] KWS 唤醒词检测模型已就绪')
        return

    tar_path = download(KWS_URL, '_kws.tar.bz2')
    os.makedirs(dest, exist_ok=True)
    with tarfile.open(tar_path, 'r:bz2') as tf:
        for m in tf.getmembers():
            base = os.path.basename(m.name)
            if m.isfile() and base.endswith(('.onnx', '.txt')):
                with tf.extractfile(m) as src, \
                        open(os.path.join(dest, base), 'wb') as dst:
                    shutil.copyfileobj(src, dst)
    os.remove(tar_path)
    print('[OK] KWS 唤醒词检测模型放置完成')


if __name__ == '__main__':
    print('=== 黑猫语音助手 - 语音模型下载 ===')
    try:
        setup_sense_voice()
        setup_vits()
        setup_kws()
        print('=== 全部完成 ===')
        print('现在可以运行：python src/main.py（源码）或 dist\\VoiceAssistant.exe（打包版）')
    except KeyboardInterrupt:
        print('\n已取消')
    except Exception as e:
        print(f'\n出错了: {e}')
        print('可重试，或按 README 说明手动下载模型。')
