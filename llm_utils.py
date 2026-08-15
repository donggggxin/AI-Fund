# -*- coding: utf-8 -*-
"""Helpers shared by the web and console LLM clients."""

import re


def build_chat_completions_url(provider, base_url):
    """Build an OpenAI-compatible chat completions URL safely.

    The UI accepts either an API root, a root ending in ``/v1``, or the
    complete ``/chat/completions`` endpoint.  Keep each form usable without
    duplicating path segments.
    """
    provider = (provider or "").strip().lower()
    url = (base_url or "").strip().rstrip("/")

    if url.endswith("/chat/completions"):
        return url

    if provider == "deepseek":
        # DeepSeek's documented endpoint is /chat/completions, not /v1/...
        url = re.sub(r"/v1$", "", url)
        url = url or "https://api.deepseek.com"
    else:
        if not url:
            url = "https://api.openai.com/v1"
        elif url == "https://api.openai.com":
            url = f"{url}/v1"

    return f"{url}/chat/completions"


def build_gemini_url(base_url, model, api_key):
    """Build a Gemini generateContent URL without duplicating /v1beta."""
    url = (base_url or "").strip().rstrip("/")
    url = re.sub(r"/v1beta$", "", url)
    root = url or "https://generativelanguage.googleapis.com"
    return f"{root}/v1beta/models/{model}:generateContent?key={api_key}"


def format_api_error(response):
    """Return useful diagnostics without exposing query-string API keys."""
    endpoint = response.url.split("?", 1)[0]
    detail = (response.text or "").strip().replace("\n", " ")
    if len(detail) > 500:
        detail = f"{detail[:500]}..."
    return f"API 呼叫失败: {response.status_code}\n接口: {endpoint}\n{detail}"
