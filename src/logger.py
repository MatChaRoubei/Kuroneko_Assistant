import logging
import os
from logging.handlers import RotatingFileHandler


def configure_logging(log_file=None):
    handlers = [logging.StreamHandler()]
    if log_file:
        log_dir = os.path.dirname(log_file)
        if log_dir and not os.path.isdir(log_dir):
            os.makedirs(log_dir, exist_ok=True)
        try:
            # 滚动日志：单文件最大 5MB，保留 3 个备份（共约 20MB 上限）。
            # 避免长期运行无限累积把磁盘写爆（旧版 assistant.log 曾涨到数百 MB）。
            handlers.append(RotatingFileHandler(
                log_file, maxBytes=5 * 1024 * 1024, backupCount=3,
                encoding='utf-8'
            ))
        except (OSError, IOError) as e:
            print(f'无法创建日志文件 {log_file}: {e}')

    logging.basicConfig(
        level=logging.INFO,
        format='[%(asctime)s] %(levelname)s: %(message)s',
        handlers=handlers
    )


def get_logger():
    return logging.getLogger('voice_assistant')
