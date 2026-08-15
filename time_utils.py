"""统一的北京时间工具。"""

from datetime import datetime
from zoneinfo import ZoneInfo


BEIJING_TZ = ZoneInfo("Asia/Shanghai")


def beijing_now():
    """返回当前北京时间，避免依赖容器或宿主机系统时区。"""
    return datetime.now(BEIJING_TZ)
