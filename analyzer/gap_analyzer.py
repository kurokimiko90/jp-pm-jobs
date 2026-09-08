"""Gap 分析器 — 對高分職缺逐筆做「我 vs JD 要求」差距分析（Haiku）。

輸入：data/candidate_profile.yaml（唯一真實來源）+ 該職 raw_jd
輸出：LLM 抽 JD 硬性要求 → 逐項比對 profile → JSON：
  { requirements: [...], matched: [...], gaps: [...],
    recommend_score: 0-100, recommend_reason: "一句話", verdict: go|improve|skip }

分工：
  - AI（Haiku）負責生成：requirements / matched / gaps / recommend_reason
  - 規則公式負責計算：recommend_score / verdict（不依賴 AI 的數字）

零編造紀律：profile 缺的不要編，gaps 如實列。

用法:
    python3 -m analyzer.gap_analyzer --job-id 123
    python3 -m analyzer.gap_analyzer --top 10 --min-score 70
    python3 -m analyzer.gap_analyzer --backfill --min-score 55   # 分批清空所有未跑的
    python3 -m analyzer.gap_analyzer --job-id 123 --dry-run   # 不寫 DB，只印
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

from interview._llm import call
from analyzer import sweet_spot
from analyzer.gap_summary import summarize_batch
from tools.app_config import get as _cfg
from tools.deid import build_deid_profile
from tools.locale import lang_directive, text as locale_text
from tracker.db import (connect, top_scored, update_gap_analysis,
                        create_gap_batch, assign_gap_batch, finalize_gap_batch)

ROOT = Path(__file__).parent.parent
OUTPUT_DIR = ROOT / "output"
MODEL = _cfg("scoring", "gap_model", "claude-haiku-4-5")
MAX_JD_CHARS = 6000  # 控制 prompt 長度 / 成本

# ── 推薦度公式 ──────────────────────────────────────────────
# 關鍵字與權重可由 config/scoring.yaml 的 gap: 區塊覆蓋。
_GAP = _cfg("scoring", "gap", {}) or {}
_TIER1 = _GAP.get("tier1_keywords", ["Fintech","決済","LLM","AIエージェント","生成AI","コーディングエージェント",
          "AI/LLM","音声AI","自律思考型AI","AI駆動","AI Agent","AI×"])
_TIER2 = _GAP.get("tier2_keywords", ["SaaS","金融","AI機能","AI活用","DX","流通","リテール","EdTech","フィンテック"])
_HARD_STOP = _GAP.get("hard_stop_keywords", ["採用対象外","第二新卒","重複応募","重複企業","3件重複","3ポジション","経験過剰","過剰スペック"])
_CULTURE_RISK = _GAP.get("culture_risk_keywords", [
    "SIer文化", "システムインテグレータ", "システムインテグレーター", "受託開発文化",
    "受託開発", "ウォーターフォール", "客先常駐",
])
_RECOMMEND_WEIGHTS = _GAP.get("recommend_weights", {
    "salary": 25,
    "role_fit": 25,
    "company_product_stage": 15,
    "requirements": 15,
    "domain": 8,
    "evidence": 6,
    "work_conditions": 3,
    "culture_risk": 3,
})
_TARGET_SALARY = _cfg("scoring", "target_salary", {}) or {}
_TARGET_SALARY_MIN = int(_TARGET_SALARY.get("min", 900))
_TARGET_SALARY_MAX = int(_TARGET_SALARY.get("max", 1800))
_CORE_PM_RE = re.compile(
    r"product manager|product owner|product lead|head of product|"
    r"プロダクト.{0,10}マネー?ジャー?|プロダクトオーナー|プロダクト責任者",
    re.IGNORECASE,
)
_PROJECT_PM_RE = re.compile(
    r"project manager|program manager|technical program manager|(?<![a-z])(?:pjm|pmo)(?![a-z])|"
    r"(?:プロジェクト|プログラム).{0,10}マネー?ジャー?",
    re.IGNORECASE,
)
_GENERIC_PM_RE = re.compile(r"(?<![a-z])pm(?![a-z])", re.IGNORECASE)
_EVIDENCE_RE = re.compile(
    r"\d|年|社|件|名|経験|実績|主導|構築|運用|設計|導入|改善|リリース|"
    r"プロジェクト|ロードマップ|要件定義|api|llm|saas",
    re.IGNORECASE,
)
FORMULA_VERSION = "weighted_v2"


def _contains(text: str, keyword: str) -> bool:
    return keyword.lower() in text.lower()


def _score_salary(salary_min, salary_max) -> tuple[float, dict]:
    """薪資帶以最高額（機會）70% + 最低額（可接受性）30% 做連續評分。"""
    smin = float(salary_min) if salary_min is not None else None
    smax = float(salary_max) if salary_max is not None else None

    if smax is None:
        upper = 50.0  # JD 未揭露，不用假裝是低薪，但會降低信心度。
    elif smax >= _TARGET_SALARY_MAX:
        upper = 100.0
    elif smax >= _TARGET_SALARY_MIN:
        upper = 60.0 + 40.0 * (smax - _TARGET_SALARY_MIN) / (_TARGET_SALARY_MAX - _TARGET_SALARY_MIN)
    else:
        upper = max(0.0, 60.0 * smax / _TARGET_SALARY_MIN)

    if smin is None:
        lower = 50.0
    elif smin >= _TARGET_SALARY_MIN:
        lower = 100.0
    else:
        lower = max(0.0, 100.0 * smin / _TARGET_SALARY_MIN)

    return 0.7 * upper + 0.3 * lower, {
        "salary_min": salary_min,
        "salary_max": salary_max,
        "upper_score": round(upper, 1),
        "lower_score": round(lower, 1),
        "target_range": [_TARGET_SALARY_MIN, _TARGET_SALARY_MAX],
    }


def _score_role_fit(title: str) -> tuple[float, str]:
    text = title or ""
    if _PROJECT_PM_RE.search(text):
        return 50.0, "project_or_program_pm"
    if _CORE_PM_RE.search(text):
        return 100.0, "core_product_manager"
    if _GENERIC_PM_RE.search(text):
        return 65.0, "generic_pm"
    if "プロダクト" in text.lower() or "product" in text.lower():
        return 75.0, "product_adjacent"
    return 30.0, "non_pm_or_unclear"


def _score_company_product_stage(raw_jd: str) -> tuple[float, dict]:
    """公司／產品階段是 15% 維度，不再以大額 bonus 壓過其餘適配度。"""
    sweet = sweet_spot.evaluate(raw_jd)
    employees = sweet["employees"]
    if employees is None:
        size_score = 50.0
    elif 100 <= employees <= 500:
        size_score = 100.0
    elif 50 <= employees < 100:
        size_score = 80.0
    elif employees > 500:
        size_score = 75.0
    else:
        size_score = 55.0
    score = size_score * 0.25 + (40.0 if sweet["maturity"] else 0.0) + (35.0 if sweet["ai_upgrade"] else 0.0)
    return score, {**sweet, "size_score": size_score}


def _score_requirements(result: dict) -> tuple[float, dict]:
    """優先讀 LLM 逐項評估；舊資料沒有時退回既有 matched/requirements 比例。"""
    reqs = result.get("requirements") or []
    assessments = result.get("requirement_assessments") or []
    importance_weight = {"must": 3, "important": 2, "preferred": 1}
    status_score = {"matched": 1.0, "partial": 0.5, "gap": 0.0}
    valid: dict[int, tuple[int, float]] = {}
    for item in assessments:
        if not isinstance(item, dict):
            continue
        index = item.get("index")
        if not isinstance(index, int) or not 1 <= index <= len(reqs) or index in valid:
            continue
        valid[index] = (
            importance_weight.get(str(item.get("importance", "important")).lower(), 2),
            status_score.get(str(item.get("status", "gap")).lower(), 0.0),
        )
    if len(valid) == len(reqs) and reqs:
        total_weight = sum(weight for weight, _ in valid.values())
        score = 100.0 * sum(weight * status for weight, status in valid.values()) / total_weight
        return score, {"method": "weighted_assessments", "assessed": len(valid), "total": len(reqs)}

    matched = result.get("matched") or []
    if reqs:
        score = min(100.0, 100.0 * len(matched) / len(reqs))
    else:
        score = min(100.0, 100.0 * len(matched) / 6.0)
    return score, {"method": "legacy_count_ratio", "matched": len(matched), "total": len(reqs)}


def _score_evidence(result: dict) -> tuple[float, dict]:
    matched = [str(item) for item in (result.get("matched") or [])]
    if not matched:
        return 0.0, {"matched_with_evidence": 0, "matched_total": 0}
    proven = sum(bool(_EVIDENCE_RE.search(item)) for item in matched)
    score = 40.0 + 60.0 * proven / len(matched)
    return score, {"matched_with_evidence": proven, "matched_total": len(matched)}


def _score_work_conditions(result: dict, remote, sponsor_visa) -> tuple[float, dict]:
    value = str(remote or "").lower()
    if value in {"full", "remote", "リモート"}:
        score = 100.0
    elif value in {"hybrid", "ハイブリッド"}:
        score = 85.0
    else:
        score = 50.0  # 工作地／出社資訊缺失時保持中性並降低信心度。
    if sponsor_visa:
        score = max(score, 80.0)

    gap_text = " ".join(str(item) for item in (result.get("gaps") or [])).lower()
    if any(k in gap_text for k in ("日本語", "japanese")):
        score = min(score, 20.0)
    elif any(k in gap_text for k in ("英語", "english")):
        score = min(score, 40.0)
    return score, {"remote": remote, "sponsor_visa": bool(sponsor_visa)}


def _score_culture_risk(result: dict, raw_jd: str) -> tuple[float, list[str], list[str]]:
    text = f"{raw_jd}\n{' '.join(str(item) for item in (result.get('gaps') or []))}"
    blockers = [keyword for keyword in _HARD_STOP if _contains(text, keyword)]
    signals = [keyword for keyword in _CULTURE_RISK if _contains(text, keyword)]
    if blockers:
        return 0.0, signals, blockers
    if len(signals) >= 2:
        return 30.0, signals, blockers
    if signals:
        return 65.0, signals, blockers
    return 100.0, signals, blockers


def _confidence(result: dict, salary_min, salary_max, raw_jd: str, stage: dict, req_detail: dict) -> int:
    score = 0
    score += 25 if salary_min is not None or salary_max is not None else 0
    score += 20 if len(raw_jd or "") >= 500 else 10 if raw_jd else 0
    score += 30 if req_detail["total"] > 0 else 0
    score += 15 if req_detail["method"] == "weighted_assessments" else 0
    score += 10 if stage["employees"] is not None or stage["maturity"] or stage["ai_upgrade"] else 0
    return score


def compute_score(
    result: dict,
    salary_max=None,
    raw_jd: str = "",
    *,
    salary_min=None,
    title: str = "",
    remote=None,
    sponsor_visa=None,
) -> tuple[int, str, dict, float]:
    """正式推薦度（weighted_v2）：八個 0–100 維度依權重合計，另有硬性排除閘。

    回傳 (recommend_score, verdict, recommend_breakdown, raw_score)。舊 gap JSON
    沒有逐項 requirement_assessments 時，以既有 matched/requirements 比例回填，並
    在 breakdown/confidence 中標示其較低的資料完整度。
    """
    weights = _RECOMMEND_WEIGHTS
    if sum(weights.values()) != 100:
        raise ValueError("gap.recommend_weights 必須合計 100")

    salary, salary_detail = _score_salary(salary_min, salary_max)
    role_fit, role_class = _score_role_fit(title)
    stage, stage_detail = _score_company_product_stage(raw_jd)
    requirements, req_detail = _score_requirements(result)
    matched_text = " ".join(str(item) for item in (result.get("matched") or []))
    domain = 100.0 if any(_contains(matched_text, k) for k in _TIER1) else (
        70.0 if any(_contains(matched_text, k) for k in _TIER2) else 40.0
    )
    evidence, evidence_detail = _score_evidence(result)
    work, work_detail = _score_work_conditions(result, remote, sponsor_visa)
    culture, risk_signals, blockers = _score_culture_risk(result, raw_jd)

    dimensions = {
        "salary": (salary, salary_detail),
        "role_fit": (role_fit, {"class": role_class, "title": title}),
        "company_product_stage": (stage, stage_detail),
        "requirements": (requirements, req_detail),
        "domain": (domain, {"tier": "tier1" if domain == 100 else "tier2" if domain == 70 else "other"}),
        "evidence": (evidence, evidence_detail),
        "work_conditions": (work, work_detail),
        "culture_risk": (culture, {"signals": risk_signals}),
    }
    raw = sum(score * weights[key] / 100.0 for key, (score, _) in dimensions.items())
    score = max(0, min(100, int(round(raw))))
    confidence = _confidence(result, salary_min, salary_max, raw_jd, stage_detail, req_detail)
    verdict = "skip" if blockers else ("go" if score >= 75 else "improve" if score >= 60 else "skip")
    breakdown = {
        "formula_version": FORMULA_VERSION,
        "weights": weights,
        "dimensions": {
            key: {"score": round(value, 1), "weight": weights[key],
                  "weighted": round(value * weights[key] / 100.0, 2), **detail}
            for key, (value, detail) in dimensions.items()
        },
        "hard_blockers": blockers,
        "confidence": confidence,
    }
    return score, verdict, breakdown, round(raw, 2)
# ────────────────────────────────────────────────────────────


def load_profile_summary() -> str:
    # compact：砍冗長敘事欄位；facts_only：再砍面試敘事/元標籤，只留要件匹配事實。
    # gap 輸出只有 requirements/matched/gaps/理由，recommend_score 是規則公式算的，
    # profile 只需可驗證的匹配事實（16.4k → 約 10k 字元）
    return build_deid_profile(compact=True, facts_only=True)


def build_prompt(profile_yaml: str, row) -> str:
    jd = (row["raw_jd"] or "")[:MAX_JD_CHARS]
    return f"""あなたは経験豊富な転職エージェントです。候補者プロフィールと求人票(JD)を読み、
「候補者が満たす要件 / 不足する要件 / 推薦度」を厳密に評価してください。

# 候補者プロフィール（唯一の真実。ここに無い事実は創作しないこと）
```yaml
{profile_yaml}
```

# 求人票
タイトル: {row['title']}
会社: {row['company'] or '不明'}
本文:
{jd or '(本文なし)'}

# 出力（JSON のみ。前後に説明やコードフェンス以外の文字を書かない）
{{
  "requirements": ["JD が求める主要要件を最大8個、各50字以内で原文に即して"],
  "matched": ["候補者が満たす要件＋根拠（最大6個、各60字以内）"],
  "gaps": ["不足/不明な要件＋なぜギャップか（最大6個、各60字以内）"],
  "requirement_assessments": [
    {{"index": 1, "importance": "must|important|preferred", "status": "matched|partial|gap"}}
  ],
  "recommend_reason": "応募の可否を判断する理由を日本語1文で"
}}

requirement_assessments は requirements の全項目を index=1 から順に1回ずつ評価すること。
importance は採用の必須条件=must、主要条件=important、歓迎条件=preferred。status は
プロフィールに明確な根拠がある=matched、一部のみ根拠がある=partial、根拠がない=gap。

{lang_directive("gap_json")}"""


def _extract_json(text: str) -> dict:
    """LLM 輸出可能含 code fence / 前後雜訊，抽出第一個 JSON 物件。"""
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", t, re.DOTALL)
    if m:
        return json.loads(m.group(0))
    raise ValueError(f"無法從 LLM 輸出抽出 JSON：\n{text[:300]}")


def _fetch_row(job_id: int):
    with connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        raise SystemExit(f"job_id {job_id} 不存在")
    return row


# 驗收條件：miko_gateway 據此判斷 brain 回應是否完整（recommend_reason 是
# JSON 最後一個 key，缺 = 輸出被截斷），不達標 gateway 自動換下一個 brain。
_ACCEPT = {"includesAll": ['"recommend_reason"']}
_MAX_ATTEMPTS = 3
_RETRY_BACKOFF_SEC = 5  # gateway 偶發 500（過載/brain 瞬斷）留時間恢復，同 provider 內重試


def _call_json(prompt: str, timeout: int = 180) -> dict:
    """呼叫 LLM 並抽 JSON。gateway 暫時性錯誤（500）或輸出截斷/夾雜雜訊時重試。"""
    last_err: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        if attempt > 0:
            time.sleep(_RETRY_BACKOFF_SEC)
        try:
            raw = call(prompt, timeout=timeout, model=MODEL, accept=_ACCEPT)
        except RuntimeError as e:  # 所有 provider 失敗（如 gateway 500），非 JSON 問題
            last_err = e
            continue
        try:
            return _extract_json(raw)
        except (ValueError, json.JSONDecodeError) as e:
            last_err = e
    raise last_err


def analyze_one(row, profile_yaml: str, dry_run: bool = False) -> dict:
    if not (row["raw_jd"] or "").strip():
        raise SystemExit(
            f"job_id {row['id']} 無 raw_jd 內文，無法 gap 分析"
            f"（來源 {row['source']} 可能未抓 JD 本文）"
        )
    prompt = build_prompt(profile_yaml, row)
    result = _call_json(prompt)

    # AI のテキスト分析後、スコアは規則公式で上書き
    score, verdict, breakdown, raw = compute_score(
        result,
        row["salary_max"],
        row["raw_jd"] or "",
        salary_min=row["salary_min"],
        title=row["title"],
        remote=row["remote"],
        sponsor_visa=row["sponsor_visa"],
    )
    result["recommend_score"] = score
    result["recommend_raw"] = raw
    result["verdict"] = verdict
    result["recommend_formula_version"] = FORMULA_VERSION
    result["recommend_breakdown"] = breakdown
    # 保留舊 key，供既有 dashboard / 匯出讀取；正式明細以 recommend_breakdown 為準。
    result["sweet_spot"] = breakdown["dimensions"]["company_product_stage"]

    if not dry_run:
        update_gap_analysis(row["id"], json.dumps(result, ensure_ascii=False))
        write_gap_md(row, result)
    return result


def write_gap_md(row, result: dict) -> Path:
    OUTPUT_DIR.mkdir(exist_ok=True)
    path = OUTPUT_DIR / f"gap-{row['id']}.md"
    lines = [
        locale_text("gap_md_title", id=row["id"], title=row["title"]),
        "",
        locale_text("gap_md_company", company=row["company"] or "—"),
        locale_text("gap_md_score", score=row["score"], rec=result.get("recommend_score", "?")),
        locale_text("gap_md_url", url=row["url"]),
        "",
        f"> {result.get('recommend_reason', '')}",
        "",
        locale_text("gap_md_requirements"),
        *[f"- {x}" for x in result.get("requirements", [])],
        "",
        locale_text("gap_md_matched"),
        *[f"- {x}" for x in result.get("matched", [])],
        "",
        locale_text("gap_md_gaps"),
        *[f"- {x}" for x in result.get("gaps", [])],
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def analyze_top(top: int, min_score: int, dry_run: bool = False,
                source_like: str | None = None, skip_done: bool = True,
                scan_limit: int | None = None,
                failed_ids: set[int] | None = None) -> tuple[int, int]:
    """分析一批高分職缺，回傳 (成功筆數, 實際嘗試筆數)。

    scan_limit: 候選掃描窗口（預設 top*5，只在最高分區找；backfill 傳大值掃全表）
    failed_ids: 跨批共用的失敗 id 集合 — 選件時排除，新失敗會加入（backfill 防重試迴圈）
    """
    profile_yaml = load_profile_summary()
    rows = top_scored(limit=scan_limit or top * 5, min_score=min_score,
                      source_like=source_like)
    if skip_done:
        rows = [r for r in rows if not (r["gap_analysis"] or "").strip()]
    if failed_ids:
        rows = [r for r in rows if r["id"] not in failed_ids]
    # 只取有 raw_jd 內文的（bizreach/linkedin 多無本文）
    rows = [r for r in rows if (r["raw_jd"] or "").strip()][:top]
    if not rows:
        print(f"[gap_analyzer] 無符合條件職缺（min_score={min_score} 且有 JD 本文）")
        return 0, 0
    batch_id = None  # 首筆成功才建 batch，避免整批失敗留下空 batch 記錄
    done = 0
    items = []  # 供批次報告 summarizer
    for r in rows:
        try:
            res = analyze_one(r, profile_yaml, dry_run)
            if not dry_run:
                if batch_id is None:
                    batch_id = create_gap_batch(source_like, min_score)
                assign_gap_batch(r["id"], batch_id)
            items.append({
                "id": r["id"], "company": r["company"], "title": r["title"],
                "score": r["score"], "rec": res.get("recommend_score"),
                "sal": [r["salary_min"], r["salary_max"]],
                "gaps": res.get("gaps"), "reason": res.get("recommend_reason"),
            })
            print(f"  ✓ [{r['id']}] score={r['score']} 推薦度={res.get('recommend_score')} {r['title'][:40]}")
            done += 1
        except Exception as e:  # 單筆失敗不中斷整批
            if failed_ids is not None:
                failed_ids.add(r["id"])
            print(f"  ✗ [{r['id']}] 失敗：{e}")

    if batch_id is not None and items:
        try:
            print(f"[gap_analyzer] 生成批次 #{batch_id} 推薦度報告…")
            summary = summarize_batch(profile_yaml, items)
            finalize_gap_batch(batch_id, done, json.dumps(summary, ensure_ascii=False))
            print(f"[gap_analyzer] 批次 #{batch_id} 報告已落庫")
        except Exception as e:
            finalize_gap_batch(batch_id, done, json.dumps({"error": str(e)}, ensure_ascii=False))
            print(f"[gap_analyzer] ✗ 批次報告生成失敗：{e}")

    print(f"[gap_analyzer] 完成 {done}/{len(rows)} 筆")
    return done, len(rows)


def backfill(min_score: int, batch_size: int = 20, max_jobs: int = 0,
             source_like: str | None = None, dry_run: bool = False) -> int:
    """迴圈分批清空所有未跑 gap 的職缺（每批一個 gap_batch + 推薦度報告）。

    整批 0 成功即中止（LLM 不可用時防空轉，下輪排程自動補跑）。
    max_jobs=0 表示不設上限，跑到清空為止。
    """
    total = 0
    failed_ids: set[int] = set()
    while True:
        size = batch_size
        if max_jobs:
            size = min(batch_size, max_jobs - total)
            if size <= 0:
                print(f"[gap_backfill] 達本輪上限 {max_jobs} 筆，停止")
                break
        done, attempted = analyze_top(
            size, min_score, dry_run=dry_run, source_like=source_like,
            scan_limit=100_000, failed_ids=failed_ids)
        if attempted == 0:
            print(f"[gap_backfill] 已清空。本輪共 {total} 筆"
                  + (f"（{len(failed_ids)} 筆失敗待下輪）" if failed_ids else ""))
            break
        total += done
        if done == 0:
            print(f"[gap_backfill] 整批 {attempted} 筆全失敗（LLM 不可用？），"
                  f"中止本輪，已完成 {total} 筆")
            break
        if dry_run:  # dry-run 不落庫，跑一批示範即止（否則同批重複選件）
            break
    return total


def main() -> None:
    ap = argparse.ArgumentParser(description="Gap 分析器（Haiku，我 vs JD 要求）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--job-id", type=int)
    g.add_argument("--top", type=int, help="對高分 top N 跑（搭配 --min-score）")
    g.add_argument("--backfill", action="store_true",
                   help="迴圈分批清空所有未跑 gap 的職缺（搭配 --min-score / --max-jobs）")
    ap.add_argument("--min-score", type=int, default=70)
    ap.add_argument("--batch-size", type=int, default=20, help="backfill 每批筆數")
    ap.add_argument("--max-jobs", type=int, default=0, help="backfill 本輪上限（0=不限）")
    ap.add_argument("--source", type=str, default=None, help="SQL LIKE 來源過濾（如 indeed_jp）")
    ap.add_argument("--all", dest="all_jobs", action="store_true", help="包含已跑過 gap 的職缺")
    ap.add_argument("--dry-run", action="store_true", help="不寫 DB / 檔案，只印結果")
    args = ap.parse_args()

    if args.job_id is not None:
        profile_yaml = load_profile_summary()
        row = _fetch_row(args.job_id)
        res = analyze_one(row, profile_yaml, args.dry_run)
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif args.backfill:
        backfill(args.min_score, batch_size=args.batch_size, max_jobs=args.max_jobs,
                 source_like=args.source, dry_run=args.dry_run)
    else:
        analyze_top(args.top, args.min_score, args.dry_run,
                    source_like=args.source, skip_done=not args.all_jobs)


if __name__ == "__main__":
    main()
