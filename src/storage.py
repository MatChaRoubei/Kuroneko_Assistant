"""Separate packaged resources from writable, private user data."""
import json
import os
import sys
import tempfile
from functools import lru_cache
from pathlib import Path


def app_directory():
    return Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent.parent


@lru_cache(maxsize=1)
def data_directory():
    # A portable distribution opts in explicitly. Installed/read-only locations
    # use the current Windows account, never the developer's build directory.
    base = app_directory()
    if not getattr(sys, 'frozen', False):
        target = base
    elif (base / 'portable.flag').exists():
        target = base / 'userdata'
    else:
        target = Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'KuronekoAssistant'
    try:
        target.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=target):
            pass
    except OSError:
        target = Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'KuronekoAssistant'
        target.mkdir(parents=True, exist_ok=True)
    return target


def user_file(name):
    return str(data_directory() / name)


def read_json(path, default=None):
    try:
        with open(path, encoding='utf-8-sig') as stream:
            return json.load(stream)
    except (OSError, ValueError):
        return default


def write_json(path, value):
    """Replace atomically so interruption cannot leave a half-written setting."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=target.name + '.', suffix='.tmp', dir=target.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, default=str)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def migrate_legacy_settings():
    """Copy this machine's old settings only when the new location is empty."""
    if not getattr(sys, 'frozen', False):
        return
    base = app_directory()
    sources = [base]
    if base.name == 'VoiceAssistant' and (base.parent.name == 'release' or base.parent.name.startswith('release-')):
        sources.append(base.parent.parent)
    for name in ('settings.json', 'model_selection.json'):
        target = Path(user_file(name))
        if target.exists():
            continue
        for source in sources:
            legacy = source / name
            if legacy == target:
                continue
            data = read_json(legacy)
            if isinstance(data, dict):
                write_json(target, data)
                break
