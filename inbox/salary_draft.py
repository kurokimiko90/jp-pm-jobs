"""年収確認メール → 数字を埋めた返信草稿（零 LLM・テンプレート）。

エージェントの「年収確認のお願い」「事前確認依頼」「推薦前確認」で聞かれた項目
（最低希望 / 希望 / 現年収）だけを、`config/salary_answer.yaml` の数字で埋める。
年収以外の確認事項（入社時期・他社状況 等）は【要記入】で残す。

LLM を通さない理由：現年収は deid 規則で外部 LLM へ送れない（CLAUDE.md の
PII 去識別化規則）うえ、数字は毎回同じなので生成させる意味がない。

設定が無ければ None を返し、呼び出し側（inbox.draft）は通常の LLM 草稿に戻る。
"""
from __future__ import annotations

import re
import unicodedata

from tools import app_config

CONFIG_NAME = "salary_answer"

# 項目の検出（NFKC 後の本文に対して）。最低系を先に消してから希望を探す
_MINIMUM = re.compile(r"最低(?:希望)?年収|最低確保したい年収|最低限の希望年収|辞退しない最低|最低ライン")
_DESIRED = re.compile(r"希望年収|希望給与|希望する年収")
_CURRENT = re.compile(r"現職?年収|現在の?年収")

_LABELS = {"minimum": "最低希望年収", "desired": "希望年収", "current": "現年収"}

# リマインド / 回答メールで前回の依頼本文を引用する位置
_PREV_MARKER = re.compile(r"以下[、,]?前回お送りした(?:メール|内容)")
# 本文の終わり：結び（何卒 / どうぞ / 引き続き よろしくお願い…）か署名ブロックの先頭。
# 区切り線の長さでは判定しない（依頼ブロック内にも 24〜35 本の線がある）
_CLOSING = re.compile(
    r"(?:よろしく|宜しく)お願い|^(?:株式会社インディードリクルートパートナーズ|キャリアアドバイザー|■ご転職を検討中)",
    re.M,
)
# 依頼項目の行頭（■ ・ ● 1） 1. 依頼1. ①）
_ITEM = re.compile(r"^\s*(?:■|・|●|◆|[0-9]+\s*[)）.．]|依頼\s*[0-9]+\s*[.．]?|[①-⑩])\s*(?P<text>\S.*)$")
_SALARY_WORDS = re.compile(r"年収|年俸|給与")
# 項目ではなく見出し（「■企業からのご回答依頼（事前対応）」等）
_HEADER_WORDS = re.compile(r"ご回答依頼|ご確認依頼|回答期限|確認事項$")


def _nfkc(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "")


def load_answer() -> dict | None:
    """数字の設定。current / desired / minimum のどれも無ければ None（機能オフ）。"""
    cfg = app_config.load(CONFIG_NAME)
    if not any(cfg.get(k) for k in _LABELS):
        return None
    return cfg


def _segments(body: str) -> tuple[str, str]:
    """(依頼が書かれうる全文, 依頼項目を拾う区間) を返す。

    - `>` 引用行（本人の過去の返信）は落とす — 受領メールで年収の語に反応させない
    - 前回メール引用マーカーがあれば、その前（今回の本文）と後（前回の依頼）に分け、
      それぞれ結び（よろしくお願い…）で切って署名・フッターを落とす
    - 項目はリマインド自身の定型（「・直近でのご対応が…」等）を拾わないよう、
      前回引用があればそちら側からだけ拾う
    """
    text = "\n".join(l for l in _nfkc(body).splitlines() if not l.lstrip().startswith(">"))
    parts = _PREV_MARKER.split(text, maxsplit=1)

    def cut(s: str) -> str:
        m = _CLOSING.search(s)
        return s[: m.start()] if m else s

    parts = [cut(p) for p in parts]
    return "\n".join(parts), parts[-1]


def asked_fields(body: str) -> list[str]:
    """聞かれている年収項目を、本文に出てくる順で返す（例: ['minimum', 'desired', 'current']）。"""
    text, _ = _segments(body)
    found: dict[str, int] = {}
    for kind, pat in (("minimum", _MINIMUM), ("current", _CURRENT)):
        m = pat.search(text)
        if m:
            found[kind] = m.start()
    # 「最低希望年収」の中の「希望年収」を希望として数えない
    masked = _MINIMUM.sub(lambda m: "＿" * len(m.group()), text)
    m = _DESIRED.search(masked)
    if m:
        found["desired"] = m.start()
    return sorted(found, key=found.get)


def other_items(body: str) -> list[str]:
    """年収以外の依頼項目（入社時期・他社状況 等）。草稿では【要記入】で残す。"""
    _, request = _segments(body)
    items: list[str] = []
    open_item = False
    for line in request.splitlines():
        m = _ITEM.match(line)
        if m:
            items.append(m.group("text").strip())
            open_item = True
        elif open_item and line.startswith(" ") and line.strip():
            # 字下げされた次行は直前の項目の続き（「２）…不可です。\n　　問題ないでしょうか。」）
            items[-1] += line.strip()
        else:
            # 空行・※注記で項目を閉じる。※注記の字下げ続き行（「　併願企業名は…」）を
            # 項目として拾わないため
            open_item = False
    out = []
    for item in items:
        item = item.rstrip("：: ").strip()
        if (item and not _SALARY_WORDS.search(item) and not _HEADER_WORDS.search(item)
                and item not in out):
            out.append(item)
    return out


def _greeting(body: str) -> str:
    if "面接調整センター" in _nfkc(body):
        return "リクルートエージェント 面接調整センター ご担当者様"
    return "ご担当者様"


def salary_reply_text(mail: dict, answer: dict | None = None) -> str | None:
    """年収確認メールなら数字入りの返信本文（署名なし）。対象外 / 設定なしなら None。"""
    answer = answer if answer is not None else load_answer()
    if not answer:
        return None
    body = mail.get("body") or ""
    fields = [f for f in asked_fields(body) if answer.get(f)]
    if not fields:
        return None

    name = (answer.get("name") or "").strip()
    lines = [
        _greeting(body), "",
        f"お世話になっております。{name + 'です。' if name else ''}",
        "年収について、以下のとおり回答いたします。", "",
    ]
    lines += [f"・{_LABELS[f]}：{answer[f]}万円" for f in fields]
    note = (answer.get("note") or "").strip()
    if note:
        lines += ["", note]
    items = other_items(body)
    if items:
        lines += ["", "その他のご確認事項について、以下のとおり回答いたします。"]
        lines += [f"・{it}：【要記入】" for it in items]
    lines += ["", "何卒よろしくお願いいたします。"]
    return "\n".join(lines)
