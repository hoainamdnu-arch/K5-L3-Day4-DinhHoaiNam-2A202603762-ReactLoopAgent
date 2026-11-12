"""Đọc cấu hình .env đơn giản, không thực thi shell hay cần package ngoài."""
import os
from pathlib import Path
import sys


def configure_console():
    """Giữ log tiếng Việt hoạt động cả khi Windows chuyển output vào pipe."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')


def load_env(path=None):
    """Nạp KEY=value; biến đã đặt trong shell được giữ nguyên."""
    env_path = Path(path) if path is not None else Path(__file__).resolve().parent / '.env'
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding='utf-8-sig').splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        line = line.removeprefix('export ')
        name, separator, value = line.partition('=')
        name, value = name.strip(), value.strip()
        if not separator or not name.isidentifier():
            continue
        if len(value) >= 2 and value[0] in ('"', "'") and value[-1] == value[0]:
            value = value[1:-1]
        else:
            value = value.split(' #', 1)[0].rstrip()
        os.environ.setdefault(name, value)
