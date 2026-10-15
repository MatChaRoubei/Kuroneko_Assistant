"""Build a clean, relocatable folder distribution and shareable ZIP."""
import hashlib
import subprocess
import sys
import zipfile
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    required = [
        'models/sense_voice/model.int8.onnx', 'models/sense_voice/tokens.txt',
        'models/vad/silero_vad.onnx',
        'models/vits-melo-tts-zh_en/model.onnx', 'models/vits-melo-tts-zh_en/tokens.txt',
    ]
    missing = [name for name in required if not (ROOT / name).is_file()]
    if missing:
        raise SystemExit('模型不完整，请先运行 python setup_models.py：\n' + '\n'.join(missing))
    import re
    version = re.search(r"FileVersion',\s*'([0-9.]+)'", (ROOT / 'version_info.txt').read_text(encoding='utf-8')).group(1)
    destination = ROOT / 'dist' / ('release-' + version)
    subprocess.run([
        sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
        '--distpath', str(destination), '--workpath', str(ROOT / 'build' / 'release'),
        str(ROOT / 'VoiceAssistant.spec'),
    ], cwd=ROOT, check=True)
    package = destination / 'VoiceAssistant'
    (package / '使用说明.txt').write_bytes((ROOT / 'QUICKSTART.txt').read_bytes())
    # Never package personal files from the working tree or a used distribution.
    excluded = {'userdata', 'settings.json', 'model_selection.json', 'chat_memory.json',
                'long_term_memory.json', 'reminders.json', 'console.log', 'assistant.log', 'debug_report.txt', 'portable.flag'}
    archive = ROOT / 'dist' / 'BlackCatAssistant-portable.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for path in sorted(package.rglob('*')):
            if path.is_file() and not any(part in excluded for part in path.relative_to(package).parts):
                bundle.write(path, Path('VoiceAssistant') / path.relative_to(package))
    with archive.open('rb') as stream:
        hasher = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            hasher.update(chunk)
        digest = hasher.hexdigest()
    archive.with_suffix('.zip.sha256').write_text(f'{digest}  {archive.name}\n', encoding='ascii')
    print(f'分享完整压缩包：{archive}\nSHA256: {digest}')
    # Keep existing shortcuts working and preserve the old executable.
    compiler = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    if compiler.is_file():
        launcher = ROOT / 'build' / 'VoiceAssistant-launcher.exe'
        subprocess.run([str(compiler), '/nologo', '/target:winexe', '/reference:System.Windows.Forms.dll',
                        '/out:' + str(launcher), str(ROOT / 'launcher.cs')], check=True)
        entry = ROOT / 'dist' / 'VoiceAssistant.exe'
        backup = ROOT / 'dist' / 'legacy' / 'VoiceAssistant-0.0.4.exe'
        assert entry.resolve().is_relative_to(ROOT) and backup.resolve().is_relative_to(ROOT)
        if entry.is_file() and not backup.exists():
            backup.parent.mkdir(exist_ok=True)
            entry.replace(backup)
        import shutil
        shutil.copy2(launcher, entry)
        print(f'原快捷方式入口已更新：{entry}')


if __name__ == '__main__':
    main()
