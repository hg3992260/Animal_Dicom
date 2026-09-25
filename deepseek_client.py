# -*- coding: utf-8 -*-
"""DeepSeek API 客户端（仅用标准库 urllib，无第三方依赖）。

配置读取顺序：deepseek_config.json → 环境变量 DEEPSEEK_API_KEY。
调用：chat(cfg, messages) → 返回助手文本。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"


class DeepSeekError(Exception):
    """DeepSeek 调用失败。"""


def load_config(path: str) -> dict:
    cfg = {"base_url": DEFAULT_BASE_URL, "model": DEFAULT_MODEL,
           "temperature": 0.3, "api_key": ""}
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                cfg.update(data)
    except Exception:  # noqa: BLE001
        pass
    if not (cfg.get("api_key") or "").strip():
        cfg["api_key"] = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    return cfg


def save_config(path: str, cfg: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def chat(cfg: dict, messages: list, timeout: float = 150.0) -> str:
    """调用 /chat/completions，返回第一条回复的文本。"""
    key = (cfg.get("api_key") or "").strip()
    if not key:
        raise DeepSeekError("未配置 DeepSeek API Key")
    url = (cfg.get("base_url") or DEFAULT_BASE_URL).rstrip("/") + "/chat/completions"
    payload = {"model": cfg.get("model") or DEFAULT_MODEL,
               "messages": messages,
               "temperature": float(cfg.get("temperature", 0.3)),
               "stream": False}
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + key},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            body = ""
        raise DeepSeekError("HTTP %s: %s" % (exc.code, body[:300]))
    except Exception as exc:  # noqa: BLE001
        raise DeepSeekError(str(exc))
    try:
        return data["choices"][0]["message"]["content"]
    except Exception:  # noqa: BLE001
        raise DeepSeekError("响应格式异常：%s" % str(data)[:200])
