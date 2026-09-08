"""GPT 音声化 — ChatGPT に話し言葉へ書き直させ、その読み上げ音声を捕捉する。

edge_tts / macOS say の合成音は「読み上げ」に聞こえる。面接練習用には
**実際に話す速度・間・語尾**が要る。そこで ChatGPT（ブラウザ・本人アカウント）に
話し言葉へ整えさせ、「大聲朗讀」の音声をそのまま音檔にする。

1 メッセージ = 1 音檔なので、既定では 20 問をまとめて 1 メッセージにする。音声取得は
再生速度に縛られない（94 秒の音声が 15 秒で落ちてくる＝実測 0.16 倍）ため、まとめた分
だけ固定オーバーヘッド（生成待ち・メニュー操作・無音判定）が丸ごと減る。

音声の取り方: 画面録音ではなく **MediaSource.appendBuffer を hook して
AAC の生データを直接受け取る**。ChatGPT の読み上げは MediaSource ストリーム
なので、再生されるバイト列＝ここを通る。ループバック音声デバイス（BlackHole 等）も
録画アプリも不要で、環境ノイズ・取りこぼし・タイミングずれが原理的に起きない。

PII: 質問文・回答骨子は外部（ChatGPT）へ出るため、送信前に必ず
tools.pii_gate.scrub_for_external() と tools.redact.redact() を通す。

用法:
    python3 -m tts.gpt_voice --job-id 123                   # 全問（キャッシュ済みは再利用）
    python3 -m tts.gpt_voice --job-id 123 --limit 5         # 先頭 5 問だけ
    python3 -m tts.gpt_voice --job-id 123 --batch-size 10   # 1 メッセージ 10 問に分割
    python3 -m tts.gpt_voice --job-id 123 --force           # キャッシュ無視で録り直し
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from hashlib import sha256
from pathlib import Path

from tools import app_config
from tools.pii_gate import scrub_for_external
from tools.redact import redact

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PREP_DIR = PROJECT_ROOT / "output" / "prep"

# prompt を変えたら音声も作り直す必要がある → キャッシュ鍵に混ぜる
PROMPT_VERSION = 4

DEFAULTS = {
    # 音声化の実行主体。
    #   auto  — miko-ws 指揮中心が生きていればそちら（ChatGPT アカウントを跨いで
    #           自動リトライしてくれる）、居なければ本機の Chrome へ落ちる
    #   miko  — 指揮中心のみ（落ちていれば失敗させる）
    #   local — 本機の Chrome のみ（従来の挙動）
    "backend": "auto",
    "cdp_port": 9261,
    # 1 メッセージにまとめる問数。音声取得は再生速度に縛られない（実測 0.16 倍）ので、
    # まとめるほど 1 問あたりの固定オーバーヘッド（生成待ち・メニュー操作・無音判定）が減る
    "batch_size": 20,
    "user_data_dir": "~/.chrome-chatgpt2",
    # 一時チャットは履歴を残さない代わりに読み上げが一切鳴らない（実測）。
    # 音声が要る以上ここは false 固定に近い。PII は送信前に閘門で落としている。
    "temporary_chat": False,
    "answer_timeout": 120,
    "audio_timeout": 240,
    "silence_seconds": 6,
    # 1 バッチ = 20 問。瞬断（gateway の一時 500・朗讀が始まらない）で 20 問まとめて
    # 失う方が高くつくので、既定で 1 回は録り直す
    "retries": 1,
    # 指揮中心がこの回数連続で失敗したら、残りは本機 Chrome で録る。gateway が落ちた
    # まま全バッチを回すと 1 バッチごとに HTTP timeout ぶん待って全滅する
    "miko_failover_after": 2,
}

# UI 言語に依存しないよう複数表記を並べる（ChatGPT の UI は言語設定で変わる）
RE_MORE_ACTIONS = r"更多動作|更多操作|More actions|その他|もっと見る"
RE_READ_ALOUD = r"大聲朗讀|大声朗读|朗讀|朗读|読み上げ|Read aloud"
RE_STOP_ALOUD = r"停止朗讀|停止朗读|停止読み上げ|Stop"
# 一時チャット初回の説明カード（入力欄を塞ぐ）を閉じるボタン
DIALOG_CONFIRM_LABELS = ("繼續", "继续", "続ける", "Continue", "OK", "了解")


def _describe(exc: BaseException) -> str:
    """例外を日誌 1 行にする。

    型名を落とすと、requests / playwright のようにメッセージが空の例外が
    「✗ 第1〜20問 — 」だけになり、後から原因を辿れない。
    """
    msg = " ".join(str(exc).split())
    return f"{type(exc).__name__}: {msg}" if msg else type(exc).__name__


def _with_retry(fn, retries: int, label: str, log, passthrough=()):
    """失敗したら再試行して (結果, 最後の例外, 試行回数) を返す。

    passthrough に挙げた例外は「再試行しても結果が変わらない」ので即座に投げる。
    """
    last = None
    for attempt in range(retries + 1):
        try:
            return fn(), None, attempt + 1
        except passthrough:
            raise
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < retries:
                log(f"    ↻ {label} 失敗 — 再試行 {attempt + 1}/{retries}: {_describe(exc)}")
                time.sleep(3)
    return None, last, retries + 1


def config() -> dict:
    cfg = dict(DEFAULTS)
    cfg.update(app_config.get("tts", "gpt_voice", {}) or {})
    return cfg


# ---------------------------------------------------------------- prompt

SYSTEM_PRIMER = (
    "これから日本語の面接練習用の音声原稿を作ります。以降、私が送る各メッセージは"
    "「質問」と「回答の骨子」です。あなたは骨子の内容だけを使い、面接本番で実際に"
    "口に出す話し言葉へ整えて出力してください。ルール:\n"
    "1. 出力は読み上げる本文だけ。前置き・見出し・箇条書き・記号・絵文字は書かない。\n"
    "2. 3〜5 文、45 秒以内で話し切れる長さ。\n"
    "3. 骨子が主軸。数字・実績・会社名は骨子に書かれたものだけを使う。\n"
    "4. 文をつなぐための補足は構わないが、私が実際に経験した範囲を出ないこと。"
    "裏を取れないことは、正しそうに見えても書かない。\n"
    "5. 文語ではなく話し言葉。「〜ます」「〜です」で、一文は短く。\n"
    "6. 冒頭で結論を言い、そのあと理由や具体例を続ける。\n"
    "最初のメッセージには「了解しました」とだけ返してください。"
)

# 一問一答ではなく「用意した原稿を読み上げる」用途（提案パックの 5 分ピッチ等）。
# 書き直させる幅を SYSTEM_PRIMER より狭く取る — 原稿側は既に閘門を通っており、
# ここで内容を作り変えられると数字錨定・PII 検査を通った意味が無くなる。
SCRIPT_PRIMER = (
    "これから日本語のプレゼン音声の原稿を作ります。以降、私が送る各メッセージは"
    "**すでに書き上がった原稿**です。あなたの仕事は内容を変えることではなく、"
    "声に出したときに自然に聞こえる形へ整えることだけです。ルール:\n"
    "1. 出力は読み上げる本文だけ。前置き・見出し・箇条書き・記号・絵文字は書かない。\n"
    "2. 事実・数字・固有名詞・主張は一切変えない。足さない。減らさない。\n"
    "3. 書き言葉の体言止めや記号は、話し言葉の文へ直す"
    "（「〜の整備」→「〜を整えます」など）。\n"
    "4. 表や箇条書きは、順に話す文へ開く。項目の数と順序は変えない。\n"
    "5. 一文は短く、「〜です」「〜ます」で終える。\n"
    "6. 元の原稿に無いことは、つなぎの言葉であっても書かない。\n"
    "最初のメッセージには「了解しました」とだけ返してください。"
)

# 骨子に無くても「捏造」ではない一般語（職種・工程の普通名詞）
GENERIC_TERMS = {
    "pdm", "pm", "po", "pjm", "api", "ui", "ux", "uat", "prd", "qa", "kpi", "okr",
    "saas", "ai", "llm", "it", "ec", "b2b", "b2c", "crm", "erp", "sier", "mvp",
    "web", "app", "sns", "db", "sql", "ok", "pdca", "roi",
}
_NUM_RE = re.compile(r"[0-9０-９]+")
_ALNUM_TERM_RE = re.compile(r"[A-Za-zＡ-Ｚａ-ｚ][A-Za-z0-9Ａ-Ｚａ-ｚ０-９]*")


def _norm(text: str) -> str:
    return re.sub(r"[\s　・，,．.]", "", text).lower()


@lru_cache(maxsize=1)
def profile_corpus() -> str:
    """本人の経歴ファイル全文（照合用・本機内のみ、外部へは送らない）。

    ChatGPT 側の記憶やカスタム指示から補われた語でも、経歴ファイルに載っていれば
    それは事実。骨子だけを基準にすると本当の経歴まで弾いてしまう。
    """
    parts = []
    for rel in ("data/candidate_profile.yaml", "resume/jp/data.yaml"):
        path = PROJECT_ROOT / rel
        if path.exists():
            parts.append(path.read_text(encoding="utf-8"))
    return _norm("\n".join(parts))


def unanchored_terms(spoken: str, source: str, corpus: str | None = None) -> list[str]:
    """原稿のうち、骨子にも経歴ファイルにも無い数字・固有名詞を返す。

    ChatGPT はアカウントの記憶から情報を足してくる。足された内容が本人の経歴なら
    問題ないが、そうでない場合は面接で口に出す原稿に嘘が混ざる。両方に無いものだけを
    「要確認」として挙げる。
    """
    src = _norm(source) + (profile_corpus() if corpus is None else _norm(corpus))
    bad: set[str] = set()
    for num in _NUM_RE.findall(spoken):
        if num.lower() not in src:
            bad.add(num)
    for term in _ALNUM_TERM_RE.findall(spoken):
        if len(term) < 2 or term.lower() in GENERIC_TERMS:
            continue
        if term.lower() not in src:
            bad.add(term)
    return sorted(bad)


def rewrite_prompt(bad_terms: list[str]) -> str:
    return ("今の原稿には、私が渡した骨子にも私の経歴にも無い語が含まれています: "
            + "、".join(bad_terms)
            + "。これらを完全に削除し、骨子に書かれた事実だけで書き直してください。"
              "出力は読み上げる本文だけ。")


def build_prompt(question: str, conclusion: str, points: list[str]) -> str:
    """1 問分の依頼文。外部送信するので呼び出し側で PII 閘門を通すこと。"""
    body = "\n".join(f"- {p}" for p in ([conclusion] if conclusion else []) + points)
    return f"質問: {question}\n回答の骨子:\n{body}"


def build_batch_prompt(pairs: list[tuple[int, str]]) -> str:
    """複数問を 1 メッセージにまとめる。

    読み上げは 1 メッセージ = 1 音檔なので、まとめた分だけ往復（生成待ち・メニュー
    操作・無音判定）が減る。音声の取得自体は再生速度に縛られない（実測 0.16 倍）ため、
    まとめても総時間はほぼ音声長ぶんしか増えない。

    番号は「第N問。」で読み上げさせる — 聞いていて位置が分かり、機械分割もできる。
    """
    head = (f"次の {len(pairs)} 問を、それぞれ話し言葉の原稿に整えてください。\n"
            "各問の冒頭に必ず「第N問。」と書き、そのあとに本文を続けてください"
            "（Nは問番号）。問と問の間は空行 1 つ。番号と本文以外は書かないでください。\n")
    body = "\n\n".join(f"第{n}問。\n{prompt}" for n, prompt in pairs)
    return head + "\n" + body


_BATCH_SPLIT_RE = re.compile(r"第\s*([0-9０-９]+)\s*問[。．.、:：]?\s*")


def parse_batch_reply(text: str, ordinals: list[int]) -> dict[int, str]:
    """まとめ回答 → 問番号ごとの原稿。取れなかった問は欠番のまま返す。"""
    parts = _BATCH_SPLIT_RE.split(text)
    out: dict[int, str] = {}
    for i in range(1, len(parts) - 1, 2):
        num = int(parts[i].translate(str.maketrans("０１２３４５６７８９", "0123456789")))
        body = parts[i + 1].strip()
        if num in ordinals and body:
            out[num] = body
    return out


def scrub(text: str) -> tuple[str, list[str]]:
    """外部送信前の二段閘門（個人 PII → 取引先ブランド名）。"""
    clean, pii = scrub_for_external(text)
    clean, brands = redact(clean)
    return clean, pii + brands


def item_hash(question: str, conclusion: str, points: list[str]) -> str:
    raw = f"{PROMPT_VERSION}|{question}|{conclusion}|{'|'.join(points)}"
    return sha256(raw.encode("utf-8")).hexdigest()[:12]


def batch_hash(hashes: list[str]) -> str:
    return sha256("|".join(hashes).encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------- browser

# 読み上げの実体は MediaSource ストリーム。appendBuffer を包んで生バイトを溜める。
_HOOK_JS = r"""() => {
  if (window.__gv) return 'already';
  window.__gv = {chunks: [], mime: '', done: false, ts: 0, err: ''};
  const b64 = (u8) => { let s = ''; const C = 8192;
    for (let i = 0; i < u8.length; i += C) s += String.fromCharCode.apply(null, u8.subarray(i, i + C));
    return btoa(s); };
  const oAdd = MediaSource.prototype.addSourceBuffer;
  MediaSource.prototype.addSourceBuffer = function (mime) {
    const sb = oAdd.call(this, mime);
    if (/audio/i.test(mime || '')) {
      window.__gv.mime = mime;
      const oApp = sb.appendBuffer.bind(sb);
      sb.appendBuffer = function (buf) {
        try {
          const u8 = buf instanceof ArrayBuffer ? new Uint8Array(buf)
            : new Uint8Array(buf.buffer, buf.byteOffset, buf.byteLength);
          window.__gv.chunks.push(b64(u8));
          window.__gv.ts = Date.now();
        } catch (e) { window.__gv.err = String(e); }
        return oApp(buf);
      };
    }
    return sb;
  };
  const oEnd = MediaSource.prototype.endOfStream;
  MediaSource.prototype.endOfStream = function (...a) {
    window.__gv.done = true; return oEnd.apply(this, a);
  };
  return 'hooked';
}"""

_RESET_JS = "() => { if (window.__gv) { window.__gv.chunks = []; window.__gv.done = false; window.__gv.ts = Date.now(); } }"

# メニュー項目は Radix 製で、playwright の click は「スクロールして可視化」で詰まる
# ことがある（長い回答ほど起きやすい）。pointer 系イベントを直接投げる方が安定。
# 優先は data-testid、無ければ表示文字で拾う。
_CLICK_READ_ALOUD_JS = """(re) => {
  const rx = new RegExp(re);
  const items = [...document.querySelectorAll('[role="menuitem"]')];
  const el = document.querySelector('[data-testid="voice-play-turn-action-button"]')
    || items.find(e => rx.test(e.innerText || ''));
  if (!el) return false;
  const opts = {bubbles: true, cancelable: true, composed: true,
                pointerId: 1, isPrimary: true, button: 0, buttons: 1};
  el.dispatchEvent(new PointerEvent('pointerdown', opts));
  el.dispatchEvent(new MouseEvent('mousedown', opts));
  el.dispatchEvent(new PointerEvent('pointerup', {...opts, buttons: 0}));
  el.dispatchEvent(new MouseEvent('mouseup', {...opts, buttons: 0}));
  el.dispatchEvent(new MouseEvent('click', {...opts, buttons: 0}));
  return true;
}"""

_OPEN_MENU_JS = """(re) => {
  const rx = new RegExp(re);
  const bs = [...document.querySelectorAll('button')].filter(b => rx.test(b.getAttribute('aria-label') || ''));
  if (!bs.length) return false;
  bs[bs.length - 1].click();
  return true;
}"""


class ChatGPTVoice:
    """CDP 経由で既ログインの ChatGPT を操り、1 問ずつ「整形 → 読み上げ捕捉」。"""

    def __init__(self, cfg: dict | None = None, log=print):
        self.cfg = cfg or config()
        self.log = log
        self._pw = None
        self._browser = None
        self.page = None
        self._proc: subprocess.Popen | None = None

    # -- lifecycle ------------------------------------------------

    def _chrome_bin(self) -> str:
        return app_config.get(
            "scraping", "chrome_bin",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")

    @staticmethod
    def _port_open(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(1.0)
            return sock.connect_ex(("127.0.0.1", port)) == 0

    def _ensure_chrome(self) -> None:
        """CDP が繋がる Chrome を用意する。

        固定 sleep で「起動したはず」と決めると、実際の失敗（プロファイル二重使用
        など）が後段の playwright 例外として出て、原因と無関係な文言になる。
        port が開くまで待ち、開かなければここで理由付きで落とす。
        """
        port = int(self.cfg["cdp_port"])
        if self._port_open(port):
            self.log(f"  既存 Chrome 再利用 (port={port})")
            return
        profile = str(Path(self.cfg["user_data_dir"]).expanduser())
        self._proc = subprocess.Popen(
            [self._chrome_bin(), f"--remote-debugging-port={port}",
             f"--user-data-dir={profile}", "--no-first-run",
             "--no-default-browser-check", self._start_url()],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(20):
            time.sleep(1.0)
            if self._port_open(port):
                return
            if self._proc.poll() is not None:
                break
        raise RuntimeError(
            f"Chrome の CDP port {port} が開きません（profile={profile} / "
            f"終了コード={self._proc.poll()}）— 同じプロファイルを別の Chrome が"
            "使っていると、起動しても debugging port は開かない。その Chrome を"
            "閉じてから再実行するか、指揮中心（backend: miko）を使う")

    def _start_url(self) -> str:
        return ("https://chatgpt.com/?temporary-chat=true"
                if self.cfg.get("temporary_chat", False) else "https://chatgpt.com/")

    def open(self) -> None:
        from playwright.sync_api import sync_playwright
        self._ensure_chrome()
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.connect_over_cdp(
            f"http://127.0.0.1:{self.cfg['cdp_port']}")
        ctx = self._browser.contexts[0] if self._browser.contexts else self._browser.new_context()
        self.page = next((p for p in ctx.pages if "chatgpt.com" in p.url), None) or ctx.new_page()
        self.page.goto(self._start_url(), wait_until="domcontentloaded")
        try:
            # 入力欄が出れば「ログイン済み・描画完了」の両方が同時に確認できる
            self.page.wait_for_selector("#prompt-textarea", timeout=30000)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "ChatGPT の入力欄が出ません（未ログインの可能性）— "
                f"{self.cfg['user_data_dir']} のプロファイルでログインしてから再実行") from exc
        time.sleep(1.5)
        self._dismiss_overlays()
        self.page.evaluate(_HOOK_JS)

    def _dismiss_overlays(self) -> None:
        """一時チャットの初回説明カード等を閉じる（入力欄を塞ぐため）。

        JS の .click() では React が反応しないので playwright の force click を使う。
        """
        for name in DIALOG_CONFIRM_LABELS:
            loc = self.page.get_by_role("button", name=name, exact=True)
            if loc.count():
                loc.first.click(force=True)
                time.sleep(1.0)

    def close(self) -> None:
        try:
            if self._browser:
                self._browser.close()
        finally:
            if self._pw:
                self._pw.stop()

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    # -- chat -----------------------------------------------------

    def _assistant_count(self) -> int:
        return self.page.locator("[data-message-author-role='assistant']").count()

    def ask(self, prompt: str) -> str:
        """1 メッセージ送って、回答が止まるまで待ち、本文を返す。"""
        before = self._assistant_count()
        # click() は初回の案内オーバーレイに邪魔されることがある → focus() で直接入れる
        self.page.keyboard.press("Escape")
        if not self.page.evaluate(
                "() => { const el = document.querySelector('#prompt-textarea');"
                " if (!el) return false; el.focus(); return true; }"):
            raise RuntimeError("入力欄 (#prompt-textarea) が見つかりません — UI 変更の可能性")
        # 前回の打ち残しが混ざると全文が壊れる → 送る前に必ず空にする
        if self.page.locator("#prompt-textarea").inner_text().strip():
            self.page.keyboard.press("ControlOrMeta+A")
            self.page.keyboard.press("Backspace")
        # 改行入りの原稿は type() だと途中送信になる → 行ごとに Shift+Enter
        for i, line in enumerate(prompt.split("\n")):
            if i:
                self.page.keyboard.press("Shift+Enter")
            self.page.keyboard.type(line)
        time.sleep(0.3)
        self.page.keyboard.press("Enter")

        deadline = time.time() + int(self.cfg["answer_timeout"])
        stable, last = 0, ""
        while time.time() < deadline:
            time.sleep(1.5)
            if self._assistant_count() <= before:
                continue
            streaming = self.page.locator("button[data-testid='stop-button']").count() > 0
            text = self.page.locator("[data-message-author-role='assistant']").last.inner_text()
            if not streaming and text and text == last:
                stable += 1
                if stable >= 2:
                    return text.strip()
            else:
                stable = 0
            last = text
        streaming = self.page.locator("button[data-testid='stop-button']").count() > 0
        raise TimeoutError(
            f"ChatGPT の回答が {int(self.cfg['answer_timeout'])}s 以内に完了しません"
            f"（新規回答={self._assistant_count() > before} / 受信 {len(last)} 字 / "
            f"生成中={streaming}）")

    # -- read aloud -----------------------------------------------

    def read_aloud(self) -> tuple[bytes, str]:
        """最後の回答を読み上げさせ、そのストリームを生バイトで受け取る。"""
        page = self.page
        page.keyboard.press("Escape")
        time.sleep(0.4)
        # ページ遷移・再描画で window.__gv は消える。消えたまま待つと必ず
        # 「音声が届かない」で落ちるので、毎回 hook の生存を確かめて張り直す
        if not page.evaluate("() => !!window.__gv"):
            self.log("    ↻ MediaSource hook が消えていたので張り直します")
            page.evaluate(_HOOK_JS)
        page.evaluate(_RESET_JS)
        if not page.evaluate(_OPEN_MENU_JS, RE_MORE_ACTIONS):
            labels = page.evaluate(
                "() => [...document.querySelectorAll('button')]"
                ".map(b => b.getAttribute('aria-label')).filter(Boolean).slice(-12)")
            raise RuntimeError(
                f"回答メニュー（更多動作）のボタンが見つかりません — UI 変更の可能性"
                f"（末尾の aria-label: {labels}）")
        time.sleep(1.2)
        if not page.evaluate(_CLICK_READ_ALOUD_JS, RE_READ_ALOUD):
            names = page.evaluate(
                "() => [...document.querySelectorAll('[role=\"menuitem\"]')].map(e => e.innerText)")
            raise RuntimeError(f"読み上げメニューが見つかりません（menu: {names}）")

        start = time.time()
        deadline = start + int(self.cfg["audio_timeout"])
        silence = float(self.cfg["silence_seconds"])
        started = False
        while time.time() < deadline:
            time.sleep(1.0)
            st = page.evaluate("() => ({n: window.__gv.chunks.length, done: window.__gv.done, ts: window.__gv.ts})")
            if st["n"]:
                started = True
                # endOfStream か、最後の chunk から silence 秒無音なら再生終了
                if st["done"] or (time.time() * 1000 - st["ts"]) / 1000 > silence:
                    break
            elif time.time() - start > 30:
                break  # 30 秒経っても 1 byte も来ない = 朗讀が始まっていない
        if not started:
            raise RuntimeError(
                f"読み上げ音声が届きませんでした（{int(time.time() - start)}s 待機・"
                f"朗讀が始まらなかった / {self._hook_state(page)}）")
        time.sleep(1.0)
        chunks = page.evaluate("() => window.__gv.chunks")
        mime = page.evaluate("() => window.__gv.mime") or "audio/aac"
        page.keyboard.press("Escape")
        data = b"".join(base64.b64decode(c) for c in chunks)
        if not data:
            raise RuntimeError(f"音声データが空です（chunk {len(chunks)} 個 / "
                               f"{self._hook_state(page)}）")
        return data, mime

    @staticmethod
    def _hook_state(page) -> str:
        """hook 側の状態を 1 行に。`__gv.err`（base64 変換失敗）はここでしか見えない。"""
        try:
            st = page.evaluate(
                "() => window.__gv ? {hooked: true, n: window.__gv.chunks.length,"
                " mime: window.__gv.mime, done: window.__gv.done, err: window.__gv.err}"
                " : {hooked: false}")
        except Exception as exc:  # noqa: BLE001
            return f"hook 状態を取得できず: {_describe(exc)}"
        if not st.get("hooked"):
            return "hook 消失（ページが再読み込みされた可能性）"
        return (f"hook 生存 / chunk {st.get('n')} / mime={st.get('mime') or '未取得'}"
                f" / endOfStream={st.get('done')}"
                + (f" / hook エラー: {st['err']}" if st.get("err") else ""))


# ---------------------------------------------------------------- files

def ext_for(mime: str) -> str:
    if "mp4" in mime or "m4a" in mime:
        return "m4a"
    if "mpeg" in mime or "mp3" in mime:
        return "mp3"
    return "aac"


def to_wav(src: Path, dst: Path) -> None:
    try:
        proc = subprocess.run(
            ["ffmpeg", "-y", "-i", str(src), "-ar", "24000", "-ac", "1", str(dst)],
            capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        raise RuntimeError("ffmpeg が見つかりません（brew install ffmpeg）— "
                           "音声は録れたが wav に変換できない") from None
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"wav 変換が 120s で終わりません: {src.name}") from None
    if proc.returncode != 0:
        raise RuntimeError(f"wav 変換失敗（{src.name}）: {proc.stderr[-300:]}")


def voice_dir(pack_dir: Path, track: str = "") -> Path:
    """音檔置き場。track を分けるのは `_prune_orphans` が同居を許さないため。

    1 パックから複数の音檔（提案パックの pitch / qa / cards）を録るとき、
    同じディレクトリに置くと manifest に載らない他 track の wav を「古い音檔」
    として消し合う。track ごとに部屋を分ける（既定の "" は面接パック用で、
    従来どおり `gpt_voice/` 直下）。
    """
    base = pack_dir / "gpt_voice"
    return base / track if track else base


def load_manifest(pack_dir: Path, track: str = "") -> dict:
    path = voice_dir(pack_dir, track) / "manifest.json"
    if not path.exists():
        return {"version": 1, "items": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_manifest(pack_dir: Path, manifest: dict, track: str = "") -> None:
    (voice_dir(pack_dir, track) / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


@dataclass
class VoiceResult:
    total: int          # 問数
    done: int           # 今回録ったバッチ数
    cached: int         # 再利用したバッチ数
    failed: int         # 失敗したバッチ数
    pii_findings: int
    wav_files: list[Path]
    degraded: int = 0   # 書き直しても裏が取れない語が残った問数
    batches: int = 0
    # 失敗したバッチの理由（呼び出し側が要約を出せるように — 端末のスクロールに
    # 流れて消える print だけでは、どのバッチがなぜ録れなかったのか残らない）
    errors: list[dict] = field(default_factory=list)


@dataclass
class Batch:
    """1 メッセージ = 1 音檔の単位。"""
    ordinals: list[int]
    questions: list[str]
    prompts: list[str]      # PII 閘門通過済み（外部送信するのはこれ）
    hash: str
    wav: Path

    @property
    def label(self) -> str:
        return (f"第{self.ordinals[0]}〜{self.ordinals[-1]}問"
                if len(self.ordinals) > 1 else f"第{self.ordinals[0]}問")


def _speak_batch(session: "ChatGPTVoice", batch: Batch, log=print
                 ) -> tuple[dict[int, str], dict[int, list[str]]]:
    """1 バッチを話し言葉化。裏が取れない語が出たら 1 回だけ書き直させる。

    戻り値は (問番号→原稿, 問番号→残った未錨定語)。未錨定語が残ったまま返すことも
    ある（音声は作るが degraded として監査に出す — 黙って捨てる方が危ない）。
    """
    pairs = list(zip(batch.ordinals, batch.prompts))
    reply = session.ask(build_batch_prompt(pairs))
    spoken = parse_batch_reply(reply, batch.ordinals)
    if len(batch.ordinals) == 1 and not spoken:
        spoken = {batch.ordinals[0]: reply}   # 番号を書き忘れても 1 問なら曖昧さはない
    _require_parsed(spoken, batch, reply)

    bad = {n: unanchored_terms(text, dict(pairs)[n]) for n, text in spoken.items()}
    bad = {n: terms for n, terms in bad.items() if terms}
    if bad:
        flat = sorted({t for terms in bad.values() for t in terms})
        log(f"    ↻ {batch.label} 骨子にも経歴にも無い語 {flat} → 書き直し")
        try:
            reply = session.ask(rewrite_prompt(flat) + "\n全問を同じ形式で出し直してください。")
        except Exception as exc:  # noqa: BLE001
            # 初稿は使える。ここで落とすと 20 問ぶんの原稿を書き直しの失敗だけで失う
            log(f"    ⚠ {batch.label} 書き直しに失敗（{_describe(exc)}）— "
                "初稿のまま録り、未錨定語は監査に残す")
            return spoken, bad
        spoken = parse_batch_reply(reply, batch.ordinals) or spoken
        bad = _anchor_check(spoken, pairs)
    return spoken, bad


def _batch_record(b: Batch, spoken: dict[int, str], bad: dict[int, list[str]], *,
                  raw: str, mime: str, size: int, engine: str, account: str = "") -> dict:
    """manifest 1 行分。ローカル Chrome でも指揮中心でも同じ形にする。"""
    return {
        "hash": b.hash, "ordinals": b.ordinals, "first_ordinal": b.ordinals[0],
        "questions": b.questions,
        "spoken": {str(n): t for n, t in sorted(spoken.items())},
        "unanchored": {str(n): t for n, t in sorted(bad.items())},
        "unparsed": [n for n in b.ordinals if n not in spoken],
        "raw": raw, "wav": b.wav.name, "mime": mime, "bytes": size,
        "engine": engine, "account": account,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


# ---------------------------------------------------------------- miko-ws backend

def _miko_ready(log=print) -> bool:
    """指揮中心（miko-ws）が使えるか。

    生成中の gateway は GET も遅くなる（実測）。1 回の空振りで本機 Chrome へ
    落とすと、指揮中心が生きているのに単一アカウント経路で録ってしまうので、
    タイムアウトを伸ばしてもう一度だけ聞く。
    """
    from tools import miko_llm

    last = None
    for timeout in (5, 20):
        try:
            if miko_llm.health(timeout=timeout).get("ok"):
                return True
        except Exception as exc:  # noqa: BLE001
            last = exc
    log(f"  指揮中心に届きません（{miko_llm.BASE}: {_describe(last) if last else '応答 ok=false'}）")
    return False


def pick_backend(cfg: dict | None = None, log=print) -> str:
    """設定と指揮中心の生死から実行主体を決める。"""
    want = (cfg or config()).get("backend", "auto")
    if want == "local":
        return "local"
    if want == "miko":
        return "miko"
    return "miko" if _miko_ready(log) else "local"


def merge_primer(primer: str) -> str:
    """primer を依頼と同じメッセージに混ぜるとき、末尾の「了解しました」指示を外す。

    primer は元々「1 通目＝挨拶、2 通目＝依頼」の 2 ターン用に書かれている。
    そのまま混ぜると ChatGPT は最後の指示だけを実行し「了解しました」だけ返す
    （＝原稿が 1 問も返らず、1 秒の音檔ができる。実測）。
    """
    kept = [ln for ln in primer.split("\n") if "最初のメッセージには" not in ln]
    return "\n".join(kept).strip()


def _require_parsed(spoken: dict[int, str], b: Batch, reply: str) -> None:
    """1 問も取り出せない返答は「音声にしてはいけない」— 内容の照合も分割もできない。

    ここで失敗させないと、挨拶だけの 1 秒音檔がキャッシュに載り、二度と録り直されない。
    """
    if not spoken:
        head = " ".join(reply.split())[:80]
        raise RuntimeError(f"{b.label}: 原稿を 1 問も取り出せません（返答冒頭: {head!r}）")


def _anchor_check(spoken: dict[int, str], pairs: list[tuple[int, str]]) -> dict[int, list[str]]:
    src = dict(pairs)
    bad = {n: unanchored_terms(t, src.get(n, "")) for n, t in spoken.items()}
    return {n: terms for n, terms in bad.items() if terms}


def _speak_batch_miko(b: Batch, primer: str, log=print) -> tuple[dict[int, str], dict[int, list[str]], dict]:
    """1 バッチを指揮中心（miko-ws）で音声化。ChatGPT アカウントの切り替えは向こう持ち。

    instruction 付きで投げる＝「骨子を話し言葉へ整えてから読め」なので、指揮中心は
    合成音（edge-tts）へは落とさない — 依頼文をそのまま読み上げた音檔を作らないため。
    """
    from tools import miko_llm

    pairs = list(zip(b.ordinals, b.prompts))
    wav = b.wav.resolve()   # 指揮中心は別プロセス — 相対パスでは書き先が変わる
    res = miko_llm.voice(build_batch_prompt(pairs), wav, instruction=merge_primer(primer))
    reply = res.get("spoken") or ""
    spoken = parse_batch_reply(reply, b.ordinals)
    if len(b.ordinals) == 1 and not spoken and reply.strip():
        spoken = {b.ordinals[0]: reply.strip()}   # 番号を書き忘れても 1 問なら曖昧さはない
    _require_parsed(spoken, b, reply)

    bad = _anchor_check(spoken, pairs)
    if bad:
        flat = sorted({t for terms in bad.values() for t in terms})
        log(f"    ↻ {b.label} 骨子にも経歴にも無い語 {flat} → 書き直し")
        body, _ = scrub(reply)
        try:
            res = miko_llm.voice(
                body, wav,
                instruction=rewrite_prompt(flat) + "\n全問を同じ「第N問。」形式で出し直してください。")
        except Exception as exc:  # noqa: BLE001
            # 初稿の音檔は既に wav にある。書き直しの失敗で 20 問ぶん捨てない
            log(f"    ⚠ {b.label} 書き直しに失敗（{_describe(exc)}）— "
                "初稿のまま録り、未錨定語は監査に残す")
            return spoken, bad, res
        reply = res.get("spoken") or reply
        spoken = parse_batch_reply(reply, b.ordinals) or spoken
        bad = _anchor_check(spoken, pairs)
    return spoken, bad, res


def plan_batches(items: list, out_dir: Path, batch_size: int) -> list[Batch]:
    """問を batch_size ごとに束ね、PII 閘門を通した prompt を持たせる。"""
    batches: list[Batch] = []
    for start in range(0, len(items), max(1, batch_size)):
        group = list(enumerate(items[start:start + max(1, batch_size)], start=start + 1))
        prompts, hashes = [], []
        for _, item in group:
            prompt, _ = scrub(build_prompt(item.question, item.conclusion, item.points))
            prompts.append(prompt)
            hashes.append(item_hash(item.question, item.conclusion, item.points))
        h = batch_hash(hashes)
        ordinals = [n for n, _ in group]
        batches.append(Batch(
            ordinals=ordinals,
            questions=[item.question for _, item in group],
            prompts=prompts,
            hash=h,
            wav=out_dir / f"{ordinals[0]:03d}-{h}.wav",
        ))
    return batches


def _count_pii(items: list) -> int:
    total = 0
    for item in items:
        _, findings = scrub(build_prompt(item.question, item.conclusion, item.points))
        total += len(findings)
    return total


def generate(pack_dir: Path, *, limit: int | None = None, force: bool = False,
             batch_size: int | None = None, log=print) -> VoiceResult:
    """面接パックの想定問答 → GPT 話し言葉 → 読み上げ音声（aac + wav）。

    取材（QA md の解析）だけがここの仕事で、録音そのものは `generate_items()`。
    提案パックなど別形状の文書は、自前で items を組み立ててそちらを直接呼ぶ。
    """
    from tts.theater import parse_standard_qa, _source_path

    src = _source_path(pack_dir)
    if src is None:
        raise FileNotFoundError(f"{pack_dir.name}: 想定問答 md が見つかりません")
    items = parse_standard_qa(src.read_text(encoding="utf-8"))
    if not items:
        raise ValueError(f"{src.name}: 標準一問一答フォーマットとして読めません")
    return generate_items(pack_dir, items, limit=limit, force=force,
                          batch_size=batch_size, log=log)


def generate_items(pack_dir: Path, items: list, *, track: str = "",
                   primer: str = "", audit_path: Path | None = None,
                   limit: int | None = None, force: bool = False,
                   batch_size: int | None = None, log=print) -> VoiceResult:
    """items（`.question` / `.conclusion` / `.points` を持つ任意の列）を音声化する。

    batch_size 件ずつ 1 メッセージにまとめる（既定は config の batch_size）。
    音声取得は再生速度に縛られないので、まとめるほど固定オーバーヘッドが減る。

    track を渡すと音檔・manifest が `gpt_voice/{track}/` に分かれる（1 パックから
    複数の音檔を録るとき、互いの古い音檔を消し合わないため）。primer は
    ChatGPT への最初の指示 — 一問一答なら既定の SYSTEM_PRIMER、原稿の読み上げ
    なら SCRIPT_PRIMER のように、素材の形に合わせて差し替える。
    """
    if not items:
        raise ValueError("音声化する項目が 0 件")
    if limit:
        items = items[:limit]
    cfg = config()
    if batch_size is None:
        batch_size = int(cfg.get("batch_size", 20))
    retries = max(0, int(cfg.get("retries", 1)))

    out_dir = voice_dir(pack_dir, track)
    out_dir.mkdir(parents=True, exist_ok=True)
    old = {it["hash"]: it for it in load_manifest(pack_dir, track).get("items", [])}

    batches = plan_batches(items, out_dir, batch_size)
    todo = [b for b in batches
            if force or b.hash not in old or not b.wav.exists()]
    cached = len(batches) - len(todo)   # todo は途中で削るので件数はここで確定させる
    log(f"  対象 {len(items)} 問 / {len(batches)} バッチ"
        f"（録音 {len(todo)} / キャッシュ {cached}）")

    records: dict[str, dict] = {}
    recorded: set[str] = set()          # 今回録れたバッチ
    failures: dict[str, dict] = {}      # hash → 失敗理由（成功したら消える）
    for b in batches:                      # キャッシュ分を先に引き継ぐ
        if b.hash in old and b.wav.exists() and b not in todo:
            records[b.hash] = old[b.hash]

    def _save() -> None:
        save_manifest(pack_dir, {
            "version": 2,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "items": sorted(records.values(), key=lambda r: r["first_ordinal"]),
            # 失敗も manifest に残す。成功しか書かないと、跑り終わって空の manifest
            # を見ても「なぜ 1 件も録れていないのか」が分からない（実測）
            "failures": sorted(failures.values(), key=lambda f: f["first_ordinal"]),
        }, track)

    def _flush(rec: dict, b: Batch) -> None:
        records[b.hash] = rec
        recorded.add(b.hash)
        failures.pop(b.hash, None)     # 別 backend で録り直せたら失敗記録は消す
        bad_terms = sorted({t for v in rec["unanchored"].values() for t in v})
        log(f"  {'⚠' if bad_terms else '✓'} {b.label} — {rec['bytes'] // 1024}KB"
            + (f" @{rec['account']}" if rec.get("account") else "")
            + (f" / 未錨定 {bad_terms}" if bad_terms else "")
            + (f" / 原稿を取れず {rec['unparsed']}" if rec["unparsed"] else ""))
        _save()

    def _fail(b: Batch, exc: BaseException, backend: str, *,
              stage: str = "batch", attempts: int = 1) -> None:
        failures[b.hash] = {
            "hash": b.hash, "ordinals": b.ordinals, "first_ordinal": b.ordinals[0],
            "questions": b.questions, "backend": backend, "stage": stage,
            "attempts": attempts, "error": _describe(exc),
            "failed_at": datetime.now().isoformat(timespec="seconds"),
        }
        log(f"  ✗ {b.label} — [{backend}/{stage}] {_describe(exc)}"
            + (f"（{attempts} 回試行）" if attempts > 1 else ""))
        _save()

    if todo:
        primer_text, _ = scrub(primer or SYSTEM_PRIMER)
        want = cfg.get("backend", "auto")
        backend = pick_backend(cfg, log=log)
        log(f"  音声化: {'miko-ws 指揮中心（ChatGPT アカウント自動切替）' if backend == 'miko' else '本機 Chrome'}")

        if backend == "miko":
            from tools.miko_llm import VoiceAPIUnsupported

            streak = 0
            failover_after = max(1, int(cfg.get("miko_failover_after", 2)))
            for b in todo:
                try:
                    out, exc, tries = _with_retry(
                        lambda b=b: _speak_batch_miko(b, primer_text, log=log),
                        retries, b.label, log, passthrough=(VoiceAPIUnsupported,))
                except VoiceAPIUnsupported as unsupported:
                    # 古い runtime（voice 能力がまだ無い）→ 残り全部を本機 Chrome で録る
                    log(f"  指揮中心に voice API がありません（{unsupported}）→ 本機 Chrome へ切替")
                    backend = "local"
                    break
                if exc is None:
                    spoken, bad, res = out
                    _flush(_batch_record(
                        b, spoken, bad,
                        raw=Path(res.get("raw") or b.wav).name,
                        mime=res.get("mime", ""), size=int(res.get("bytes") or 0),
                        engine=res.get("engine", "gpt"), account=res.get("account", "")), b)
                    streak = 0
                    continue
                _fail(b, exc, "miko", attempts=tries)
                streak += 1
                # gateway ごと落ちている場合、残りも同じだけ待って全滅する。
                # backend: miko を明示指定していないなら本機 Chrome へ切り替える
                if streak >= failover_after and want != "miko":
                    log(f"  指揮中心が {streak} 連続失敗 → 残りは本機 Chrome で録り直す")
                    backend = "local"
                    break
            todo = [b for b in todo if b.hash not in recorded] if backend == "local" else []

        if backend == "local" and todo:
            try:
                with ChatGPTVoice(cfg, log=log) as session:
                    session.ask(primer_text)
                    for b in todo:
                        def _record(b=b) -> dict:
                            spoken, bad = _speak_batch(session, b, log=log)
                            data, mime = session.read_aloud()
                            raw = out_dir / f"{b.ordinals[0]:03d}-{b.hash}.{ext_for(mime)}"
                            raw.write_bytes(data)
                            to_wav(raw, b.wav)
                            return _batch_record(b, spoken, bad, raw=raw.name,
                                                 mime=mime, size=len(data), engine="gpt")

                        rec, exc, tries = _with_retry(_record, retries, b.label, log)
                        if exc is None:
                            _flush(rec, b)
                        else:
                            _fail(b, exc, "local", attempts=tries)
            except Exception as exc:  # noqa: BLE001
                # open() / primer の失敗＝残り全部録れない。ここで raise すると
                # 呼び出し側（prep.py の voice stage）が落ちて theater 備援も動かない
                log(f"  ✗ 本機 Chrome のセッションを開始できません — {_describe(exc)}")
                for b in todo:
                    if b.hash not in recorded and b.hash not in failures:
                        _fail(b, exc, "local", stage="session")

    manifest_items = sorted(records.values(), key=lambda r: r["first_ordinal"])
    fail_list = sorted(failures.values(), key=lambda f: f["first_ordinal"])
    _save()
    _prune_orphans(out_dir, {r["wav"] for r in manifest_items}
                   | {r["raw"] for r in manifest_items})
    write_audit(pack_dir, manifest_items, path=audit_path, failures=fail_list)
    return VoiceResult(
        total=len(items), done=len(recorded), cached=cached, failed=len(fail_list),
        pii_findings=_count_pii(items), batches=len(batches),
        wav_files=[b.wav for b in batches if b.wav.exists()],
        degraded=sum(len(r.get("unanchored") or {}) for r in manifest_items),
        errors=fail_list)


def _prune_orphans(out_dir: Path, keep: set[str]) -> None:
    """manifest から外れた古い音檔を消す（QA 改訂のたびに溜まるため）。

    track の部屋（サブディレクトリ）には触らない — 別 track の音檔は別 manifest
    の管理下で、ここの keep には最初から載らない。
    """
    for path in out_dir.iterdir():
        if path.is_dir():
            continue
        if path.name != "manifest.json" and path.name not in keep:
            path.unlink()


def write_audit(pack_dir: Path, records: list[dict],
                path: Path | None = None,
                failures: list[dict] | None = None) -> Path:
    """読み上げ原稿の監査表。音声だけ聞いても「骨子に無い話」に気づけないため。

    録れなかったバッチも先頭に並べる — 音檔が欠けていること自体は音声を聞いても
    分からないし、端末に流れた ✗ 行はもう残っていない。
    """
    lines = ["# GPT 音声原稿の監査", "",
             "話し言葉への書き直しで、想定問答の骨子にも経歴ファイルにも無い数字・"
             "固有名詞が混ざっていないかの機械チェック結果。⚠ の問は本番で口に出す前に"
             "必ず裏を取ること。", ""]
    if failures:
        lines += [f"## ✗ 録れなかったバッチ（{len(failures)} 件）", "",
                  "| 問 | 実行主体 | 段階 | 試行 | 理由 |", "|---|---|---|---|---|"]
        for f in failures:
            span = (f"第{f['ordinals'][0]}〜{f['ordinals'][-1]}問"
                    if len(f["ordinals"]) > 1 else f"第{f['ordinals'][0]}問")
            reason = str(f.get("error", "")).replace("|", "\\|")[:200]
            lines.append(f"| {span} | {f.get('backend', '')} | {f.get('stage', '')} "
                         f"| {f.get('attempts', 1)} | {reason} |")
        lines.append("")
    for r in sorted(records, key=lambda x: x["first_ordinal"]):
        spoken = r.get("spoken") or {}
        bad_map = r.get("unanchored") or {}
        lines.append(f"## 音声 `{r.get('wav', '')}`（第{r['ordinals'][0]}〜"
                     f"{r['ordinals'][-1]}問）\n")
        for n, question in zip(r["ordinals"], r["questions"]):
            bad = bad_map.get(str(n)) or []
            text = spoken.get(str(n))
            verdict = ("⚠ 裏が取れない語 — " + "、".join(bad) if bad
                       else "✓ 骨子・経歴の範囲内" if text else "— 原稿を取得できず")
            lines += [f"### {n}. {question}", "", f"- 判定: {verdict}", "",
                      text or "", ""]
    path = path or pack_dir / "06_voice_audit.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def find_pack_dir(job_id: int) -> Path:
    candidates = sorted(PREP_DIR.glob(f"{job_id}_*"))
    if not candidates:
        sys.exit(f"output/prep/{job_id}_* が見つかりません")
    return candidates[0]


def main() -> None:
    p = argparse.ArgumentParser(description="想定問答の GPT 音声化（ChatGPT 読み上げ捕捉）")
    p.add_argument("--job-id", type=int, required=True)
    p.add_argument("--limit", type=int, default=None, help="先頭 N 問だけ")
    p.add_argument("--force", action="store_true", help="キャッシュ無視で録り直し")
    p.add_argument("--batch-size", type=int, default=None,
                   help="1 メッセージにまとめる問数（既定は config の batch_size）")
    args = p.parse_args()

    pack_dir = find_pack_dir(args.job_id)
    res = generate(pack_dir, limit=args.limit, force=args.force,
                   batch_size=args.batch_size)
    print(f"完了: {res.total} 問 / {res.batches} バッチ（録音 {res.done} / "
          f"キャッシュ {res.cached} / 失敗 {res.failed}）"
          + (f" / PII 置換 {res.pii_findings} 件" if res.pii_findings else "")
          + (f" / 要確認 {res.degraded} 問" if res.degraded else ""))
    sys.exit(1 if res.failed and not res.done else 0)


if __name__ == "__main__":
    main()
