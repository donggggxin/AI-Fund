"""Streamlit 到 FastAPI 的轻量客户端；未配置 URL 时由前端本地降级。"""

import os

import requests


class BackendUnavailable(RuntimeError):
    pass


def get_diagnostics():
    base_url = os.getenv("API_BASE_URL", "").rstrip("/")
    if not base_url:
        raise BackendUnavailable("API_BASE_URL is not configured")
    headers = {}
    api_key = os.getenv("FUND_API_KEY", "")
    if api_key:
        headers["X-API-Key"] = api_key
    try:
        response = requests.get(
            f"{base_url}/api/diagnostics", headers=headers, timeout=30
        )
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        raise BackendUnavailable(str(exc)) from exc
