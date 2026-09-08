"""miko_llm.py — 呼叫本機 LLM 指揮中心（miko-ws runtime :3005 的 /api/llm/*）。

取代脆弱的 CLI subprocess（解 `interview/_llm.py` 在背景進程下 PATH 丟失導致
「No LLM CLI responded」的痛點）。文字走指揮中心的 brain pool（paid-first），
不再依賴本機 PATH 上的 claude/codex 二進位。

需求：miko-ws runtime 開著且 .env 設 LLM_CENTER_ENABLED=true。
若指揮中心不可用，呼叫方應自行 fallback（用 is_available() 先判斷）。

環境變數：
  LLM_GATEWAY_URL    預設 http://localhost:3005
  LLM_GATEWAY_TOKEN  若 miko-ws 設了密鑰則需一致
"""

import os
import time

import requests

BASE = os.environ.get("LLM_GATEWAY_URL", "http://localhost:3005")
TOKEN = os.environ.get("LLM_GATEWAY_TOKEN", "")
PROJECT = "jp-pm-jobs"

# miko-ws runtime 的 launchd label（KeepAlive 服務，kickstart -k 可強制重啓）
LAUNCHD_LABEL = os.environ.get("LLM_GATEWAY_LAUNCHD_LABEL", "com.miko.runtime")


def _headers():
    h = {"Content-Type": "application/json"}
    if TOKEN:
        h["x-llm-token"] = TOKEN
    return h


def _payload_error(r) -> str:
    """HTTP エラー時、body に入っている**本当の理由**を取り出す。

    指揮中心は 500 でも `{"ok": false, "error": "chatgpt1 未ログイン…"}` を返す。
    `raise_for_status()` だけだと「500 Server Error for url: …」しか残らず、
    どのアカウントがなぜ駄目だったのかが日誌から消える。
    """
    try:
        data = r.json()
    except ValueError:
        return " ".join((r.text or "").split())[:300] or "(body 空)"
    if isinstance(data, dict):
        return str(data.get("error") or data.get("message") or data)[:300]
    return str(data)[:300]


def _unwrap(r, key: str, what: str):
    """`{ok, <key>}` を取り出す。失敗時は body の理由まで載せて RuntimeError。"""
    if not r.ok:
        raise RuntimeError(f"{what} 失敗（HTTP {r.status_code}）: {_payload_error(r)}")
    try:
        data = r.json()
    except ValueError:
        raise RuntimeError(
            f"{what} 失敗: JSON ではない応答 — {' '.join((r.text or '').split())[:200]!r}") from None
    if not data.get("ok"):
        raise RuntimeError(f"{what} 失敗: {data.get('error') or 'error 欄なし'}")
    if key not in data:
        raise RuntimeError(f"{what} 失敗: 応答に {key!r} が無い（keys={sorted(data)}）")
    return data[key]


def health(timeout=5):
    r = requests.get(f"{BASE}/api/llm/health", timeout=timeout)
    return r.json()


def is_available():
    """指揮中心是否就緒（呼叫前先判斷，不可用時 fallback 原路徑）。"""
    try:
        return bool(health().get("ok"))
    except Exception:
        return False


def probe(timeout=90):
    """端到端探活：/api/llm/health 可能誤報 ok（gateway 進程活著但 brain
    路由全死 → 呼叫 500），用最小 prompt 真打一次 /api/llm/text 驗證。"""
    try:
        return bool(text("回覆：OK", timeout=timeout))
    except Exception:
        return False


def restart_gateway(wait=120, poll=5):
    """kickstart 重啓 miko-ws launchd 服務，輪詢 health 直到恢復。回傳是否恢復。

    非 macOS / launchctl 不存在 / label 未載入 → 回 False，不 raise
    （呼叫方照「不可用」處理，下輪重試）。
    """
    import subprocess
    try:
        r = subprocess.run(
            ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{LAUNCHD_LABEL}"],
            capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            print(f"[miko_llm] kickstart {LAUNCHD_LABEL} 失敗: "
                  f"{(r.stderr or r.stdout).strip()}")
            return False
    except Exception as e:
        print(f"[miko_llm] kickstart 異常: {e}")
        return False
    t0 = time.time()
    while time.time() - t0 < wait:
        time.sleep(poll)
        if is_available():
            print(f"[miko_llm] 指揮中心重啓恢復（{int(time.time() - t0)}s）")
            return True
    print(f"[miko_llm] 重啓後 {wait}s 內未恢復")
    return False


def ensure_available(restart=True, wait=120):
    """health 檢查；掛掉且 restart=True 時 kickstart 重啓一次。回傳最終是否可用。"""
    if is_available():
        return True
    if not restart:
        return False
    print("[miko_llm] 指揮中心不可用 → 嘗試重啓 launchd 服務")
    return restart_gateway(wait=wait)


def text(prompt, timeout=300, opts=None, engine="codex"):
    """同步取文字。失敗 raise RuntimeError。

    opts: 傳給指揮中心的 opts dict。可含 accept 驗收條件（指揮中心據此判斷
          brain 回應是否「完成」，不達標自動換下一個 brain）：
          {"accept": {"minChars": 2000, "minLines": 60,
                      "includesAll": ["## 段落A", ...], "includesAny": [...],
                      "notIncludes": [...], "regex": "...", "regexFlags": ""}}

    engine: 預設 "codex"（比 Chrome brain 快）。codex 序列鏈忙碌時指揮中心會
            自動 fallback 回 Chrome paid-first pool，不會硬等。傳 None 或已在
            opts 內指定 engine 則不覆蓋。
    """
    body_opts = dict(opts) if opts else {}
    if engine and "engine" not in body_opts:
        body_opts["engine"] = engine
    # 指揮中心の codex 有界超時は既定 60s。長い prompt（growth の 1.4 万字級）は
    # 必ず超えて Chrome pool へ落ちるので、caller の timeout を codex 側にも渡す。
    if body_opts.get("engine") == "codex" and "codexTimeoutMs" not in body_opts:
        body_opts["codexTimeoutMs"] = int(timeout * 1000)
    body = {"project": PROJECT, "prompt": prompt}
    if body_opts:
        body["opts"] = body_opts
    r = requests.post(
        f"{BASE}/api/llm/text",
        json=body,
        headers=_headers(),
        timeout=timeout,
    )
    return _unwrap(r, "text", "llm text")


def image(prompt, output_path, timeout=560, codex=None, engine=None, agy=None):
    """同步生圖（POST /api/llm/image）。指揮中心が失敗時の備援まで面倒を見る。

    engine: 第一希望の生圖エンジン。指揮中心が順番と備援を決める。
            None / "codex" → `codex exec` 内蔵 image_gen → gemini1 Chrome 備援。
            "agy"          → Antigravity CLI（`agy` の generate_image ＝ Gemini 画像）
                             → codex → gemini1 の順で備援。実測 codex より速い。
            "gemini1"      → Chrome CDP の Gemini → codex 備援。

    output_path: 絕對路徑，產出的 PNG 直接寫到這裡（同機才有意義 — 指揮中心
                 跟呼叫端共用檔案系統）。
    codex / agy: 可選 dict，透傳給對應底層的執行選項（如 {"timeoutMs": 480000}、
                 agy なら {"model": "gemini-3.1-pro-high"}）。
    timeout: HTTP 逾時秒數，需大於伺服端生圖預設逾時（8 分鐘）以免提前斷線。
    """
    body = {"project": PROJECT, "prompt": prompt, "outputPath": str(output_path)}
    if codex:
        body["codex"] = codex
    if agy:
        body["agy"] = agy
    if engine:
        body["engine"] = engine
    r = requests.post(
        f"{BASE}/api/llm/image",
        json=body,
        headers=_headers(),
        timeout=timeout,
    )
    return _unwrap(r, "image", "llm image")


class VoiceAPIUnsupported(RuntimeError):
    """指揮中心が /api/llm/voice を持っていない（古い runtime）。呼び出し側は
    自前の音声化経路（tts/gpt_voice.py のローカル CDP）へ落ちる。"""


def voice(text_, output_path, timeout=900, engine=None, instruction=None,
          fallback=None, gpt=None, edge=None):
    """同步で「文字 → 音檔」（POST /api/llm/voice）。

    指揮中心が **ChatGPT アカウントを順に試す**（1 つが未ログイン／朗読が始まらない
    等で駄目でも次のアカウントで録る）。全滅時の合成音（edge-tts）備援まで向こう持ち。

    engine: 第一希望。None / "gpt" → ChatGPT の読み上げ（人の声）。"edge" → 合成音のみ。
    instruction: 原稿の扱い方。未指定なら「一字一句そのまま読ませる」（＝純粋な TTS）。
                 指定すると text は依頼文として扱われ、ChatGPT が書き直してから読む。
                 このとき合成音への備援は自動で外れる（依頼文を読み上げた音檔を作らないため）。
    output_path: 絕對路徑。`.wav` なら向こうで ffmpeg 変換（同機共有前提）。
    gpt / edge: 下層への透過オプション（answerTimeoutMs / audioTimeoutMs / maxGap、
                edge は voice / rate）。

    戻り値: {"path", "raw", "mime", "bytes", "spoken", "engine", "account"}
    spoken は実際に読み上げられた本文 — 呼び出し側の事実チェックはこれに対して行う。

    ⚠️ text は外部（ChatGPT）へ出る。PII / 取引先ブランド名の遮蔽は**呼び出し側の責任**。
    """
    body = {"project": PROJECT, "text": text_, "outputPath": str(output_path)}
    if engine:
        body["engine"] = engine
    if instruction:
        body["instruction"] = instruction
    if fallback is not None:
        body["fallback"] = bool(fallback)
    if gpt:
        body["gpt"] = gpt
    if edge:
        body["edge"] = edge
    r = requests.post(
        f"{BASE}/api/llm/voice",
        json=body,
        headers=_headers(),
        timeout=timeout,
    )
    if r.status_code in (404, 405, 501):
        raise VoiceAPIUnsupported(f"voice API 非対応（HTTP {r.status_code}）")
    return _unwrap(r, "voice", "llm voice")


class TaskAPIUnsupported(RuntimeError):
    """指揮中心が task queue API を持っていない（古い runtime）。同期呼び出しへ落ちる。"""


def text_with_meta(prompt, timeout=300, opts=None, engine="codex", poll=3):
    """text() と同じ生成に加えて「実際に答えた brain」を返す。

    同期の `/api/llm/text` は `{ok, text}` しか返さない。`engine` は**要求値**で
    あって生成者ではない（codex 混雑時は Chrome pool へ自動 fallback する）。
    task API は `attempts` に brain 名と所要 ms を残すので、どの LLM が書いたかを
    記録したい呼び出しはこちらを通す。

    戻り値: (text, meta)
      meta = {task_id, brain（採用された brain）, brains（試した順）,
              attempts（生データ）, llm_ms, queued_ms}
    """
    body_opts = dict(opts) if opts else {}
    if engine and "engine" not in body_opts:
        body_opts["engine"] = engine
    if body_opts.get("engine") == "codex" and "codexTimeoutMs" not in body_opts:
        body_opts["codexTimeoutMs"] = int(timeout * 1000)
    try:
        task_id = submit("text", prompt, opts=body_opts or None, timeout=30)
    except requests.exceptions.HTTPError as e:
        code = getattr(e.response, "status_code", None)
        if code in (404, 405, 501):
            raise TaskAPIUnsupported(f"task API 非対応（HTTP {code}）") from e
        raise

    # 生成中の gateway は GET にも遅くなる（既定 10s だと実測で ReadTimeout）。
    # 落ちたのは接続であって生成ではないので、poll は通信例外を飲んで回し続ける
    t0 = time.time()
    task = None
    while time.time() - t0 < timeout:
        try:
            task = get_task(task_id, timeout=30)
        except requests.exceptions.RequestException:
            time.sleep(poll)
            continue
        if task.get("status") in ("done", "failed"):
            break
        time.sleep(poll)
    else:
        raise TimeoutError(f"task {task_id} 等待逾時（{timeout}s）")

    if not task or task.get("status") != "done":
        raise RuntimeError((task or {}).get("error") or f"task {task_id} 失敗")

    attempts = task.get("attempts") or []
    ok_brains = [a.get("brain") for a in attempts if a.get("ok")]
    result = task.get("result")
    out = result if isinstance(result, str) else (result or {}).get("text", "")
    meta = {
        "task_id": task_id,
        "brain": ok_brains[-1] if ok_brains else None,
        "brains": [a.get("brain") for a in attempts],
        "attempts": attempts,
        "llm_ms": sum(int(a.get("ms") or 0) for a in attempts),
        "queued_ms": max(0, int((task.get("started_at") or 0)
                                - (task.get("created_at") or 0))),
    }
    return out, meta


def submit(kind, prompt, opts=None, priority=None, timeout=10):
    """入隊一個任務（kind: text|image|codex-content），回 task_id。"""
    body = {"project": PROJECT, "kind": kind, "prompt": prompt}
    if opts:
        body["opts"] = opts
    if priority is not None:
        body["priority"] = priority
    r = requests.post(f"{BASE}/api/llm/task", json=body, headers=_headers(), timeout=timeout)
    r.raise_for_status()
    return r.json()["taskId"]


def get_task(task_id, timeout=10):
    r = requests.get(f"{BASE}/api/llm/task/{task_id}", headers=_headers(), timeout=timeout)
    r.raise_for_status()
    return r.json()["task"]


def wait(task_id, poll=3, max_wait=600):
    """輪詢直到 done/failed，回完整 task dict。逾時 raise TimeoutError。"""
    t0 = time.time()
    while time.time() - t0 < max_wait:
        t = get_task(task_id)
        if t["status"] in ("done", "failed"):
            return t
        time.sleep(poll)
    raise TimeoutError(f"task {task_id} 等待逾時（{max_wait}s）")


if __name__ == "__main__":
    # 煙霧測試：python3 -m tools.miko_llm
    print("available:", is_available())
    if is_available():
        print(text("用一句話自我介紹"))
