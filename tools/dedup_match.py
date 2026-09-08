"""跨來源模糊查重 — 入庫前攔截同一職缺的重複。

同一職缺在不同來源（recruit_agent / recruiter_agent ...）source_id 格式不同，
`(source, source_id)` 精確去重抓不到。此模組用「正規化公司名完全相等 + 標題相似度」
判定重複，供 `tracker.db.upsert_job` 在 INSERT 前查詢。

門檻校準（實測 12 筆 recruit_agent）：重複職缺標題相似度 98-100%，
唯一職缺 ≤16%，斷層極大，門檻設 0.85 留足安全邊界。

raw_jd 第二道防線只適用於「跨來源」pair：全庫實測 43 對同來源
JD 相似度 ≥0.92 的 pair 全部是同公司不同職位（語言別崗位、senior/regular、
不同 domain），boilerplate 佔 JD 大半所致；同一平台上不同 source_id
即是不同刊登，靠標題規則即可。跨來源才存在「同一職缺換標題再現」需要 JD 攔截。

職級守衛：兩標題的職級 token（シニア/senior/junior/lead...）不一致時
一律視為不同職位，即使 JD 幾乎相同（senior 與 regular 常共用 JD 模板）。
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from difflib import SequenceMatcher

# 標題相似度門檻：≥ 此值且公司正規化相等 → 判為同職缺重複
TITLE_SIM_THRESHOLD = 0.85
# raw_jd 相似度門檻：標題未命中時的第二道防線（僅跨來源 pair）
JD_SIM_THRESHOLD = 0.85
# JD 防線的標題相似度下限：低於此值即使 JD 相同也視為不同職位
# （公司 boilerplate 佔 JD 大半時，完全不同的職位 JD 也會 ≥0.92）
JD_TITLE_FLOOR = 0.5
# raw_jd 比對截斷長度（避免超長 JD 拖慢速度）
JD_COMPARE_CHARS = 3000

# 公司名後綴 / 法人格雜訊。
# 英文法人格 / 集團字必須帶邊界，否則 Corp 會把 Corporation 砍成 oration、
# Group 誤傷 Groupon。不能用 \b：Python 把漢字也算 \w，「HOLDINGS株式会社」
# 的 S|株 之間沒有 \b，改用只認 ASCII 英數的 lookaround。
_COMPANY_NOISE = re.compile(
    r"株式会社|有限会社|合同会社|ホールディングス|グループ|本社"
    r"|(?<![A-Za-z0-9])(?:HOLDINGS?|GROUPS?|INC|CORP(?:ORATION)?|CO\.?,? ?LTD|LTD)\.?"
    r"(?![A-Za-z0-9])"
    r"|\s+",
    re.IGNORECASE,
)

# 公司名尾部括號注記：拠点 / 読み / 舊名等（「株式会社nine(四ツ谷)」）。
# 內容限 20 字以內，避免誤刪帶長說明的正文。(株) 也由此規則吸收。
_COMPANY_PAREN = re.compile(r"[\(（][^\)）]{0,20}[\)）]")

# 正規化後的公司別名 → 統一名（英日社名跨來源不一致時補齊）。
# key/value 都是 normalize 完成後的字串。只收實測出現過的組合，勿擴張。
_COMPANY_ALIASES = {
    "ly": "lineヤフー",           # LY Corporation (linkedin) ↔ LINEヤフー (indeed)
    "rakuten": "楽天",            # Rakuten (linkedin) ↔ 楽天 (indeed/bizreach)
}

# 標題雜訊：記號、數字、常見地名 / 募集副詞前綴
_TITLE_SYMBOLS = re.compile(r"[【】\[\]（）()《》／/|｜・,、。!！?？～~＜＞<>0-9]")
_TITLE_WORDS = ("東京", "フルリモート可", "フルリモート", "候補", "急募", "積極採用中")
# Indeed JP が末尾に付ける職種カテゴリラベル（" プロダクトマネージャー" や
# " web/オープンプロジェクトマネージャー" 等、キーワード前に別語が付く場合もある）
_INDEED_SUFFIX = re.compile(
    r"\s+\S*?(プロダクトマネージャー|プロジェクトマネージャー|エンジニア|コンサルタント"
    r"|マネージャー|ディレクター|アナリスト|デザイナー|スペシャリスト)$"
)

# 職級 token：兩標題的命中集合不同 → 視為不同職位（不判重複）
_LEVEL_PATTERNS = {
    "senior": re.compile(r"シニア|senior|\bsr\.?\b", re.IGNORECASE),
    "junior": re.compile(r"ジュニア|junior|\bjr\.?\b", re.IGNORECASE),
    "lead": re.compile(r"テックリード|リード(?!する)|\blead\b", re.IGNORECASE),
    "principal": re.compile(r"プリンシパル|principal", re.IGNORECASE),
    "head": re.compile(r"\bhead\b|ヘッドオブ", re.IGNORECASE),
    "vp": re.compile(r"\bvpo?[pe]?\b|vice ?president", re.IGNORECASE),
}


def normalize_company(name: str) -> str:
    """正規化公司名：NFKC → 去括號注記 → 去法人格 / 後綴 → 小寫 → 別名歸一。"""
    s = unicodedata.normalize("NFKC", name or "")
    s = _COMPANY_PAREN.sub("", s)
    s = _COMPANY_NOISE.sub("", s)
    s = s.strip().lower()
    return _COMPANY_ALIASES.get(s, s)


def normalize_title(title: str) -> str:
    """正規化職務標題：NFKC → 去記號 / 數字 → 去 Indeed 職種サフィックス → 小寫。"""
    s = unicodedata.normalize("NFKC", title or "")
    s = _TITLE_SYMBOLS.sub("", s)
    s = _INDEED_SUFFIX.sub("", s)
    for w in _TITLE_WORDS:
        s = s.replace(w, "")
    return s.strip().lower()


def title_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, normalize_title(a), normalize_title(b)).ratio()


def level_profile(title: str) -> frozenset[str]:
    """抽取標題中的職級 token 集合（NFKC 後比對，全半形不敏感）。"""
    s = unicodedata.normalize("NFKC", title or "")
    return frozenset(k for k, p in _LEVEL_PATTERNS.items() if p.search(s))


def find_duplicate(conn: sqlite3.Connection, job: dict) -> int | None:
    """在 jobs 中查同一職缺的既有列，命中回傳其 id，否則 None。

    判斷順序（同公司 + 職級 token 一致的前提下）：
      ② 職位標題相似度 ≥ TITLE_SIM_THRESHOLD → 重複
      ③ 跨來源且標題相似度 ≥ JD_TITLE_FLOOR 且 raw_jd 相似度 ≥ JD_SIM_THRESHOLD
         → 重複（同一職缺換標題再現；同來源不適用，見模組 docstring）
    """
    cnorm = normalize_company(job.get("company") or "")
    if not cnorm:
        return None

    tnorm = normalize_title(job.get("title") or "")
    new_level = level_profile(job.get("title") or "")
    new_source = job.get("source") or ""
    new_jd = (job.get("raw_jd") or "")[:JD_COMPARE_CHARS]

    rows = conn.execute(
        "SELECT id, source, title, raw_jd FROM jobs WHERE company_norm = ?", (cnorm,)
    ).fetchall()

    best_title_id: int | None = None
    best_title_sim = 0.0
    best_jd_id: int | None = None
    best_jd_sim = 0.0

    for r in rows:
        # 職級不一致 → 不同職位（senior/regular 常共用 JD 模板，先擋掉）
        if level_profile(r["title"] or "") != new_level:
            continue

        t_sim = SequenceMatcher(None, tnorm, normalize_title(r["title"] or "")).ratio()
        if t_sim > best_title_sim:
            best_title_sim, best_title_id = t_sim, r["id"]

        # JD 防線：僅跨來源、且標題至少中度相似時才比 JD
        if (
            new_jd
            and r["raw_jd"]
            and new_source
            and r["source"] != new_source
            and t_sim >= JD_TITLE_FLOOR
        ):
            sm = SequenceMatcher(None, new_jd, (r["raw_jd"] or "")[:JD_COMPARE_CHARS])
            # quick_ratio 是 ratio 的上界，先濾掉明顯不同的，省全量比對
            if sm.quick_ratio() >= JD_SIM_THRESHOLD:
                j_sim = sm.ratio()
                if j_sim > best_jd_sim:
                    best_jd_sim, best_jd_id = j_sim, r["id"]

    if best_title_sim >= TITLE_SIM_THRESHOLD:
        return best_title_id
    if best_jd_sim >= JD_SIM_THRESHOLD:
        return best_jd_id
    return None
