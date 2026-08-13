"""本地状态存储：统一读取和原子写入，避免半写文件。"""

import json
import os
import tempfile
from pathlib import Path


def load_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return {} if default is None else default
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {} if default is None else default


def save_json(path, data):
    """在同一目录写临时文件后原子替换目标文件。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=4)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def initialize_data_files(data_dir, template_dir):
    """首次启动时创建可写运行文件，不覆盖已有用户数据。"""
    data_dir, template_dir = Path(data_dir), Path(template_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    config_path = data_dir / "fund_config.json"
    if not config_path.exists():
        template = load_json(template_dir / "fund_config.example.json")
        save_json(config_path, template)
    for filename in ("holdings_cache.json", "trend_matrix.json"):
        path = data_dir / filename
        if not path.exists():
            save_json(path, {})
    report_path = data_dir / "agent_report.md"
    if not report_path.exists():
        report_path.write_text(
            "# AI 智能诊断报告\n\n尚未生成报告。\n", encoding="utf-8"
        )
