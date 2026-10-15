"""GPT-SoVITS api_v2 adapter; the voice model runs in its own local service."""
import asyncio
import io
import wave
import os
from urllib.parse import urlsplit

import httpx
import numpy as np

DEFAULT_PROFILE = {
    'url': 'http://127.0.0.1:9880', 'ref_audio_path': '', 'prompt_text': '',
    'prompt_lang': 'ja', 'text_lang': 'auto', 'speed_factor': 1.0,
}


def validate_profile(profile):
    profile = {**DEFAULT_PROFILE, **profile}
    address = urlsplit(profile['url'])
    if address.scheme not in ('http', 'https') or address.hostname not in ('127.0.0.1', 'localhost', '::1'):
        raise ValueError('角色声音服务目前仅支持本机地址，例如 http://127.0.0.1:9880。')
    if address.username or address.password or address.query or address.fragment or address.path not in ('', '/', '/tts'):
        raise ValueError('请填写本机 GPT-SoVITS 服务地址，不要附带参数。')
    if not str(profile['ref_audio_path']).strip():
        raise ValueError('请先选择角色参考音频。通用模型不能自动变成指定角色的声音。')
    if profile['prompt_lang'] not in ('zh', 'ja', 'en') or profile['text_lang'] not in ('zh', 'ja', 'auto'):
        raise ValueError('角色声音语言配置无效。')
    if not 0.7 <= float(profile['speed_factor']) <= 1.3:
        raise ValueError('角色声音语速需要在 0.7～1.3 之间。')
    return profile


def make_request(text, profile):
    profile = validate_profile(profile)
    url = profile['url'].rstrip('/')
    if not url.endswith('/tts'):
        url += '/tts'
    return url, {
        'text': text, 'text_lang': profile['text_lang'],
        'ref_audio_path': profile['ref_audio_path'], 'prompt_text': profile['prompt_text'],
        'prompt_lang': profile['prompt_lang'], 'speed_factor': float(profile['speed_factor']),
        'text_split_method': 'cut5', 'batch_size': 1,
        'media_type': 'wav', 'streaming_mode': False,
    }


def decode_audio(data):
    with wave.open(io.BytesIO(data), 'rb') as wav:
        width, channels, rate = wav.getsampwidth(), wav.getnchannels(), wav.getframerate()
        if width not in (2, 4) or channels not in (1, 2) or not 8000 <= rate <= 96000:
            raise ValueError('角色声音服务返回了不支持的 WAV 格式。')
        samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype='<i2' if width == 2 else '<i4')
        samples = samples.astype(np.float32) / (32768 if width == 2 else 2147483648)
        if channels == 2:
            samples = samples.reshape(-1, 2).mean(axis=1)
        if not samples.size:
            raise ValueError('角色声音服务返回空音频。')
    return samples, rate


def check_readiness(profile):
    """Check the actual local API and assets, not just the Python adapter."""
    profile = {**DEFAULT_PROFILE, **profile}
    # Validate the address even when no reference has been selected yet.
    validated = validate_profile({**profile, 'ref_audio_path': profile['ref_audio_path'] or 'pending'})
    root = validated['url'].rstrip('/').removesuffix('/tts')
    try:
        with httpx.Client(timeout=2, trust_env=False) as client:
            response = client.get(root + '/openapi.json')
    except httpx.RequestError as exc:
        raise RuntimeError('GPT-SoVITS 本地服务未连接。助手只提供连接接口，不包含 GPT-SoVITS 服务和角色模型。请先启动 api_v2.py 服务。') from exc
    try:
        paths = response.json().get('paths', {})
    except (ValueError, AttributeError):
        paths = {}
    if response.status_code != 200 or 'post' not in paths.get('/tts', {}):
        raise RuntimeError('此地址没有 GPT-SoVITS api_v2 的 /tts 接口，请检查是否连接了其他服务。')
    validate_profile(profile)
    if not os.path.isfile(profile['ref_audio_path']):
        raise ValueError('角色参考音频文件不存在，请选择本机可读取的音频文件。')
    return profile


async def _fetch(text, profile, cancelled):
    url, payload = make_request(text, profile)
    async def request():
        # trust_env=False keeps a loopback voice request off system proxy routes.
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=2), trust_env=False) as client:
            async with client.stream('POST', url, json=payload) as response:
                if response.status_code != 200:
                    raise RuntimeError(f'角色声音服务返回 HTTP {response.status_code}，请检查服务日志和参考音频。')
                pieces, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > 20 * 1024 * 1024:
                        raise ValueError('角色声音音频过大，已停止接收。')
                    pieces.append(chunk)
                return decode_audio(b''.join(pieces))
    task = asyncio.create_task(request())
    deadline = asyncio.get_running_loop().time() + 30
    try:
        while not task.done():
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError('角色声音合成超时')
            if cancelled():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                return None
            await asyncio.sleep(0.05)
        return await task
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


def synthesize(text, profile, cancelled=lambda: False):
    validate_profile(profile)
    if not os.path.isfile(profile['ref_audio_path']):
        raise ValueError('角色参考音频文件不存在，请选择本机可读取的音频文件。')
    try:
        return asyncio.run(_fetch(text, profile, cancelled))
    except httpx.ConnectError as exc:
        raise RuntimeError('GPT-SoVITS 本地服务未连接，请先启动 api_v2.py 服务；此安装包不包含该服务和角色模型。') from exc
