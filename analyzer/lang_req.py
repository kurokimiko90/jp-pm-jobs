"""JD の外国語要件判定（純規則零 LLM）— jobs.lang_env を回填する。

値の形式（カンマ区切り、言語:強度）:
  "en:req"         英語必須（英文 JD＝英語環境も含む）
  "en:pref,zh:req" 英語歓迎・中国語必須
  ""               日本語のみ（他言語の要求なし）
  None             raw_jd 空（判定不能）

判定:
  1. JD 本文の CJK 比率 < 20%（prep.py の投遞包言語判定と同じ閾値）→ 英語環境 = en:req
  2. 言語キーワードごとに前後の文脈を見る
     - 否定（不問／不要／not required）→ 無視
     - 福利厚生（英会話補助／語学研修）→ 無視
     - 同じ行に 歓迎／尚可／preferred / plus → pref
     - 直前の見出しが 歓迎要件 系 → pref、必須要件 系 → req
     - 能力を問う語（レベル／TOEIC／堪能／fluent…）が近くに無い裸の言及 → 無視
  同じ言語で req と pref が両方出たら req を採る。
"""

from __future__ import annotations

import re

_CJK_RE = re.compile(r"[　-鿿＀-￯]")
_EN_JD_THRESHOLD = 0.20
_MIN_BODY = 200  # 短すぎる本文は比率がぶれるので英文 JD 判定に使わない

_LANG_PATTERNS: dict[str, re.Pattern[str]] = {
    "en": re.compile(
        r"英語|英文|バイリンガル|TOEIC|TOEFL|IELTS|(?<![a-z])english(?![a-z])",
        re.IGNORECASE,
    ),
    "zh": re.compile(
        r"中国語|北京語|中文|普通話|繁体字|簡体字|(?<![a-z])(?:mandarin|chinese)(?![a-z])",
        re.IGNORECASE,
    ),
    "ko": re.compile(r"韓国語|ハングル|(?<![a-z])korean(?![a-z])", re.IGNORECASE),
}

_NEG_RE = re.compile(
    r"不問|不要|必要(?:は)?(?:ありません|ない|なし)|問いません|できなくても|なくても"
    r"|not\s+(?:required|necessary|needed)|no\s+english",
    re.IGNORECASE,
)
_BENEFIT_RE = re.compile(r"英会話|語学研修|語学学習|学習支援|補助|手当|レッスン|e-?learning", re.IGNORECASE)
_PREF_RE = re.compile(
    r"歓迎|尚可|尚良|望まし|あれば|優遇|活か|抵抗の?ない|preferred|nice\s+to\s+have|a\s+plus|\bplus\b|bonus|advantage",
    re.IGNORECASE,
)
_SKILL_RE = re.compile(
    r"レベル|力|スキル|堪能|流暢|ネイティブ|ビジネス|日常会話|会話|読み書き|読解|点以上|使用|公用語"
    r"|初級|中級|上級|経験|利用|理解|抵抗"
    r"|コミュニケーション|やり取り|やりとり|折衝|会議|ミーティング|資料|メール|話せ"
    r"|TOEIC|TOEFL|IELTS|fluen|proficien|business|native|conversational|communicat"
    r"|speak|written|verbal|level|required|must",
    re.IGNORECASE,
)
_HEAD_PREF_RE = re.compile(
    r"歓迎(?:要件|スキル|条件|経験)?|尚可|preferred\s+(?:qualifications|skills|requirements)"
    r"|nice\s+to\s+have|bonus\s+points|pluses",
    re.IGNORECASE,
)
_HEAD_REQ_RE = re.compile(
    r"必須(?:要件|スキル|条件|経験)?|応募資格|応募条件|求める(?:スキル|経験)"
    r"|(?:minimum|basic|required)\s+qualifications|requirements|must\s+have",
    re.IGNORECASE,
)
_SECTION_LOOKBACK = 400


def is_english_jd(raw_jd: str) -> bool:
    body = raw_jd.strip()
    if len(body) < _MIN_BODY:
        return False
    return len(_CJK_RE.findall(body)) / len(body) < _EN_JD_THRESHOLD


def _line_around(text: str, start: int, end: int) -> str:
    ls = text.rfind("\n", 0, start) + 1
    le = text.find("\n", end)
    line = text[ls: le if le != -1 else len(text)]
    # 長い 1 行（改行なし JD）は前後 60 字に絞る
    if len(line) > 160:
        return text[max(ls, start - 60): min(le if le != -1 else len(text), end + 60)]
    return line


def _section_strength(text: str, start: int) -> str | None:
    """直前の見出しが歓迎系なら pref、必須系なら req、どちらも無ければ None。"""
    before = text[max(0, start - _SECTION_LOOKBACK): start]
    last_pref = max((m.end() for m in _HEAD_PREF_RE.finditer(before)), default=-1)
    last_req = max((m.end() for m in _HEAD_REQ_RE.finditer(before)), default=-1)
    if last_pref == last_req == -1:
        return None
    return "pref" if last_pref > last_req else "req"


def _mention_strength(text: str, m: re.Match[str]) -> str | None:
    line = _line_around(text, m.start(), m.end())
    if _NEG_RE.search(line) or _BENEFIT_RE.search(line):
        return None
    if _PREF_RE.search(line):
        return "pref"
    if not _SKILL_RE.search(line.replace(m.group(0), "")) and not re.match(
        r"TOEIC|TOEFL|IELTS|バイリンガル", m.group(0), re.IGNORECASE
    ):
        return None
    return _section_strength(text, m.start()) or "req"


def detect_lang_env(title: str | None, raw_jd: str | None) -> str | None:
    """jobs.lang_env の値を返す（形式はモジュール docstring 参照）。"""
    body = raw_jd or ""
    if not body.strip():
        return None
    found: dict[str, str] = {}
    if is_english_jd(body):
        found["en"] = "req"
    text = f"{title or ''}\n{body}"
    for lang, pat in _LANG_PATTERNS.items():
        for m in pat.finditer(text):
            strength = _mention_strength(text, m)
            if strength == "req" or (strength == "pref" and lang not in found):
                found[lang] = strength
    return ",".join(f"{lang}:{s}" for lang, s in found.items())
