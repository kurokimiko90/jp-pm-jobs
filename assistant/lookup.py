"""提問導向的職缺／企業檢索 — 固定快照答不了「XX 公司投過沒有」。

`context.build_context()` 的固定區塊只放得下漏斗與 top N 清單。使用者一問到
具體公司，事實區塊裡沒有那家 → LLM 就退回去讀 [對話紀錄] 的舊回答。這裡負責
把「提問提到的公司／職缺」的**當前**狀態撈出來，一起送進 prompt。

比對走模糊：使用者打的是「サンプル」「SAMPLE STUDIO」這種簡稱或部分名，
DB 存的是「株式会社サンプルロボティクス」。精確子字串比對接不住。
"""

from __future__ import annotations

import json
import re
import unicodedata
from difflib import SequenceMatcher

from tracker import db

_JOB_ID_RE = re.compile(r"(?:job:|#)(\d+)")
_ASCII_RE = re.compile(r"^[\x00-\x7f]+$")

# 「投過沒有」類提問的意圖詞。命中但查無公司時要明確說「查無」，
# 不然 LLM 會拿別家公司的紀錄硬套，或含糊帶過。
APPLY_INTENT_WORDS = (
    "應聘", "应聘", "應募", "応募", "投遞", "投递", "投過", "投过", "投了",
    "應徵", "应征", "面試過", "面试过", "面接した", "受けた", "エントリー",
    "選考", "选考", "apply", "applied", "application",
)

# 法人格を剥がしても残る汎用語。これで命中させると同業他社が総なめになる。
_GENERIC_TOKENS = frozenset({
    "システム", "システムズ", "テクノロジー", "テクノロジーズ", "ソリューション",
    "ソリューションズ", "ホールディングス", "ジャパン", "グループ", "サービス",
    "コンサルティング", "パートナーズ", "ネットワーク", "デザイン", "エンジニア",
    "エンジニアリング", "プロダクト", "デジタル", "ソフトウェア", "リサーチ",
    "technology", "technologies", "solutions", "holdings", "japan", "group",
    "services", "consulting", "partners", "systems", "digital", "software",
})

# CJK 名の命中閾値。4 文字一致すれば十分に固有（サンプル→サンプルロボティクス）、
# それ未満は「core の 7 割以上が一致」を要求して汎用語の巻き込みを防ぐ。
_MIN_ABS_MATCH = 4
_MIN_SHORT_MATCH = 3
_SHORT_RATIO = 0.7
# 一致部分が core の 3 割に満たないものは、長い社名にたまたま含まれた共通語とみなす
_MIN_RATIO_FLOOR = 0.3
# ASCII 名は日常英語に釣られるので長さを要求したうえで単語境界照合
_MIN_ASCII_LEN = 5

# `normalize_company` は空白・記号を落とすので DB 側は `samplestudio`。
# 質問文の `SAMPLE STUDIO` と突き合わせるには質問文も同じ形に潰す必要がある。
_PUNCT_RE = re.compile(r"[\s\-_.,&'’·・()（）\[\]「」【】/]+")


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "").lower()


def compact(s: str) -> str:
    """比對基準：NFKC → 小寫 → 去空白與分隔記號（DB 的 company_norm 同款處理）。"""
    return _PUNCT_RE.sub("", _norm(s))


def has_apply_intent(question: str) -> bool:
    q = _norm(question)
    return any(w in q for w in APPLY_INTENT_WORDS)


def _longest_common(core: str, q: str) -> str:
    """core と質問文の最長共通部分文字列。命中の根拠として呼び出し側へ返す。"""
    m = SequenceMatcher(None, core, q, autojunk=False).find_longest_match(
        0, len(core), 0, len(q)
    )
    return core[m.a: m.a + m.size]


def match_score(core: str, q: str) -> tuple[float, str] | None:
    """core が質問文（`compact()` 済み）に出ているか。

    命中なら (core に対する一致率, 命中した文字列)。率は同名候補が複数出た
    ときの並べ替えに使う。
    """
    if not core or not q:
        return None

    if _ASCII_RE.match(core):
        if len(core) < _MIN_ASCII_LEN or core in _GENERIC_TOKENS:
            return None
        # 空白を潰した後なので単語境界は「前後が英数字でない」で判定する。
        # これが無いと `studio` が `samplestudio` の内側に食い込んで誤命中する。
        if re.search(rf"(?<![a-z0-9]){re.escape(core)}(?![a-z0-9])", q):
            return 1.0, core
        return None

    hit = _longest_common(core, q)
    if len(hit) < _MIN_SHORT_MATCH or hit in _GENERIC_TOKENS:
        return None
    ratio = len(hit) / len(core)
    if ratio < _MIN_RATIO_FLOOR:
        return None
    # 部分一致は社名の**先頭**でなければ弾く。日本語の社名は語尾が共通しがちで
    # （〜キャスト／〜スタジオ／〜ティング）、末尾一致を許すと同業他社が総なめに
    # なる。略称は頭を取る（サンプル→サンプルロボティクス）ので先頭要求で拾える。
    # 逆に「正式名が DB 側の社名の途中に埋まっている」ケースは取り逃す。
    if ratio < 1.0 and not core.startswith(hit):
        return None
    if len(hit) >= _MIN_ABS_MATCH or ratio >= _SHORT_RATIO:
        return ratio, hit
    return None


def _gap_reason(gap_json: str | None) -> str | None:
    """gap_analysis JSON から理由を 1 文。取れなければ None（捏造しない）。"""
    if not gap_json:
        return None
    try:
        reason = json.loads(gap_json).get("reason")
    except (json.JSONDecodeError, TypeError, AttributeError):
        return None
    return (reason or "").strip()[:120] or None


def _rows_for_ids(conn, ids: list[int]) -> list[dict]:
    rows = conn.execute(
        "SELECT j.id, j.company, j.company_norm, j.title, j.score, j.recommend_score, "
        "j.gap_analysis, a.status, a.applied_at, a.last_updated, a.next_event, "
        "a.rejection_stage, a.rejection_reason, a.channel "
        "FROM jobs j LEFT JOIN applications a ON a.job_id = j.id "
        f"WHERE j.id IN ({','.join('?' * len(ids))})",
        ids,
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["gap_reason"] = _gap_reason(d.pop("gap_analysis"))
        out.append(d)
    return out


def find_companies(question: str, limit: int = 4) -> list[dict]:
    """質問文に出てくる企業を曖昧一致で探し、その企業の応募歴を返す。

    企業単位（company_norm）でまとめる — 同じ会社で複数の求人が入っていることが
    あり、1 件だけ見て「応募していない」と答えると事実を取り違える。
    """
    q = compact(question)
    if not q:
        return []

    with db.connect() as conn:
        companies = conn.execute(
            "SELECT DISTINCT COALESCE(company_norm, '') AS cnorm, company FROM jobs "
            "WHERE company IS NOT NULL AND company != ''"
        ).fetchall()

        matched: dict[str, tuple[float, str, str]] = {}
        for r in companies:
            core = r["cnorm"] or compact(r["company"])
            hit = match_score(core, q)
            if hit is None:
                continue
            score, text = hit
            prev = matched.get(core)
            if prev is None or score > prev[0]:
                matched[core] = (score, text, r["company"])

        if not matched:
            return []

        out = []
        for core, (score, hit_text, display) in sorted(
            matched.items(), key=lambda kv: -kv[1][0]
        )[:limit]:
            rows = conn.execute(
                "SELECT j.id, j.company, j.title, j.score, j.recommend_score, "
                "j.gap_analysis, a.status, a.applied_at, a.last_updated, a.next_event, "
                "a.rejection_stage, a.rejection_reason, a.channel "
                "FROM jobs j LEFT JOIN applications a ON a.job_id = j.id "
                "WHERE COALESCE(j.company_norm, '') = ? "
                "ORDER BY a.applied_at IS NULL, a.applied_at DESC, j.score DESC",
                (core,),
            ).fetchall()

            applications, others = [], []
            for r in rows:
                d = dict(r)
                d["gap_reason"] = _gap_reason(d.pop("gap_analysis"))
                (applications if d["status"] else others).append(d)

            out.append({
                "company": display,
                "company_norm": core,
                "matched_text": hit_text,
                "match_ratio": round(score, 2),
                "applied": bool(applications),
                "applications": applications,
                "other_jobs": others[:3],
                "job_count": len(rows),
            })
    return out


def find_jobs_by_id(question: str, limit: int = 5) -> list[dict]:
    """質問文の `job:ID` / `#ID` を直接引く。"""
    ids = sorted({int(m) for m in _JOB_ID_RE.findall(question)})[:limit]
    if not ids:
        return []
    with db.connect() as conn:
        return _rows_for_ids(conn, ids)
