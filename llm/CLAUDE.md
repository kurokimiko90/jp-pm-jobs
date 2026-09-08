# llm/ — LLM Provider 抽象層（2026-07 開源改造）

`interview/_llm.py` 已改為 adapter，實際邏輯在這裡（`llm/__init__.py` 的 `call()` / `health()`）。
呼叫端（`analyzer/gap_analyzer.py`、`tools/resume_tailor.py` 等）不受影響，仍是
`from interview._llm import call`——換 provider 不需要改任何呼叫端程式碼。

## 設定

`config/llm.yaml`（本人環境用 `miko_gateway`，範本見 `config/llm.yaml.example`）：

```yaml
chain: [anthropic, cli]   # 依序嘗試，第一個成功者勝出
providers:
  anthropic: {api_key_env: ANTHROPIC_API_KEY, model: claude-sonnet-4-5}
  cli: {binaries: [claude, codex, gemini]}   # OAuth 訂閱制，免 API key
```

缺 `config/llm.yaml` 時自動偵測：`.env` 有哪家 API key 用哪家，都沒有則試本機 CLI。
`LLM_CONFIG=<path>` 環境變數可覆蓋預設路徑（`analyzer/gap_analyzer.py --backfill` 常駐排程用
`config/llm.gap.yaml` 強制走 `miko_gateway`，見 `analyzer/CLAUDE.md`）。

## Provider 實作

| Provider | 檔案 | 認證方式 |
|---|---|---|
| Anthropic API | `llm/providers/anthropic_api.py` | `ANTHROPIC_API_KEY` |
| OpenAI API | `llm/providers/openai_api.py` | `OPENAI_API_KEY` |
| Gemini API | `llm/providers/gemini_api.py` | `GEMINI_API_KEY` |
| CLI（OAuth 訂閱） | `llm/providers/cli.py` | 本機已登入的 `claude` / `codex` / `gemini` binary |
| miko_gateway | `llm/providers/miko_gateway.py` | 內部指揮中心（本人環境專用） |

共同基類 `llm/providers/base.py`——新增 provider 只需繼承它、實作 `call()` / `health()`，
再加進 `config/llm.yaml` 的 `chain` 即可，不需改 `llm/__init__.py`。

## Gotcha

`cli.py` 用 `subprocess` 呼叫 claude/codex/gemini，在 Claude Code 的 `run_in_background` 下
PATH 可能丟失（已有 fallback PATH 防護）。遇到全 provider 失敗不要反覆重試，直接用 Claude Code
自己完成 LLM 歸納任務。
