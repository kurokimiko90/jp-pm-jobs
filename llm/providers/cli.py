"""CLI subprocess provider — 走本機已登入的 LLM CLI（OAuth 訂閱制，免 API key）。

支援 claude / codex / gemini 等 CLI。二進位尋找順序：
1. config 的 binaries 清單（可為絕對路徑）
2. 環境變數 CLAUDE_PATH / CODEX_PATH / GEMINI_PATH / OPENAI_CLI_PATH
3. PATH 上的 which
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .base import BaseLLMProvider, LLMResponse, ProviderError

_ENV_PATH_VARS = {
    "claude": "CLAUDE_PATH",
    "codex": "CODEX_PATH",
    "gemini": "GEMINI_PATH",
    "openai": "OPENAI_CLI_PATH",
}
_FALLBACK_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"


def _resolve(binary: str) -> str | None:
    """binary 名或路徑 → 可執行的絕對路徑；找不到回 None。"""
    p = Path(binary).expanduser()
    if p.is_absolute() and p.exists():
        return str(p)
    env_var = _ENV_PATH_VARS.get(Path(binary).name)
    if env_var and os.environ.get(env_var):
        ep = Path(os.environ[env_var]).expanduser()
        if ep.exists():
            return str(ep)
    return shutil.which(binary)


class CLIProvider(BaseLLMProvider):
    name = "cli"

    def __init__(self, cfg: dict | None = None):
        cfg = cfg or {}
        self.binaries: list[str] = cfg.get("binaries", ["claude", "codex", "gemini"])

    def is_available(self) -> bool:
        return any(_resolve(b) for b in self.binaries)

    def accepts_model(self, model: str) -> bool:
        return True  # --model 只轉給 claude CLI，其餘忽略

    def _run(self, bpath: str, prompt: str, timeout: int, model: str) -> str | None:
        args = [bpath]
        binname = Path(bpath).name.lower()
        if model and "claude" in binname:
            args += ["--model", model]
        if "codex" in binname:
            args += ["exec", prompt]
        else:
            args += ["-p", prompt]
        env = os.environ.copy()
        if not env.get("PATH"):  # 背景進程 PATH 丟失防護
            env["PATH"] = _FALLBACK_PATH
        try:
            result = subprocess.run(args, capture_output=True, text=True,
                                    timeout=timeout, env=env)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return None
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        return None

    def call(self, prompt: str, model: str = "", timeout: int = 180,
             accept: dict | None = None) -> LLMResponse:
        tried: list[str] = []
        for binary in self.binaries:
            bpath = _resolve(binary)
            if not bpath:
                continue
            tried.append(binary)
            out = self._run(bpath, prompt, timeout, model)
            if out:
                return LLMResponse(text=out, model=model, provider=f"cli:{binary}")
        raise ProviderError(
            f"cli: 無 CLI 回應（嘗試過: {tried or self.binaries}）。"
            "請確認已安裝並登入 claude / codex / gemini 其中之一。")
