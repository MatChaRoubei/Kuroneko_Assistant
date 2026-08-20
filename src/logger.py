import logging
import os


def configure_logging(log_file=None):
    handlers = [logging.StreamHandler()]
    if log_file:
        log_dir = os.path.dirname(log_file)
        if log_dir and not os.path.isdir(log_dir):
            os.makedirs(log_dir, exist_ok=True)
        try:
            handlers.append(logging.FileHandler(log_file, encoding='utf-8'))
        except (OSError, IOError) as e:
            print(f'无法创建日志文件 {log_file}: {e}')

    logging.basicConfig(
        level=logging.INFO,
        format='[%(asctime)s] %(levelname)s: %(message)s',
        handlers=handlers
    )


def get_logger():
    return logging.getLogger('voice_assistant')
