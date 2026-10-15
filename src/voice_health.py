"""Real synthesis checks shared by playback and settings; never play probe audio."""
import asyncio
import re


def azure_endpoint(region):
    region = str(region).strip().lower()
    if not re.fullmatch(r'[a-z][a-z0-9]{1,39}', region):
        raise ValueError('Azure 区域应为资源的区域名称，例如 japaneast 或 chinaeast2。')
    domain = ('azure.cn' if region.startswith('china') else
              'azure.us' if region.startswith('usgov') else 'microsoft.com')
    return f'https://{region}.tts.speech.{domain}/cognitiveservices/v1'


def error_message(error):
    """Avoid showing request URLs, client tokens, or proxy credentials."""
    status = getattr(error, 'status', None) or getattr(error, 'code', None)
    if status == 403:
        return '微软服务拒绝请求（HTTP 403）；当前在线声音不可用。'
    if status == 401:
        return '鉴权失败（HTTP 401），请检查订阅密钥及对应区域。'
    if status:
        return f'语音服务返回 HTTP {status}。'
    if isinstance(error, (TimeoutError, asyncio.TimeoutError)) or 'timeout' in type(error).__name__.lower():
        return '语音服务连接或合成超时，请检查网络和代理。'
    if type(error).__name__ in ('ConnectError', 'ClientConnectorError', 'ConnectionRefusedError'):
        return '无法连接语音服务，请检查服务是否启动，以及地址和端口。'
    if isinstance(error, (ValueError, RuntimeError)):
        return str(error).split('http')[0][:220]
    return f'语音请求失败（{type(error).__name__}），请检查网络或服务。'


async def edge_audio(text, voice, rate, pitch, cancelled=lambda: False):
    import edge_tts
    from src.edge_compat import ensure_compatibility
    ensure_compatibility()
    if str(pitch).endswith('%'):
        try:
            pitch = f'{float(pitch[:-1]):+.0f}Hz'
        except ValueError:
            pitch = '+0Hz'

    async def fetch():
        voice_stream = edge_tts.Communicate(text, voice=voice, rate=rate, pitch=pitch,
                                           connect_timeout=4, receive_timeout=8).stream()
        pieces, size = [], 0
        try:
            async for chunk in voice_stream:
                if chunk['type'] == 'audio':
                    size += len(chunk['data'])
                    if size > 20 * 1024 * 1024:
                        raise ValueError('在线语音返回的音频过大。')
                    pieces.append(chunk['data'])
        finally:
            await voice_stream.aclose()
        if not size:
            raise RuntimeError('在线语音未返回音频。')
        return b''.join(pieces)

    task = asyncio.create_task(fetch())
    deadline = asyncio.get_running_loop().time() + 10
    try:
        while not task.done():
            if cancelled():
                return None
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError('在线语音合成超时')
            await asyncio.sleep(0.05)
        return await task
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


def probe(engine, values):
    """Return success only after that selected engine produces audio, no fallback."""
    try:
        if engine == 'edge':
            data = asyncio.run(edge_audio('语音连接测试。', values['voice'], values['rate'], values['pitch']))
            return True, f'微软在线语音合成成功（{len(data)} 字节），未播放。'
        if engine == 'gptsovits':
            from src.character_tts import check_readiness, synthesize
            profile = values['character']
            check_readiness(profile)
            samples, rate = synthesize('我已经听见了。', profile)
            return True, f'角色声音合成成功（{len(samples) / rate:.1f} 秒），未播放；音色仍需试听确认。'
        if engine in ('vits', 'auto'):
            from src.feedback import _init_vits, _tts_lock
            with _tts_lock:
                model = _init_vits()
                if model is None:
                    raise RuntimeError('本地自然语音模型未加载成功。')
                audio = model.generate('语音连接测试。', sid=0, speed=1.0)
                if not len(audio.samples):
                    raise RuntimeError('本地模型返回空音频。')
            return True, '本地自然语音合成成功，未播放。'
        if engine == 'azure':
            from src.feedback import azure_audio
            audio = azure_audio('语音连接测试。', values['key'], values['region'],
                                values['azure_voice'], values['rate'], values['pitch'], values['style'])
            return True, f'Azure 合成成功（{len(audio)} 字节），未播放。'
        return False, 'Windows 系统声音请使用“保存并试听当前声音”检查实际播放。'
    except Exception as exc:
        return False, error_message(exc)
