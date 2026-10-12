"""既存面接パックの回答を審稿し、口語化・深掘り・再回答まで生成する。

デフォルトは非破壊。既存 01/03_interview_qa.md を読み、pack 内の qa_upgrade/
へ成果物を出す。``--promote`` を明示した場合のみ、バックアップ後に元ファイルへ反映する。

用法:
    python3 -m interview.qa_upgrade --job-id 123
    python3 -m interview.qa_upgrade --job-id 123 --questions 3,5
    python3 -m interview.qa_upgrade --job-id 123 --no-llm
    python3 -m interview.qa_upgrade --job-id 123 --promote
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

from llm import call as llm_call
from tools.deid import build_deid_profile, load_profile
from tools.pii_gate import scrub_for_external
from tracker.db import connect

from .qa_quality import (
    QAItem,
    drill_oral_lints,
    oral_lints,
    parse_drills,
    parse_qa,
    placeholders,
    render_qa,
    unsupported_numbers,
)


ROOT = Path(__file__).resolve().parents[1]
PREP_DIR = ROOT / "output" / "prep"
COMPANY_DIR = ROOT / "interview" / "companies"
SOURCE_NAMES = ("03_interview_qa.md", "01_interview_qa.md")
PROMPT_VERSION = 2
DEFAULT_BATCH = 4
DEFAULT_CRITICAL = 8


REVIEW_PROMPT = """\
あなたは日本企業の中途採用面接官と、面接回答を直す日本語コーチです。
下の既存回答を事実審査し、内容を強くした上で、日本人が実際の面接で話す自然な敬体へ直してください。

# 絶対ルール
1. 「権威素材」だけが事実です。既存回答は審査対象であり、事実の出典ではありません。
2. 権威素材にない数字・経験・役割・成果・顧客名を残したり追加したりしないでください。
3. 不足経験は隠さず、誠実に認めてから、素材にある隣接経験と学び方へつなげてください。
4. 結論を先に一文、その後に2〜3個の要点。本人の行動・判断・結果が区別できるようにします。
5. 各回答は面接で30〜60秒程度。抽象的な自己評価より、実プロジェクトの証拠を優先します。
6. 話し言葉のです・ます調。面接では「御社」を使用します。
7. 「んです」は背景・理由の説明、「ですけれども」は不足の承認や柔らかい転換にだけ自然に使います。
   機械的に追加せず、それぞれ一回答0〜2回まで。官僚的な書面語や暗記調を避けてください。
8. 会社の公開情報は志望理由や仮説には使えますが、本人の実績として混同しないでください。
9. 一文は90字以内。「権威素材」「提供された情報」「回答案」「確認できません」など、
   審稿作業のメタ表現を面接回答に書かないでください。不足は「そこは経験していません」等と本人の言葉で話します。
10. 年収質問では、値を推測せず ``<CURRENT_ANNUAL>万円`` と ``<DESIRED_ANNUAL>万円`` を必ず使います。
    このトークンは外部送信後にローカルで実値へ置換されます。
11. JDの仕事内容・必須・優遇要件と会社の事業段階から、採用側が実際に欲しい人物像を4〜6個の
    「採用シグナル」として内部で固定してください。肩書ではなく、入社後に任せたい判断・行動・成果で表現します。
12. 全回答で、その質問に自然な採用シグナルを最低1個、候補者の具体的証拠または正直なgapとともに示してください。
    無関係な強みを機械的に足したり、全回答で同じ言い回しを反復したりしないでください。
13. 日本人が面接で実際に話す、日常的なビジネス日本語にしてください。履歴書の読み上げや翻訳調にせず、
    「〜してきました」「一方で」「正直に言うと」「〜したいと考えています」等を文脈に応じて自然に使います。
    「当該」「〜において」「寄与」「勘案」「帰属」「推進してまいりました」等の硬い書面語は使いません。
14. 技術用語は必要最小限にし、最初に使う時は非技術者にも分かる言葉で意味を添えてください。
    一文を短くし、3〜5文で一息に話せる回答にします。略語や名詞の羅列は避けます。
15. 出力は候補者がその場で口にする回答そのものです。「面接では〜と答えます」「〜と表現します」
    「資料上では」のようなコーチ目線・審稿目線の説明は禁止し、質問へ直接答えてください。
16. 文法が正しくても、資料用語や翻訳調は避けてください。「責任分界」は「各社がどこまで責任を持つか」、
    「精緻化条件」は「見積を更新する条件」、「近接経験」は「共通する部分が多い経験」のように、
    日本人が会話で使う具体的な表現へ言い換えてください。

# 評価軸
- grounding: 事実根拠
- relevance: 質問とJDへの命中
- ownership: 本人の貢献の明確さ
- evidence: 具体的証拠
- persona_fit: 採用側が欲しい人物像との一貫した接続
- defensibility: 追問への耐性
- spoken_ja: 日本語口語の自然さ

# 権威素材（候補者プロフィールは去識別化済み）
{evidence}

# 審査対象
{drafts}
{feedback}

# 出力（JSONのみ）
{{"items":[{{
  "id": 1,
  "score_before": 0,
  "risk": 0,
  "decision": "KEEP|REWRITE|BLOCK",
  "issues": ["日本語で簡潔に"],
  "evidence_refs": ["権威素材中の項目名"],
  "fit_refs": ["この回答が示す採用シグナル。1〜3個"],
  "conclusion": "結論一文",
  "points": ["要点1", "要点2", "必要なら要点3"]
}}]}}
"""


DRILL_PROMPT = """\
あなたは日本企業の厳しい面接官パネル（HR / PdM / 技術）と、候補者のcareer coachです。
アップグレード済み回答に対し、指定数の深掘り質問を作り、それぞれに事実ベースで回答してください。

# 絶対ルール
1. 「権威素材」だけが事実です。アップグレード済み回答も矛盾確認の対象で、単独の事実出典ではありません。
2. 数字・経験・役割・結果を捏造しない。不足は認め、隣接経験または具体的な学び方へつなげます。
3. D1は基底回答の最も弱い主張を突きます。D2以降は直前の応答中の具体的な語句を anchor にして、さらに深く追います。
4. 質問は、本人の役割、数字の測定、失敗・反例、取捨選択、再現性、未経験領域のどれかを突きます。
5. 回答は結論先行の自然な話し言葉で3〜6文、30〜45秒程度。「御社」を使います。
6. 「んです」「ですけれども」は説明・転換に必要な時だけ使い、濫用しません。
7. 「権威素材」「提供された情報」「回答案」「確認できません」など、生成作業のメタ表現は禁止です。
   不足は「その経験はありません」「正確な数値は手元にありません」と本人の言葉で答えます。
8. 「結論として」「御社では」を全回答の冒頭で反復せず、質問に直接答えてください。
9. 年収回答にある ``<CURRENT_ANNUAL>`` / ``<DESIRED_ANNUAL>`` は正しいローカル置換トークンです。
   値を推測したり、トークンを削除したりしないでください。
10. 追問では、基底回答が示す採用シグナルがこの職務で再現できるか、または不足経験が致命的でないかを
    少なくとも1回は攻撃してください。一般的な自己PRへ逃げず、JD上の責任に結びつけます。
11. 回答は日本人が面接で実際に使う日常的なビジネス日本語にします。
    「現時点の資料では」「再構成です」「帰属させません」のような審稿・書面表現ではなく、
    「正確な数字は手元で確認できていません」「私だけの成果とは言いません」のように本人の言葉で答えます。
12. 「面接では〜と答えます」「〜と話します」のような回答方法の説明ではなく、面接官への実際の返答を
    そのまま書いてください。技術用語は日本語で短く言い換え、必要な場合だけ括弧で補足します。
13. 「責任分界」「近接経験」「精緻化条件」など、文法は正しくても資料的な表現を避けます。
    「誰がどこまで責任を持つか」など、会話で自然な動詞のある文へ直してください。

# 権威素材（去識別化済み）
{evidence}

# 対象回答と必要な追問数
{answers}

# 出力（JSONのみ）
{{"items":[{{
  "id": 1,
  "drills": [{{
    "tag": "HR|PdM|技術",
    "question": "深掘り質問",
    "anchor": "基底回答または直前回答からの短い語句",
    "answer": "結論先行の応答"
  }}]
}}]}}
"""


AUDIT_PROMPT = """\
あなたは日本企業の採用責任者です。権威素材と、アップグレード済み面接QA・深掘り回答を照合してください。
既存QAや生成回答を事実出典として扱わず、次を厳格に監査します。

1. 権威素材にない数字・実績・役割・顧客・経験がないか
2. 基底回答と深掘り回答が矛盾していないか
3. チーム成果を本人だけの成果にしていないか
4. 物流未経験、現場観察、Growth/A-Bテスト等のgapを誤魔化していないか
5. 日本の面接で不自然な書面語、強すぎる断定、冗長な暗記調がないか
6. ``<CURRENT_ANNUAL>`` / ``<DESIRED_ANNUAL>`` はローカル置換前の正規トークンなので、
   不明情報・placeholder・hard finding として扱わないこと
7. JDと会社の事業から推測できる採用人物像が回答間で一貫しているか
8. 全回答が少なくとも1つの採用シグナルを、具体的証拠または正直なgapとともに示しているか

# 権威素材（去識別化済み）
{evidence}

# 監査対象
{output}

# 出力（JSONのみ）
{{"verdict":"PASS|REVIEW","hard_findings":["なければ空配列"],
  "soft_findings":["なければ空配列"],"strongest_answers":["Q番号"],
  "weakest_answers":["Q番号と理由"]}}
"""


def _load_job(job_id: int) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise SystemExit(f"job_id {job_id} が見つかりません")
    return dict(row)


def _pack_for(job_id: int, explicit: Path | None) -> Path:
    if explicit:
        pack = explicit if explicit.is_absolute() else ROOT / explicit
        if not pack.is_dir():
            raise SystemExit(f"面接パックがありません: {pack}")
        return pack
    matches = sorted(p for p in PREP_DIR.glob(f"{job_id}_*") if p.is_dir())
    if len(matches) != 1:
        raise SystemExit(f"job {job_id} の面接パックを一意に特定できません: {matches}")
    return matches[0]


def _source_for(pack: Path, explicit: Path | None) -> Path:
    if explicit:
        source = explicit if explicit.is_absolute() else ROOT / explicit
        if not source.exists():
            raise SystemExit(f"QA source がありません: {source}")
        return source
    for name in SOURCE_NAMES:
        candidate = pack / name
        if candidate.exists():
            return candidate
    raise SystemExit(f"{pack}: 03/01_interview_qa.md がありません")


def _find_facts(company: str, explicit: Path | None) -> tuple[str, str]:
    if explicit:
        path = explicit if explicit.is_absolute() else ROOT / explicit
        return path.read_text(encoding="utf-8"), str(path.relative_to(ROOT))
    needle = re.sub(r"[\s　]|株式会社|有限会社", "", company)
    for path in sorted(COMPANY_DIR.glob("*_facts.md")):
        head = path.read_text(encoding="utf-8")[:800]
        normalized = re.sub(r"[\s　]|株式会社|有限会社", "", head)
        if needle and needle in normalized:
            return path.read_text(encoding="utf-8"), str(path.relative_to(ROOT))
    return "", "なし"


def _json_object(text: str) -> dict[str, Any]:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.DOTALL)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("JSON object が見つかりません")
    data = json.loads(text[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("JSON root が object ではありません")
    return data


def _safe_call(prompt: str, timeout: int, retries: int, validate) -> tuple[dict[str, Any], int]:
    clean_prompt, pii = scrub_for_external(prompt)
    last_error = ""
    for attempt in range(retries + 1):
        try:
            data = _json_object(llm_call(clean_prompt, timeout=timeout))
            validate(data)
            return data, len(set(pii))
        except (RuntimeError, ValueError, KeyError, TypeError) as exc:
            last_error = str(exc)
            if attempt < retries:
                print(f"    … 出力不合格、再試行 {attempt + 1}/{retries}: {last_error}", file=sys.stderr)
    raise RuntimeError(last_error)


def _evidence(job: dict[str, Any], facts: str, profile_data: dict[str, Any]) -> str:
    # 回答監査では失敗・修正経緯などの詳細が重要。compact=True は experience[].highlights
    # を落とすため、QR bug の A/B/C 修正のような正当な根拠まで消えてしまう。
    # PII 安全性は白名單 build_deid_profile 自体で担保し、ここでは省略しない。
    profile = build_deid_profile(profile_data, compact=False)
    gap = job.get("gap_analysis") or "（gap analysis なし）"
    jd = (job.get("raw_jd") or "")[:6000]
    facts = facts[:16000] if facts else "（会社 facts なし）"
    return f"""\
## 候補者の職業事実
```yaml
{profile}
```

## 求人
- 会社: {job.get('company')}
- 職位: {job.get('title')}
- 年収レンジ: {job.get('salary_min')}〜{job.get('salary_max')}万円
### JD
{jd}

## gap analysis
{gap}

## 確認済み会社 facts
{facts}

## 年収質問の安全なローカル置換
年収の実値は外部LLMへ送らない。年収回答では必ず
``<CURRENT_ANNUAL>万円`` / ``<DESIRED_ANNUAL>万円`` を使い、削除・推測しない。
"""


def _replace_comp_tokens(text: str, profile: dict[str, Any]) -> str:
    """LLM に値を見せず、成果物へ出す直前だけ年収トークンをローカル置換。"""

    comp = profile.get("compensation") or {}
    replacements = {
        "<CURRENT_ANNUAL>": str(comp.get("current_annual") or "{{現年収を確認}}"),
        "<DESIRED_ANNUAL>": str(comp.get("desired_annual") or "{{希望年収を確認}}"),
    }
    for token, value in replacements.items():
        text = text.replace(token, value)
    return text


def _local_comp_source(profile: dict[str, Any]) -> str:
    """数字 audit 専用。外部 prompt には連結しない。"""

    comp = profile.get("compensation") or {}
    return " ".join(str(comp[k]) for k in ("current_annual", "desired_annual") if comp.get(k) is not None)


def _local_numeric_sources(profile: dict[str, Any], resume_path: Path | None = None) -> str:
    """ローカル数字監査用の権威素材。外部 prompt には絶対に連結しない。"""

    sources = [_local_comp_source(profile)]
    resume_path = resume_path or ROOT / "resume" / "jp" / "data.yaml"
    if resume_path.is_file():
        sources.append(resume_path.read_text(encoding="utf-8"))
    return "\n".join(sources)


def _draft_block(items: list[QAItem]) -> str:
    parts: list[str] = []
    for item in items:
        parts += [f"## ID {item.qid}", f"質問: {item.question}", f"結論: {item.conclusion}"]
        parts += [f"要点{idx}: {point}" for idx, point in enumerate(item.points, start=1)]
        parts.append("")
    return "\n".join(parts)


def _validate_review(data: dict[str, Any], ids: set[int]) -> None:
    rows = data.get("items")
    if not isinstance(rows, list) or {int(x["id"]) for x in rows} != ids:
        raise ValueError("review items の ID が入力と一致しません")
    for row in rows:
        if row.get("decision") not in {"KEEP", "REWRITE", "BLOCK"}:
            raise ValueError("decision が不正です")
        for field in ("score_before", "risk"):
            if not isinstance(row.get(field), (int, float)) or not 0 <= row[field] <= 100:
                raise ValueError(f"{field} は0〜100の数値が必要です")
        if not isinstance(row.get("issues"), list) or not isinstance(row.get("evidence_refs"), list):
            raise ValueError("issues / evidence_refs は配列が必要です")
        fit_refs = row.get("fit_refs")
        if (not isinstance(fit_refs, list) or not 1 <= len(fit_refs) <= 3
                or not all(isinstance(x, str) and x.strip() for x in fit_refs)):
            raise ValueError("fit_refs は1〜3個の採用シグナルが必要です")
        if not isinstance(row.get("conclusion"), str) or not row["conclusion"].strip():
            raise ValueError("conclusion がありません")
        if not isinstance(row.get("points"), list) or not 2 <= len(row["points"]) <= 3:
            raise ValueError("points は2〜3個必要です")
        answer = "\n".join([row["conclusion"], *[str(x) for x in row["points"]]])
        if any(word in answer for word in ("権威素材", "提供された情報", "回答案", "NG回答", "NG例")):
            raise ValueError("面接回答に審稿メタ表現が残っています")
        if any(len(s.strip()) > 90 for s in re.split(r"[。！？\n]", answer)):
            raise ValueError("90字超の文があります")


def _validate_drills(data: dict[str, Any], expected: dict[int, int]) -> None:
    rows = data.get("items")
    if not isinstance(rows, list) or {int(x["id"]) for x in rows} != set(expected):
        raise ValueError("drill items の ID が入力と一致しません")
    for row in rows:
        drills = row.get("drills")
        qid = int(row["id"])
        if not isinstance(drills, list) or len(drills) != expected[qid]:
            raise ValueError(f"Q{qid}: drills 数が不正です")
        for drill in drills:
            if drill.get("tag") not in {"HR", "PdM", "技術"}:
                raise ValueError(f"Q{qid}: tag が不正です")
            if not all(isinstance(drill.get(k), str) and drill[k].strip()
                       for k in ("question", "anchor", "answer")):
                raise ValueError(f"Q{qid}: drill 欄が不足しています")
            if any(word in drill["answer"] for word in ("権威素材", "提供された情報", "回答案")):
                raise ValueError(f"Q{qid}: drill に審稿メタ表現があります")


def _validate_audit(data: dict[str, Any]) -> None:
    if data.get("verdict") not in {"PASS", "REVIEW"}:
        raise ValueError("audit verdict が不正です")
    if not isinstance(data.get("hard_findings"), list) or not isinstance(data.get("soft_findings"), list):
        raise ValueError("audit findings が配列ではありません")


def _updated_item(original: QAItem, review: dict[str, Any]) -> QAItem:
    return QAItem(
        qid=original.qid,
        section=original.section,
        question=original.question,
        conclusion=review["conclusion"].strip(),
        points=[str(p).strip() for p in review["points"]],
        keyword=original.keyword,
    )


def _critical_ids(items: list[QAItem], reviews: dict[int, dict[str, Any]], limit: int) -> set[int]:
    keywords = ("志望動機", "強み", "失敗", "AI", "生成", "物流", "倉庫", "未経験",
                "リサーチ", "KPI", "A/B", "採る理由", "貢献")
    ranked = []
    for item in items:
        risk = int(reviews[item.qid].get("risk") or 0)
        boost = 25 if any(k.lower() in item.question.lower() for k in keywords) else 0
        ranked.append((risk + boost, item.qid))
    return {qid for _, qid in sorted(ranked, reverse=True)[:min(limit, len(items))]}


def _review_markdown(source: Path, items: list[QAItem], reviews: dict[int, dict[str, Any]],
                     override_ids: set[int] | None = None) -> str:
    override_ids = override_ids or set()
    lines = ["# 面接回答アップグレード — 審査レポート", "",
             f"対象: `{source.name}` / 全{len(items)}問", "",
             ("人間確認済み override: " + ", ".join(f"Q{x}" for x in sorted(override_ids))
              + "。最終判定は `04_audit.md` を参照。") if override_ids else "人間確認済み override: （なし）",
             "",
             "| Q | 判定 | Before | Risk | 採用シグナル | 主な修正点 |",
             "|---:|---|---:|---:|---|---|"]
    for item in items:
        row = reviews[item.qid]
        issues = " / ".join(str(x).replace("|", "｜") for x in row.get("issues") or []) or "軽微な口語調整"
        fit_refs = " / ".join(str(x).replace("|", "｜") for x in row.get("fit_refs") or []) or "未設定"
        decision = "HUMAN-OVERRIDE" if item.qid in override_ids else row["decision"]
        lines.append(
            f"| {item.qid} | {decision} | {row.get('score_before', '?')} | "
            f"{row.get('risk', '?')} | {fit_refs} | {issues} |"
        )
    lines += ["", "## BLOCK / 高リスク項目", ""]
    risky = [i for i in items if reviews[i.qid]["decision"] == "BLOCK" or int(reviews[i.qid].get("risk") or 0) >= 70]
    if not risky:
        lines.append("（なし）")
    for item in risky:
        row = reviews[item.qid]
        lines += [f"### Q{item.qid}. {item.question}", "",
                  *[f"- {x}" for x in row.get("issues") or []], ""]
    return "\n".join(lines).rstrip() + "\n"


def _drill_markdown(company: str, items: list[QAItem], drills: dict[int, list[dict[str, Any]]],
                    critical: set[int]) -> str:
    lines = [f"# {company} — 深掘り想定問答（回答付き）", "",
             "基底は内容・口語アップグレード版。★は三層深掘り対象。", ""]
    for item in items:
        lines += ["---", "", f"## Q{item.qid}. {item.question}{' ★' if item.qid in critical else ''}", ""]
        for idx, drill in enumerate(drills.get(item.qid, []), start=1):
            lines += [f"### D{idx}. {drill['question']}（{drill['tag']}）", "",
                      f"> {drill['answer'].strip()}", ""]
    return "\n".join(lines).rstrip() + "\n"


def _answer_and_drill_text(items: list[QAItem], drills: dict[int, list[dict[str, Any]]]) -> str:
    parts: list[str] = []
    for item in items:
        parts += [f"Q{item.qid} {item.question}", item.answer_text]
        for idx, drill in enumerate(drills.get(item.qid, []), start=1):
            parts += [f"D{idx} {drill['question']}", drill["answer"]]
    return "\n".join(parts)


def _audit_markdown(audit: dict[str, Any], numeric: list[str], lints: list[str],
                    unresolved: list[str], pii_count: int) -> str:
    hard = [*audit.get("hard_findings", [])]
    if numeric:
        hard.append("権威素材にない数値候補: " + ", ".join(numeric))
    lines = ["# 面接 QA アップグレード — 最終監査", "",
             f"結論: **{'REVIEW' if hard else audit.get('verdict', 'REVIEW')}**", "",
             f"外部送信前 PII scrub: {pii_count} 件（値は記録しない）", "",
             "## Hard findings", ""]
    lines += [f"- {x}" for x in hard] or ["（なし）"]
    lines += ["", "## Soft findings", ""]
    soft = [*audit.get("soft_findings", []), *lints]
    lines += [f"- {x}" for x in soft] or ["（なし）"]
    lines += ["", "## 未解決 placeholder", ""]
    lines += [f"- `{x}`" for x in unresolved] or ["（なし）"]
    lines += ["", "## 強い回答", ""]
    lines += [f"- {x}" for x in audit.get("strongest_answers", [])] or ["（未指定）"]
    lines += ["", "## 優先して人間確認する回答", ""]
    lines += [f"- {x}" for x in audit.get("weakest_answers", [])] or ["（なし）"]
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="既存面接QAの審稿・口語化・深掘り再生成")
    parser.add_argument("--job-id", type=int, required=True)
    parser.add_argument("--pack", type=Path, default=None)
    parser.add_argument("--source", type=Path, default=None)
    parser.add_argument("--facts", type=Path, default=None)
    parser.add_argument("--questions", default=None, help="一部だけ再生成（例: 3,5）。初回は全問のみ")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH)
    parser.add_argument("--critical", type=int, default=DEFAULT_CRITICAL,
                        help="三層深掘りにする高リスク問題数")
    parser.add_argument("--per-critical", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=420)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--audit-only", action="store_true",
                        help="既存 checkpoint + overrides から再描画し、最終監査だけ実行")
    parser.add_argument("--overrides", type=Path, default=None,
                        help="人間確認済みの回答・深掘り上書き JSON（既定: qa_upgrade/overrides.json）")
    parser.add_argument("--promote", action="store_true")
    args = parser.parse_args()

    job = _load_job(args.job_id)
    pack = _pack_for(args.job_id, args.pack)
    source = _source_for(pack, args.source)
    source_text = source.read_text(encoding="utf-8")
    all_items = parse_qa(source_text)
    if not all_items:
        raise SystemExit(f"{source}: 標準QA形式を解析できません")

    selected_ids = {x.qid for x in all_items}
    if args.questions:
        selected_ids = {int(x) for x in args.questions.split(",") if x.strip()}
        missing = selected_ids - {x.qid for x in all_items}
        if missing:
            raise SystemExit(f"存在しない質問ID: {sorted(missing)}")

    facts, facts_source = _find_facts(job.get("company") or "", args.facts)
    profile_data = load_profile()
    evidence = _evidence(job, facts, profile_data)
    out_dir = pack / "qa_upgrade"
    out_dir.mkdir(parents=True, exist_ok=True)
    prompt_dir = out_dir / "_prompts"
    prompt_dir.mkdir(exist_ok=True)
    selected_items = [item for item in all_items if item.qid in selected_ids]
    audit_report = out_dir / "04_audit.md"
    feedback = ""
    if args.questions and audit_report.exists():
        feedback = ("\n# 前回監査（今回選択した設問に関する指摘をすべて直すこと）\n"
                    + audit_report.read_text(encoding="utf-8")[:14000])

    if args.no_llm:
        for offset in range(0, len(selected_items), args.batch_size):
            batch = selected_items[offset:offset + args.batch_size]
            prompt = REVIEW_PROMPT.format(evidence=evidence, drafts=_draft_block(batch), feedback=feedback)
            clean, _ = scrub_for_external(prompt)
            (prompt_dir / f"review_{batch[0].qid:02d}_{batch[-1].qid:02d}.txt").write_text(clean, encoding="utf-8")
        print(f"✓ review prompts: {prompt_dir.relative_to(ROOT)}")
        return 0

    review_checkpoint = out_dir / "review_checkpoint.json"
    reviews: dict[int, dict[str, Any]] = {}
    if args.questions or args.audit_only:
        if not review_checkpoint.exists():
            raise SystemExit("部分更新/audit-only には review_checkpoint.json が必要です")
        reviews = {int(k): v for k, v in json.loads(review_checkpoint.read_text(encoding="utf-8")).items()}
        if set(reviews) != {x.qid for x in all_items}:
            raise SystemExit("review checkpoint が全設問を含んでいません")

    pii_total = 0
    if not args.audit_only:
        print(f"審稿: {len(selected_items)}問 / batch={args.batch_size}"
              + ("（部分修復）" if args.questions else ""))
        for offset in range(0, len(selected_items), args.batch_size):
            batch = selected_items[offset:offset + args.batch_size]
            print(f"  review Q{batch[0].qid}〜Q{batch[-1].qid} …", flush=True)
            prompt = REVIEW_PROMPT.format(evidence=evidence, drafts=_draft_block(batch), feedback=feedback)
            data, pii = _safe_call(prompt, args.timeout, args.retries,
                                   lambda d, ids={x.qid for x in batch}: _validate_review(d, ids))
            pii_total += pii
            for row in data["items"]:
                reviews[int(row["id"])] = row
            review_checkpoint.write_text(json.dumps(reviews, ensure_ascii=False, indent=2), encoding="utf-8")

    upgraded = [_updated_item(item, reviews[item.qid]) for item in all_items]
    critical = _critical_ids(upgraded, reviews, max(0, args.critical))
    drill_checkpoint = out_dir / "drill_checkpoint.json"
    drills: dict[int, list[dict[str, Any]]] = {}
    if args.audit_only:
        if not drill_checkpoint.exists():
            raise SystemExit("audit-only には drill_checkpoint.json が必要です")
        drills = {int(k): v for k, v in json.loads(drill_checkpoint.read_text(encoding="utf-8")).items()}
    else:
        print(f"深掘り: 全問1問 + 高リスク{len(critical)}問は{args.per_critical}問")
        for offset in range(0, len(upgraded), args.batch_size):
            batch = upgraded[offset:offset + args.batch_size]
            counts = {x.qid: (args.per_critical if x.qid in critical else 1) for x in batch}
            answer_parts = []
            for item in batch:
                answer_parts.append(
                    f"## ID {item.qid} / 追問数 {counts[item.qid]}\n質問: {item.question}\n回答:\n{item.answer_text}")
            print(f"  drill Q{batch[0].qid}〜Q{batch[-1].qid} …", flush=True)
            prompt = DRILL_PROMPT.format(evidence=evidence, answers="\n\n".join(answer_parts))
            data, pii = _safe_call(prompt, args.timeout, args.retries,
                                   lambda d, expected=counts: _validate_drills(d, expected))
            pii_total += pii
            for row in data["items"]:
                drills[int(row["id"])] = row["drills"]
            drill_checkpoint.write_text(json.dumps(drills, ensure_ascii=False, indent=2), encoding="utf-8")

    override_path = args.overrides or (out_dir / "overrides.json")
    if not override_path.is_absolute():
        override_path = ROOT / override_path
    override_question_ids: set[int] = set()
    if override_path.exists():
        override_data = json.loads(override_path.read_text(encoding="utf-8"))
        question_overrides = {int(k): v for k, v in (override_data.get("questions") or {}).items()}
        override_question_ids = set(question_overrides)
        upgraded = [
            QAItem(
                qid=item.qid, section=item.section, question=item.question,
                conclusion=question_overrides.get(item.qid, {}).get("conclusion", item.conclusion),
                points=question_overrides.get(item.qid, {}).get("points", item.points),
            ) for item in upgraded
        ]
        for key, value in (override_data.get("drills") or {}).items():
            drills[int(key)] = value
        print(f"✓ human overrides: {override_path.relative_to(ROOT)}")

    upgraded_md_safe = render_qa(job.get("company") or pack.name, job.get("title") or "",
                                 date.today().isoformat(), upgraded)
    drill_md_safe = _drill_markdown(job.get("company") or pack.name, upgraded, drills, critical)
    audit_target_safe = _answer_and_drill_text(upgraded, drills)

    print("最終監査 …", flush=True)
    audit_prompt = AUDIT_PROMPT.format(evidence=evidence, output=audit_target_safe)
    audit, pii = _safe_call(audit_prompt, args.timeout, args.retries, _validate_audit)
    pii_total += pii
    upgraded_md = _replace_comp_tokens(upgraded_md_safe, profile_data)
    drill_md = _replace_comp_tokens(drill_md_safe, profile_data)
    audit_target = _replace_comp_tokens(audit_target_safe, profile_data)
    numeric = unsupported_numbers(audit_target, evidence + "\n" + _local_numeric_sources(profile_data))
    lints = [
        *oral_lints(parse_qa(upgraded_md)),
        *drill_oral_lints(parse_drills(drill_md)),
    ]
    unresolved = placeholders(upgraded_md + drill_md)

    (out_dir / "01_review.md").write_text(
        _review_markdown(source, all_items, reviews, override_question_ids), encoding="utf-8")
    (out_dir / "02_interview_qa_upgraded.md").write_text(upgraded_md, encoding="utf-8")
    (out_dir / "03_drilldown_qa.md").write_text(drill_md, encoding="utf-8")
    audit_md = _audit_markdown(audit, numeric, lints, unresolved, pii_total)
    (out_dir / "04_audit.md").write_text(audit_md, encoding="utf-8")

    manifest = {
        "version": PROMPT_VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "job_id": args.job_id,
        "pack": str(pack.relative_to(ROOT)),
        "source": source.name,
        "source_sha256": hashlib.sha256(source_text.encode()).hexdigest(),
        "facts_source": facts_source,
        "questions": len(upgraded),
        "critical_ids": sorted(critical),
        "numeric_suspects": numeric,
        "oral_lints": lints,
        "llm_verdict": audit.get("verdict"),
        "hard_findings": audit.get("hard_findings") or [],
        "overrides": str(override_path.relative_to(ROOT)) if override_path.exists() else None,
        "promoted": False,
    }

    hard_fail = bool(numeric or lints or audit.get("hard_findings"))
    if args.promote:
        if hard_fail:
            manifest["promotion_blocked"] = True
            print("✗ hard finding があるため promote を拒否しました", file=sys.stderr)
        else:
            backup_dir = out_dir / "backups"
            backup_dir.mkdir(exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup = backup_dir / f"{source.stem}.{stamp}{source.suffix}"
            shutil.copy2(source, backup)
            shutil.copy2(out_dir / "02_interview_qa_upgraded.md", source)
            manifest["promoted"] = True
            manifest["backup"] = str(backup.relative_to(ROOT))
            print(f"✓ promote: {source.relative_to(ROOT)}（backup: {backup.relative_to(ROOT)}）")
            if (pack / "theater").exists() or (pack / "04_qa_audio.mp3").exists():
                print("⚠ theater/voice 資產未同步，如有用到面接シアター請重跑 "
                      f"theater_script + interview_voice: "
                      f"python3 -m interview.theater_script --job-id {args.job_id} && "
                      f"python3 -m tools.interview_voice --job-id {args.job_id}")

    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                             encoding="utf-8")
    print(f"✓ 出力: {out_dir.relative_to(ROOT)}/")
    print(f"  questions={len(upgraded)} critical={len(critical)} numeric_suspects={len(numeric)} "
          f"oral_lints={len(lints)} verdict={audit.get('verdict')}")
    return 2 if hard_fail else 0


if __name__ == "__main__":
    sys.exit(main())
