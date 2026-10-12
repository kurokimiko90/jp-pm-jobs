"""stage 編排 — 生成 → 機械閘門 → 不合格なら是正フィードバック付きで再生成。

設計方針：
  * 内容尋址キャッシュ（prompt の sha256）— 素材が変わらなければ再呼び出ししない
  * 閘門不合格は最大 RETRY_MAX 回まで是正再生成、それでも駄目なら degraded として記録
  * 途中で失敗しても後続 stage は前段の成果物を読んで続行できる（冪等・再開可能）
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from llm import call as llm_call

from . import context, gates, knowledge, prompts

ROOT = Path(__file__).parent.parent
GROWTH_DIR = ROOT / "output" / "growth"
RETRY_MAX = 2
PREV_MAX_CHARS = 3500   # 前段成果物を prompt に載せるときの上限

# 原 JD 差し込みブロックの目印。後続 stage が前段成果物を読むときはここを剥がす
# （JD は context.build() 側で別途渡るので、二重に載せて prompt を膨らませない）
JD_BLOCK_START = "<!-- JD-RAW:START -->"
JD_BLOCK_END = "<!-- JD-RAW:END -->"
_JD_BLOCK_RE = re.compile(
    re.escape(JD_BLOCK_START) + r".*?" + re.escape(JD_BLOCK_END) + r"\n*", re.S)


@dataclass
class StageResult:
    name: str
    status: str            # ok | degraded | cached | skipped | failed
    attempts: int = 0
    errors: list[str] = field(default_factory=list)
    path: str = ""
    elapsed: float = 0.0
    warnings: list[str] = field(default_factory=list)


def _slug(text: str) -> str:
    s = re.sub(r"株式会社|有限会社|\s+", "", text or "company")
    s = re.sub(r"[^\w一-鿿぀-ヿa-zA-Z0-9]", "", s)
    return s[:20] or "company"


def pack_dir(job: dict) -> Path:
    return GROWTH_DIR / f"{job['id']}_{_slug(job.get('company') or '')}"


def _cache_path(pdir: Path, stage: str) -> Path:
    return pdir / "_cache" / f"{stage}.json"


def _read_prev(pdir: Path, stage: str) -> str:
    """前段成果物を prompt 用に読み込む（無ければ空、長すぎれば頭を切る）。"""
    meta = prompts.STAGES.get(stage)
    if not meta:
        return ""
    path = pdir / meta["file"]
    if not path.exists():
        return "（未生成 — この段の内容は自分で JD から補完すること）"
    text = _JD_BLOCK_RE.sub("", path.read_text(encoding="utf-8"))
    if len(text) > PREV_MAX_CHARS:
        text = text[:PREV_MAX_CHARS] + "\n…（以下略）"
    return text


def build_prompt(job: dict, stage: str, pdir: Path, facts: str,
                 feedback: str = "") -> str:
    meta = prompts.STAGES[stage]
    tags = knowledge.classify_jd(context.jd_text(job))
    # 前半 5 stage は「この仕事をどう遂行するか」の客観設計なので応募者情報を載せない
    # （prompt が 3.5 倍に膨らみ、かつ論点が「自分語り」に引きずられるため）
    needs_profile = bool(meta.get("needs_profile"))
    ctx = context.build(job, facts, with_profile=needs_profile,
                        with_gap=needs_profile)

    fields = {
        "context": ctx,
        "common_rules": prompts.COMMON_RULES,
        "tags": json.dumps(tags, ensure_ascii=False),
        "frameworks": "",
        "cases": "",
        "reports": "",
    }
    for dep in meta["deps"]:
        fields[f"prev_{dep}"] = _read_prev(pdir, dep)

    if stage == "metrics":
        fields["frameworks"] = knowledge.render_frameworks(_framework_keys(tags))
    if stage == "benchmark":
        fields["cases"] = knowledge.render_cases(knowledge.select_cases(tags, limit=9))
    if stage == "cadence":
        fields["reports"] = knowledge.render_reports()

    prompt = meta["prompt"].format(**fields)
    if feedback:
        prompt += (
            "\n\n# ★前回の出力が機械閘門で不合格になった。以下を必ず直して書き直すこと★\n"
            f"{feedback}\n"
            "（指摘されていない部分の品質は落とさないこと。全文を書き直す。）\n"
        )
    return prompt


def _framework_keys(tags: dict) -> list[str]:
    keys = ["north_star", "experiment_design"]
    models = set(tags.get("business_model") or [])
    if "ai_product" in models:
        keys.append("ai_product_metrics")
    if models & {"b2b_saas", "enterprise"}:
        keys.append("saas_unit_economics")
    if models & {"plg", "b2c"}:
        keys += ["plg_funnel", "aarrr"]
    keys.append("rice_prioritization")
    return list(dict.fromkeys(keys))


def _accept_for(spec: gates.GateSpec) -> dict:
    """指揮中心の受け入れ条件 — 不合格なら別の brain に自動で切り替わる。"""
    return {
        "minChars": int(spec.min_chars * 0.8),
        "minLines": int(spec.min_lines * 0.8),
        "includesAll": spec.required_sections[:3],
    }


def run_stage(job: dict, stage: str, pdir: Path, facts: str, *,
              force: bool = False, no_llm: bool = False) -> StageResult:
    meta = prompts.STAGES[stage]
    spec: gates.GateSpec = meta["spec"]
    out_path = pdir / meta["file"]
    cache_path = _cache_path(pdir, stage)
    t0 = time.time()

    prompt = build_prompt(job, stage, pdir, facts)
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
    jd_block = _jd_block(job) if meta.get("include_jd") else ""

    if not force and out_path.exists() and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            cached = {}
        if cached.get("digest") == digest and cached.get("status") in ("ok", "degraded"):
            print(f"  ⏭  {stage}: キャッシュ命中（素材変化なし）")
            return StageResult(stage, "cached", cached.get("attempts", 0),
                               cached.get("errors", []), str(out_path))

    if no_llm:
        pdir.joinpath("_prompts").mkdir(parents=True, exist_ok=True)
        (pdir / "_prompts" / f"{stage}.prompt.md").write_text(prompt, encoding="utf-8")
        print(f"  → {stage}: prompt 落地（--no-llm）")
        return StageResult(stage, "skipped", 0, [], "")

    feedback = ""
    last_errors: list[str] = []
    last_warnings: list[str] = []
    text = ""
    for attempt in range(1, RETRY_MAX + 2):
        p = prompt if attempt == 1 else build_prompt(job, stage, pdir, facts, feedback)
        label = "生成" if attempt == 1 else f"是正再生成 {attempt - 1}/{RETRY_MAX}"
        print(f"  … {stage}: {label} 中（最長 {meta['timeout']}s）")
        try:
            text = llm_call(p, timeout=meta["timeout"], accept=_accept_for(spec))
        except Exception as e:
            last_errors = [f"[LLM] 呼び出し失敗: {e}"]
            print(f"  ✗ {stage}: LLM 失敗 — {e}")
            break
        text = _strip_fence(text)
        result = gates.check(text, spec, job)
        if result.ok:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(_with_header(meta, text, jd_block), encoding="utf-8")
            _write_cache(cache_path, digest, "ok", attempt, [])
            print(f"  ✓ {stage}: 閘門通過（{attempt} 回目）→ {meta['file']}")
            if result.warnings:
                print(f"      参考指摘 {len(result.warnings)} 件（非ブロッキング）")
            return StageResult(stage, "ok", attempt, [], str(out_path),
                               time.time() - t0, result.warnings)
        last_errors = result.errors
        last_warnings = result.warnings
        feedback = result.as_feedback()
        print(f"  ⚠ {stage}: 閘門 {len(result.errors)} 件不合格")
        for e in result.errors[:4]:
            print(f"      {e}")

    if text:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        warn = ("\n\n---\n> ⚠ **この文書は機械閘門を通過していない（degraded）。**\n"
                "> 未解決の指摘:\n"
                + "\n".join(f"> - {e}" for e in last_errors) + "\n")
        out_path.write_text(_with_header(meta, text, jd_block) + warn, encoding="utf-8")
        _write_cache(cache_path, digest, "degraded", RETRY_MAX + 1, last_errors)
        print(f"  ⚠ {stage}: degraded で保存 → {meta['file']}")
        return StageResult(stage, "degraded", RETRY_MAX + 1, last_errors,
                           str(out_path), time.time() - t0, last_warnings)

    _write_cache(cache_path, digest, "failed", RETRY_MAX + 1, last_errors)
    return StageResult(stage, "failed", RETRY_MAX + 1, last_errors, "",
                       time.time() - t0)


def _strip_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n", "", t)
        t = re.sub(r"\n```$", "", t)
    return t.strip()


def _jd_block(job: dict) -> str:
    """原 JD 全文をそのまま差し込むセクション（LLM を通さない）。

    コードフェンスで囲うのは、JD 中の記号や見出しが成果物の構造を壊さないため。
    """
    jd = context.jd_text(job).strip()
    if not jd:
        return ""
    raw_len = len(job.get("raw_jd") or "")
    note = ""
    if raw_len > context.JD_MAX_CHARS:
        note = (f"\n> ※ 元 JD は {raw_len} 文字。先頭 {context.JD_MAX_CHARS} 文字までを"
                "解析対象とした（以下も同じ範囲）。\n")
    return (
        f"{JD_BLOCK_START}\n"
        "## 0. 原 JD 全文（解析の入力そのもの）\n"
        "\n"
        f"> 会社: {job.get('company') or '?'} ／ 職位: {job.get('title') or '?'} "
        f"／ job_id={job['id']}\n"
        "> 以下は LLM を通していない原文（取引先ブランド遮蔽のみ適用）。"
        "本文の解析はこの範囲だけを根拠にしている。\n"
        f"{note}"
        "\n```text\n"
        f"{jd}\n"
        "```\n"
        "\n---\n"
        f"{JD_BLOCK_END}\n"
    )


def _with_header(meta: dict, text: str, jd_block: str = "") -> str:
    head = f"# {meta['title']}\n\n_生成日: {date.today().isoformat()}_\n\n"
    return f"{head}{jd_block}{text}\n"


def _write_cache(path: Path, digest: str, status: str, attempts: int,
                 errors: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "digest": digest, "status": status, "attempts": attempts,
        "errors": errors, "at": date.today().isoformat(),
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def write_review(job: dict, pdir: Path, results: list[StageResult]) -> Path:
    """08_review_report.md — 閘門結果の総括と人手確認リスト。"""
    lines = [
        "# 檢證報告 — 機械閘門の結果と人手確認事項",
        "",
        f"_生成日: {date.today().isoformat()} / 対象求人: "
        f"{job.get('company')} {job.get('title')} (job_id={job['id']})_",
        "",
        "## 1. 各段階の閘門結果",
        "",
        "| 段階 | 產出 | 狀態 | 嘗試次數 | 未解決指摘 |",
        "|---|---|---|---|---|",
    ]
    icon = {"ok": "✅ 通過", "cached": "⏭ 快取", "degraded": "⚠ 未通過",
            "failed": "❌ 失敗", "skipped": "— 未執行"}
    for r in results:
        meta = prompts.STAGES.get(r.name, {})
        lines.append(
            f"| {r.name} | {meta.get('file', '-')} | {icon.get(r.status, r.status)} | "
            f"{r.attempts} | {len(r.errors)} |"
        )

    degraded = [r for r in results if r.status in ("degraded", "failed")]
    lines += ["", "## 2. 未解決の指摘", ""]
    if not degraded:
        lines.append("すべての段階が機械閘門を通過した。")
    for r in degraded:
        lines.append(f"### {r.name}")
        for e in r.errors:
            lines.append(f"- {e}")
        lines.append("")

    warned = [r for r in results if r.warnings]
    if warned:
        lines += ["", "## 2b. 参考指摘（非ブロッキング・目視確認推奨）", ""]
        for r in warned:
            lines.append(f"### {r.name}")
            for w in r.warnings:
                lines.append(f"- {w}")
            lines.append("")

    lines += [
        "",
        "## 3. 人手で確認すべきこと（機械では判定できない）",
        "",
        "| # | 確認事項 | なぜ機械では判定できないか | 確認方法 |",
        "|---|---|---|---|",
        "| 1 | `{{要確認}}` の箇所すべて | 会社固有の数字は公開情報を当たるしかない | IR・採用ページ・技術ブログ |",
        "| 2 | 指標の実現可能性 | 社内のデータ基盤の実態は外からは見えない | 面接での逆質問に組み込む |",
        "| 3 | 組織の意思決定構造 | JD には書かれない | 面接で「意思決定はどう行われるか」を聞く |",
        "| 4 | 90 日計画の負荷現実性 | 実際の会議体・割り込み量が不明 | 入職後に主管と再調整する前提で持ち込む |",
        "| 5 | 標竿事例の移植妥当性 | 事業モデルの細部が不明 | 移植改造設計を面接で当ててみる |",
        "",
        "## 4. 事實錨定の範囲",
        "",
        f"- 数字つきで引用してよい他社事例: 案例庫 {len(knowledge.load_cases())} 件のみ",
        "- 会社固有の数字: `--facts` で渡した調査済み事実のみ。未提供なら全て `{{要確認}}`",
        "- 応募者の経験: `data/candidate_profile.yaml` の去識別化済み内容のみ",
        "",
    ]
    path = pdir / "08_review_report.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_readme(job: dict, pdir: Path, results: list[StageResult]) -> Path:
    done = {r.name for r in results if r.status in ("ok", "cached", "degraded")}
    lines = [
        f"# 增長實戰手冊 — {job.get('company')} / {job.get('title')}",
        "",
        f"job_id: {job['id']} ｜ 生成日: {date.today().isoformat()} ｜ "
        f"recommend_score: {job.get('recommend_score')}",
        "",
        "## 讀的順序",
        "",
        "| # | 檔案 | 回答什麼問題 | 狀態 |",
        "|---|---|---|---|",
    ]
    for name in prompts.STAGE_ORDER:
        meta = prompts.STAGES[name]
        mark = "✅" if name in done else "—"
        lines.append(f"| {prompts.STAGE_ORDER.index(name) + 1} | "
                     f"[{meta['file']}]({meta['file']}) | {meta['title']} | {mark} |")
    lines += [
        "| 8 | [08_review_report.md](08_review_report.md) | 哪些地方機器不敢保證、要自己查 | ✅ |",
        "",
        "## 怎麼用",
        "",
        "1. **面試前**：讀 01 → 06 →「面試可用的具體說法」直接背骨架。",
        "2. **面試中**：04 的實施手順、05 的報告目錄，是回答「入社後どう進めますか」的素材。",
        "3. **入職後**：05 當作 30/60/90 日計畫的草稿，跟主管對齊後改成正式版。",
        "4. **平時**：07 是自己的技能補課清單，跟這份工作無關也能用。",
        "",
        "## 重新生成",
        "",
        "```bash",
        f"python3 -m growth {job['id']}                    # 全段（快取命中則跳過）",
        f"python3 -m growth {job['id']} --stage playbook --force   # 只重跑某段",
        f"python3 -m growth {job['id']} --facts path/to/facts.md   # 帶公司調研事實",
        "```",
        "",
    ]
    path = pdir / "README.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def run(job: dict, stage_names: list[str], facts: str = "", *,
        force: bool = False, no_llm: bool = False) -> list[StageResult]:
    pdir = pack_dir(job)
    pdir.mkdir(parents=True, exist_ok=True)
    results: list[StageResult] = []
    for name in stage_names:
        results.append(run_stage(job, name, pdir, facts, force=force, no_llm=no_llm))
    if not no_llm:
        write_review(job, pdir, results)
        write_readme(job, pdir, results)
    return results
