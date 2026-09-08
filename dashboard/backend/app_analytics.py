"""應募データ的統計層（純函數，無 FastAPI 依賴，可單獨測試）。

設計原則（避免三個常見的統計誤讀）：

1. **禁止只看通過者的組成比**。「通過者中 60% 是 PdM」不代表 PdM 容易過——
   若投遞本來就有 65% 是 PdM，那 PdM 反而是拖後腿的。所有維度一律輸出
   **條件通過率 P(通過 | 分群)**，並與整體基準率對照。
2. **小樣本必須標記**。n=1 的 100% 與 n=146 的 15.8% 不能並排展示。
   一律附 Wilson 95% 信賴區間，且 n < `MIN_N` 標記為參考值。
3. **不確定資料不可默默丟棄**。rejected 但未填 rejection_stage 的記錄無法判斷
   卡在哪關，除了排除在分母外，另外輸出「全算沒過 / 全算通過」的區間上下界。

判定只在 `annotate()` 做一次，其餘 builder 全部吃標註後的結果——
同一頁的漏斗與通過率若各自判定，會出現 25 與 26 兩個數字互相打臉。
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime

# 選考階段順序。casual（カジュアル面談）不是所有人的必經關卡，
# 這裡只用它當「已進到面談階段」的門檻 index，漏斗不單獨列這一格。
STAGE_SEQUENCE = ["applied", "casual", "recruiter", "tech", "onsite", "offer"]
STAGE_INDEX = {s: i for i, s in enumerate(STAGE_SEQUENCE)}
_STAGE_INDEX = STAGE_INDEX  # 後方互換の別名
_INTERVIEW_THRESHOLD = _STAGE_INDEX["casual"]  # ≥ 此 index = 書類選考已通過

# 拒絕階段 → 該筆「至少到達過」的階段（在哪關被拒 = 到達過那關）
_REJECTION_STAGE_REACHED = {
    "shorui": "applied", "casual": "casual", "ichiji": "recruiter",
    "niji": "tech", "saishu": "onsite",
}

# 漏斗展示用的階段（key, 對應的 reached index 門檻）
_FUNNEL_STEPS = [
    ("applied", _STAGE_INDEX["applied"]),
    ("shorui_pass", _STAGE_INDEX["casual"]),
    ("recruiter", _STAGE_INDEX["recruiter"]),
    ("tech", _STAGE_INDEX["tech"]),
    ("onsite", _STAGE_INDEX["onsite"]),
    ("offer", _STAGE_INDEX["offer"]),
]

# 這個秒數以内に下位段階へ戻ったイベントは UI の押し間違いとみなす
_BURST_SECONDS = 600

# 本人辞退の目印（notes / next_event の自由記述。日本語・繁体字の表記ゆれを含む）
_WITHDRAWAL_KEYWORDS = ("辞退", "辭退", "自己拒絕", "自分から辞退")

# 樣本數低於此值只當參考值，不下結論
MIN_N = 10
# 分群項目少於 2 格 = 沒有對比資訊，不值得成為一個維度
_MIN_BUCKETS = 2


# ───────────────────────── 統計基礎 ─────────────────────────

def wilson_interval(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score 區間（%）。小樣本/極端比例時比常態近似可靠，
    且 k=0 或 k=n 時不會退化成寬度 0 的假精確區間。"""
    if n <= 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = z / denom * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (round(max(0.0, center - margin) * 100, 1), round(min(1.0, center + margin) * 100, 1))


def _parse_ts(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat((value or "")[:19])
    except ValueError:
        return None


def drop_misclick_events(events: list) -> list:
    """application_events から UI 連打の痕跡を落とす（漏斗と滞留日数の共通前処理）。

    このテーブルは dashboard の status 変更を全部記録するので、ドロップダウンを
    続けて押した軌跡（recruiter→tech→onsite→offer→recruiter が 3 分以内、等）が
    そのまま残る。実績として数えると「最終面接 3 件・内定 3 件」という実在しない
    漏斗や「オファー滞留 0.0 日」が出る（両方とも実際に出た）。

    直後（`_BURST_SECONDS` 以内）に上書きされた記録は連打の途中とみなして捨て、
    クラスタの最後＝本人が落ち着かせた状態だけを残す。
    """
    by_job: dict[int, list] = defaultdict(list)
    for e in events:
        by_job[e["job_id"]].append(e)

    cleaned = []
    for rows in by_job.values():
        for i, row in enumerate(rows):
            nxt = rows[i + 1] if i + 1 < len(rows) else None
            if nxt is not None:
                t0, t1 = _parse_ts(row["changed_at"]), _parse_ts(nxt["changed_at"])
                if t0 and t1 and 0 <= (t1 - t0).total_seconds() <= _BURST_SECONDS:
                    continue
            cleaned.append(row)
    return cleaned


def reached_max_stage(status: str | None, rejection_stage: str | None,
                      event_statuses: list[str]) -> int:
    """該筆最遠到達過的階段 index。信頼度の高い証拠から順に採用する：

    1. **選考中の記録**（status が rejected 以外）— 本人が更新した最新の事実。
    2. **rejection_stage あり** — 「どこで見送られたか」は人が記録した真実なので、
       イベント履歴より優先する。イベント側に押し間違いで上位段階が残っていても
       ここで打ち消す（例：書類落ちの案件に offer の残骸があった）。
    3. **段階不明の見送り** — 手がかりがイベントしか無いので、洗浄済み履歴を使う
       （`drop_misclick_events` を通した後の status 列を渡すこと）。
    """
    if status and status != "rejected" and status in _STAGE_INDEX:
        return _STAGE_INDEX[status]
    inferred = _REJECTION_STAGE_REACHED.get(rejection_stage or "")
    if inferred:
        return _STAGE_INDEX[inferred]
    indices = [_STAGE_INDEX[s] for s in event_statuses if s in _STAGE_INDEX]
    return max(indices) if indices else _STAGE_INDEX["applied"]


def is_self_withdrawn(notes: str | None, next_event: str | None) -> bool:
    """本人が辞退した案件か（企業からのお見送りとは別物）。

    判定はここが唯一の入口。ファネル・通過率・辞退一覧が別々に判定すると、
    同じページで件数が食い違う。表記ゆれ（辞退／辭退／自己拒絕）を全部見る。
    """
    blob = f"{notes or ''} {next_event or ''}"
    return any(kw in blob for kw in _WITHDRAWAL_KEYWORDS)


def classify(status: str | None, rejection_stage: str | None, reached: int,
             self_withdrawn: bool = False) -> str:
    """單筆應募對「書類選考」這道關卡的結果：

    pass      — 有證據進到面談/面接階段（事件表或拒絕階段任一佐證）＝書類有過
    fail      — 明確在書類選考被企業に見送られた
    withdrawn — 書類の結果が出る前に本人が辞退（右側打ち切り）。企業の判断では
                ないので分母から外す。fail に混ぜると通過率を不当に下げる
    unknown   — 已被拒但沒記錄階段、也無面接痕跡，無法判斷（不計入分母）
    pending   — 還在等結果（同樣不計入分母）

    面接まで進んだ後の辞退は「書類は通った」事実が変わらないので pass のまま。
    """
    if reached >= _INTERVIEW_THRESHOLD:
        return "pass"
    if self_withdrawn:
        return "withdrawn"
    if status == "applied":
        return "pending"
    if status == "rejected" and rejection_stage == "shorui":
        return "fail"
    if status == "rejected" and not rejection_stage:
        return "unknown"
    return "fail" if status == "rejected" else "pending"


def annotate(apps: list, cleaned_events: list) -> list[dict]:
    """把 DB row 攤平成帶 `outcome` / `reached` 的分析用記錄（唯一判定入口）。

    `cleaned_events` は `drop_misclick_events()` を通したものを渡す。
    """
    by_job: dict[int, list[str]] = defaultdict(list)
    for e in cleaned_events:
        by_job[e["job_id"]].append(e["status"])

    annotated = []
    for r in apps:
        reached = reached_max_stage(
            r["status"], r["rejection_stage"], by_job.get(r["job_id"], [])
        )
        withdrawn = is_self_withdrawn(r["notes"], r["next_event"])
        annotated.append({
            "job_id": r["job_id"], "company": r["company"], "status": r["status"],
            "rejection_stage": r["rejection_stage"], "channel": r["channel"] or "other",
            "applied_at": r["applied_at"], "tier": r["tier"], "job_type": r["job_type"],
            "mentions_ai": r["mentions_ai"], "score": r["score"],
            "recommend_score": r["recommend_score"], "employee_count": r["employee_count"],
            "domains": _domains(r["score_breakdown"]),
            "notes": r["notes"], "next_event": r["next_event"],
            "reached": reached, "self_withdrawn": withdrawn,
            "outcome": classify(r["status"], r["rejection_stage"], reached, withdrawn),
        })
    return annotated


# ───────────────────────── 分群定義 ─────────────────────────

def _score_band(v: int | None) -> str:
    if v is None:
        return "unknown"
    return "<60" if v < 60 else "60-69" if v < 70 else "70-79" if v < 80 else "80+"


def _employee_band(v: int | None) -> str:
    if not v:
        return "unknown"
    return "<100" if v < 100 else "100-999" if v < 1000 else "1000+"


def _domains(score_breakdown: str | None) -> list[str]:
    try:
        data = json.loads(score_breakdown) if score_breakdown else {}
    except (json.JSONDecodeError, TypeError):
        return ["none"]
    matched = data.get("matched_domain") or []
    return list(matched) if matched else ["none"]


# 維度定義：(key, 取值函數, 是否多標籤)
# 多標籤（一筆可命中多個）的分母彼此重疊，加總必然超過 100%，前端另外標示。
_DIMENSIONS: list[tuple[str, object, bool]] = [
    ("channel", lambda r: r["channel"], False),
    ("job_type", lambda r: r["job_type"] or "unclassified", False),
    ("recommend_band", lambda r: _score_band(r["recommend_score"]), False),
    ("score_band", lambda r: _score_band(r["score"]), False),
    ("mentions_ai", lambda r: "yes" if r["mentions_ai"] == 1 else ("no" if r["mentions_ai"] == 0 else "unknown"), False),
    ("domain", lambda r: r["domains"], True),
    ("employee_band", lambda r: _employee_band(r["employee_count"]), False),
]


# ───────────────────────── 對外組裝 ─────────────────────────

def _rate_entry(key: str, counts: dict, baseline: float | None) -> dict:
    decided = counts["pass"] + counts["fail"]
    rate = round(counts["pass"] / decided * 100, 1) if decided else None
    ci = wilson_interval(counts["pass"], decided) if decided else (None, None)
    return {
        "key": key, "passed": counts["pass"], "decided": decided,
        "pending": counts["pending"], "unknown": counts["unknown"],
        "rate": rate, "ci_low": ci[0], "ci_high": ci[1],
        # lift = 相對基準率的倍數；1.0 = 與整體無異
        "lift": round(rate / baseline, 2) if (rate is not None and baseline) else None,
        "insufficient": decided < MIN_N,
    }


def build_summary(annotated: list[dict]) -> dict:
    """頂端指標：書類通過率（含 CI）＋ 階段不明記錄造成的敏感度上下界。"""
    counts: dict = defaultdict(int)
    for r in annotated:
        counts[r["outcome"]] += 1
    decided = counts["pass"] + counts["fail"]
    ci = wilson_interval(counts["pass"], decided) if decided else (None, None)
    resolvable = decided + counts["unknown"]
    reached_interview = sum(1 for r in annotated if r["reached"] >= _INTERVIEW_THRESHOLD)
    return {
        "total": len(annotated),
        "passed": counts["pass"], "failed": counts["fail"],
        "unknown_stage": counts["unknown"], "pending": counts["pending"],
        # 書類の結果が出る前に本人が降りた件数（分母から除外済み）
        "withdrawn_early": counts["withdrawn"],
        # 面接まで進んだ後に本人が辞退した件数（書類は通っているので pass のまま）
        "withdrawn_after_pass": sum(
            1 for r in annotated if r["self_withdrawn"] and r["outcome"] == "pass"
        ),
        "decided": decided,
        "pass_rate": round(counts["pass"] / decided * 100, 1) if decided else None,
        "ci_low": ci[0], "ci_high": ci[1],
        # 敏感度：把「拒絕但階段不明」全算沒過 / 全算通過的兩個極端
        "bound_low": round(counts["pass"] / resolvable * 100, 1) if resolvable else None,
        "bound_high": round((counts["pass"] + counts["unknown"]) / resolvable * 100, 1) if resolvable else None,
        "reached_interview": reached_interview,
        "offers": sum(1 for r in annotated if r["reached"] >= _STAGE_INDEX["offer"]),
        "in_progress": sum(1 for r in annotated if r["status"] not in ("rejected", "offer")),
    }


def build_funnel(annotated: list[dict]) -> list[dict]:
    """選考漏斗：各階段「至少到達過」的件數與轉換率。

    用「至少到達 S」而非「當前狀態 = S」，保證單調遞減；否則會出現
    「オファー 3 件 > 最終面接 2 件」這種看起來壞掉的漏斗（面接記錄缺漏所致）。
    """
    funnel, prev, start = [], None, len(annotated)
    for i, (key, threshold) in enumerate(_FUNNEL_STEPS):
        at_stage = [r for r in annotated if r["reached"] >= threshold]
        n = len(at_stage)
        # この段階で止まった案件のうち、本人が降りたもの。企業に切られた数と
        # 混ぜると「通らなかった」に見えるが、実際は自分で選んで降りている。
        nxt = _FUNNEL_STEPS[i + 1][1] if i + 1 < len(_FUNNEL_STEPS) else None
        stopped_here = [
            r for r in at_stage if nxt is None or r["reached"] < nxt
        ]
        withdrawn_here = sum(1 for r in stopped_here if r["self_withdrawn"])
        funnel.append({
            "stage": key, "n": n,
            "conv_from_prev": round(n / prev * 100, 1) if prev else None,
            "conv_from_start": round(n / start * 100, 1) if start else None,
            "dropped": (prev - n) if prev is not None else None,
            "withdrawn": withdrawn_here,
        })
        prev = n
    return funnel


def build_segments(annotated: list[dict], baseline: float | None) -> list[dict]:
    """各維度的條件通過率（含 Wilson CI 與樣本數），按通過率排序。"""
    segments = []
    for dim, getter, multi in _DIMENSIONS:
        buckets: dict[str, dict] = defaultdict(lambda: defaultdict(int))
        for r in annotated:
            keys = getter(r)  # type: ignore[operator]
            for k in (keys if multi else [keys]):
                buckets[k][r["outcome"]] += 1
        items = [_rate_entry(k, v, baseline) for k, v in buckets.items()]
        items = [it for it in items if it["decided"] > 0]
        if len(items) < _MIN_BUCKETS:
            continue
        # 樣本充足者優先，各自再按通過率排序。n=1 の 100% を先頭に置くと
        # 「最も通りやすい分群」に見えてしまう＝このページが一番避けたい誤読。
        items.sort(key=lambda it: (it["insufficient"], -(it["rate"] or 0), -it["decided"]))
        # 効果量 = 十分な標本の中で基準からの乖離が最大のもの（次元の並べ替えに使う）
        effects = [
            abs(it["lift"] - 1) for it in items
            if not it["insufficient"] and it["lift"] is not None
        ]
        max_effect = round(max(effects), 2) if effects else 0.0
        segments.append({
            "dimension": dim, "multi_label": multi,
            "max_effect": max_effect, "informative": max_effect >= 0.3, "items": items,
        })
    # 効果量の大きい次元から並べる（アルファベット順だと弱い信号が上に来る）
    segments.sort(key=lambda s: -s["max_effect"])
    return segments


def build_cohorts(annotated: list[dict], baseline: float | None) -> list[dict]:
    """月別 cohort 通過率——以「投遞當月」分組，看的是投遞策略的成效趨勢。"""
    buckets: dict[str, dict] = defaultdict(lambda: defaultdict(int))
    for r in annotated:
        month = (r["applied_at"] or "")[:7]
        if month:
            buckets[month][r["outcome"]] += 1
    return [
        {**_rate_entry(m, v, baseline), "month": m}
        for m, v in sorted(buckets.items())
    ]


def build_quality(annotated: list[dict]) -> dict:
    """資料品質：哪些欄位覆蓋率太低，以致上面的分析不能做或不可信。"""
    total = len(annotated) or 1
    return {
        "tier_coverage": round(
            sum(1 for r in annotated if r["tier"] and r["tier"] != "unknown") / total * 100, 1),
        "recommend_coverage": round(
            sum(1 for r in annotated if r["recommend_score"] is not None) / total * 100, 1),
        "employee_coverage": round(
            sum(1 for r in annotated if r["employee_count"]) / total * 100, 1),
        "unknown_stage_pct": round(
            sum(1 for r in annotated if r["outcome"] == "unknown") / total * 100, 1),
        "min_n": MIN_N,
    }
