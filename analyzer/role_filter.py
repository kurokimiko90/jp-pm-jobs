"""職能過濾 — 排除純工程職，避免 PM 求職 pipeline 把高分工程職當精選。

策略：只排除「明確是工程職且無 PM 字樣」的 title（保守，寧可放過不誤殺）。
日文職銜（プロダクト企画 / 事業開発 等）多半 PM/ENG 都不命中，一律保留。
"""

from __future__ import annotations

import re


def _normalize(text: str) -> str:
    """全角英数字を半角に変換（リクルートエージェント等の全角変換対策）。"""
    result = []
    for ch in text:
        cp = ord(ch)
        if 0xFF01 <= cp <= 0xFF5E:
            result.append(chr(cp - 0xFEE0))
        else:
            result.append(ch)
    return "".join(result)

_ENG = re.compile(
    r"\b(engineer|scientist|developer|sre|architect|backend|frontend|fullstack|infra(structure)?)\b"
    r"|エンジニア|開発者",
    re.IGNORECASE,
)
_PM = re.compile(
    r"product\s*manager|product\s*owner"
    r"|(?<![a-zA-Z])pdm(?![a-zA-Z])|(?<![a-zA-Z])tpm(?![a-zA-Z])"
    r"|(?<![a-zA-Z])pjm(?![a-zA-Z])|(?<![a-zA-Z])pmo(?![a-zA-Z])"
    r"|program\s*manager|project\s*manager"
    r"|プロダクトマネ|プロダクト\s*オーナー|プロダクト.*マネージャー|プロジェクトマネ",
    re.IGNORECASE,
)


def is_engineering_only(title: str | None) -> bool:
    """True = 純工程職（工程關鍵字命中且無任何 PM 字樣）→ 應從 PM 精選排除。"""
    if not title:
        return False
    return bool(_ENG.search(title)) and not bool(_PM.search(title))


def filter_pm_roles(rows: list) -> list:
    """從 job rows（含 title 欄）剔除純工程職。"""
    return [r for r in rows if not is_engineering_only(r["title"])]


# 正向 PM 白名單閘 —— 用於 Bizreach 等寬鬆全文搜尋來源（qk= 會撈回大量
# 営業/法務/人事/監査/コンサル/デザイナー 等非 PM 職）。
# 策略：標題必須命中 PM 模式才入庫，其餘一律剔除（嚴格，寧可漏抓不污染）。
_PM_STRICT = re.compile(
    r"product\s*manager|product\s*owner|program\s*manager|project\s*manager"
    r"|(?<![a-zA-Z])pdm(?![a-zA-Z])|(?<![a-zA-Z])tpm(?![a-zA-Z])"
    r"|(?<![a-zA-Z])pjm(?![a-zA-Z])|(?<![a-zA-Z])pmo(?![a-zA-Z])"
    r"|(?<![a-zA-Z])pm(?![a-zA-Z])"
    r"|head\s*of\s*product|vp\s*of\s*product|(?<![a-zA-Z])cpo(?![a-zA-Z])|chief\s*product"
    r"|プロダクトマネ|プロダクト\s*オーナー|プロダクト.*マネージャー|プロジェクトマネ"
    # プロダクト/製品 + (企画|責任者|統括|オーナー)：間に「開発」等の修飾語が
    # 挟まる実務タイトル（例:「プロダクト開発責任者」）も拾えるよう .{0,6} を許容。
    # 「製品」は「プロダクト」の漢字表記（同義）。
    r"|(プロダクト|製品).{0,6}(企画|責任者|統括|オーナー)",
    re.IGNORECASE,
)


def is_pm_role(title: str | None) -> bool:
    """True = 標題明確是產品 PM 職（白名單命中）。用於寬鬆搜尋來源的入庫閘。"""
    if not title:
        return False
    return bool(_PM_STRICT.search(title))


# 非 PM 角色黑名單 —— Bizreach 卡片 title 是行銷標題，真職銜藏在 JD。
# 改用「標題明顯是他職 + JD 缺 PM 訊號」雙條件剔除（避免誤殺 title 是 blurb 的真 PM）。
_NON_PM = re.compile(
    r"営業|セールス|コンサルタント|コンサル|法務|経理|財務|人事|総務|監査"
    r"|デザイナー|アナリスト|マーケティング|広報|事務|窓口|接客|スタッフ"
    r"|エコノミスト|リサーチャー|サポート|オペレーション|BPO|運用管理"
    r"|テスト|検証|技術者|ABAP|SAP",
    re.IGNORECASE,
)
# JD 中的強 PM 訊號（職位描述層級，非偶然提及）
_PM_JD_SIGNAL = re.compile(
    r"product\s*manager|program\s*manager|project\s*manager"
    r"|プロダクトマネージャー|プロダクト\s*オーナー|プロジェクトマネージャー"
    r"|プロダクトマネジメント|プロジェクトマネジメント|product\s*owner|(?<![a-zA-Z])pdm(?![a-zA-Z])"
    r"|(プロダクト|製品).{0,6}(企画|責任者|統括)"
    r"|(?<![a-zA-Z])pm(?![a-zA-Z])|(?<![a-zA-Z])pmo(?![a-zA-Z])",  # PM/PMO略語
    re.IGNORECASE,
)


def is_pm_by_content(title: str | None, raw_jd: str | None, min_jd_hits: int = 2) -> bool:
    """True = 此職應保留為 PM。雙軌判定：

    1. 標題直接命中 PM 白名單 → 一律保留。
    2. 否則：標題若被非 PM 角色主導 → 看 JD 的 PM 訊號數，達 min_jd_hits 才保留。
       標題既非 PM 也非他職（純 blurb）→ 同樣靠 JD 訊號判定。
    """
    title = _normalize(title or "")
    if _PM_STRICT.search(title):
        return True
    jd = _normalize(raw_jd or "")
    pm_hits = len(_PM_JD_SIGNAL.findall(jd))
    # 標題被他職主導：需要更強的 JD PM 訊號才翻盤
    if _NON_PM.search(title):
        return pm_hits >= min_jd_hits + 1
    return pm_hits >= min_jd_hits
