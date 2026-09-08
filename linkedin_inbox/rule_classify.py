"""LinkedIn 對話規則分類（零 LLM）— 只留招聘相關，過濾社交/廣告/雜訊。

比照 inbox/rule_classify.py 的關鍵詞判斷路數，但判斷面不同：Gmail 版看信件標題/正文，
這裡额外看 LinkedIn 對話特有的訊號——發信人 headline（職稱欄，最可靠）與訊息正文關鍵詞。
"""
from __future__ import annotations

# 發信人 headline 命中任一 → 高信心招聘相關（LinkedIn 職稱欄很少造假，訊號最強）
_HEADLINE_KEYWORDS = (
    "recruiter", "recruiting", "talent acquisition", "talent partner", "sourcer",
    "hr ", "human resources", "headhunt", "hiring manager", "people team",
    "採用", "人材紹介", "人事", "タレントアクイジション", "リクルーター", "HRBP",
)

# 訊息正文命中 → 中信心，需搭配 headline 或多個關鍵詞才判定招聘相關
_BODY_KEYWORDS = (
    "position", "opportunity", "opening", "role", "job", "career", "hiring",
    "interview", "resume", "cv ", "compensation", "salary",
    "求人", "採用", "募集", "面接", "カジュアル面談", "選考", "年収", "転職",
    "ポジション", "キャリア", "職務経歴書",
)

# 明顯是廣告/連結邀請罐頭訊息的雜訊詞 → 直接判定非招聘（優先權最高，避免誤判）
_NOISE_KEYWORDS = (
    "connect with me", "let's connect", "grow my network", "endorse",
    "つながりましょう", "コネクトしましょう",
)

_HIGH_CONF = 0.85
_MID_CONF = 0.6
_LOW_CONF = 0.3


def classify(conv: dict) -> dict:
    """回傳 {"category": "recruiting"|"other", "confidence": float}。"""
    headline = (conv.get("sender_headline") or "").lower()
    body = (conv.get("body_raw") or "").lower()

    if any(kw in body for kw in _NOISE_KEYWORDS) and not any(
        kw.lower() in headline for kw in _HEADLINE_KEYWORDS
    ):
        return {"category": "other", "confidence": _LOW_CONF}

    headline_hit = any(kw.lower() in headline for kw in _HEADLINE_KEYWORDS)
    body_hits = sum(1 for kw in _BODY_KEYWORDS if kw.lower() in body)

    if headline_hit and body_hits >= 1:
        return {"category": "recruiting", "confidence": _HIGH_CONF}
    if headline_hit:
        return {"category": "recruiting", "confidence": _MID_CONF}
    if body_hits >= 2:
        return {"category": "recruiting", "confidence": _MID_CONF}
    return {"category": "other", "confidence": _LOW_CONF}
