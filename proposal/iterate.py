"""評分駆動の版管理 — 生成 → Telegram 推送 → 人が採点 → 90 点未満なら書き直し。

停止条件は**人間の採点だけ**（0〜100、合格ライン TARGET_SCORE）。紅隊の自己採点は
LLM が自分に付けた点で、`--refine` の判断材料にしかならない — パックを面接へ持ち
込めるかを決めるのは読んだ本人。

流れ:
  python3 -m proposal 123 --iterate      全 stage 生成 → v1 として快照 → Telegram 推送
  （Telegram で読む / 手元で開く）
  python3 -m proposal 123 --score 72 --note "KPI ツリーが浅い"
      → 採点記録 → 72 < 90 なので note を是正指示に翻訳して書き直し
      → v2 として快照 → 再推送
  python3 -m proposal 123 --score 93
      → 合格。完了通知を送って終わり。

全版が versions/v{N}/ に残る（**上書きしない** — どの版が良かったかを後から
並べて見るため）。採点履歴は _scores.jsonl（1 行 1 イベント）。

書き直しの範囲（--scope）:
  auto     … note がスライドの話だけなら deck のみ（LLM 1 call）、それ以外は full
  full     … main_case から下流を全部（main_case/plan90/cards/mapping/redteam/deck ≈ 6 call）
  thinking … 課題仮説そのものが違うとき。hypotheses から下流を全部（≈ 7 call）
  deck     … スライドの構成だけ作り直す
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path

from . import pipeline, prompts, trace

TARGET_SCORE = 90
SCORES_FILE = "_scores.jsonl"
VERSIONS_DIR = "versions"

# 快照に含めるもの = 読み手に渡る成果物すべて（生素材・キャッシュは除く）。
# **音檔は含めない** — 版ごとに 20MB 積むと versions/ が実質使えなくなる。
# 音声は最新版だけ持てば足りる（過去版の音声を聞き比べる使い方をしない）
_SNAPSHOT_PATTERNS = ("0*.md", "10_deck.html", "11_capability_playbook.md",
                      prompts.STAGES["pitch"]["file"], prompts.REVIEW_FILE,
                      "_deck.fields.json", "_deck.ja_review.json", "README.md")

# note がスライドの話だけか（--scope auto の判定語）
_DECK_WORDS = re.compile(r"deck|slide|ppt|スライド|投影|簡報", re.I)

# full 書き直しで回す stage（main_case の是正 → 下流を追随させる順）。
# **playbook は入れない** — deps が persona / product だけなので、main_case から
# 下流を書き直しても素材が変わらない。force で回すと同じものを作り直すだけの
# 1 call になる（playbook が古くなるのは persona か product を回したときで、
# それは通常の stage 実行が拾う）
# pitch は最後（deps が main_case / plan90 / mapping なので、上流が書き直された
# 後でないと古い主張のまま口に出す原稿が残る — 音声にする文書なので一番危ない）
_FULL_STAGES = ("main_case", "plan90", "cards", "mapping", "redteam", "deck",
                "pitch")
# thinking = 課題仮説からやり直す（仮説が違うと main_case をいくら磨いても直らない）
_THINKING_STAGES = ("hypotheses",) + _FULL_STAGES
_SCOPE_STAGES = {"deck": ("deck",), "thinking": _THINKING_STAGES,
                 "full": _FULL_STAGES}


# ── 版管理 ─────────────────────────────────────────────


def versions_dir(pdir: Path) -> Path:
    return pdir / VERSIONS_DIR


def versions(pdir: Path) -> list[int]:
    if not versions_dir(pdir).exists():
        return []
    out = []
    for d in versions_dir(pdir).iterdir():
        m = re.fullmatch(r"v(\d+)", d.name)
        if m and d.is_dir():
            out.append(int(m.group(1)))
    return sorted(out)

def latest_version(pdir: Path) -> int:
    vs = versions(pdir)
    return vs[-1] if vs else 0


def snapshot(pdir: Path) -> int:
    """現在のパックを versions/v{N}/ へ複製する。既存の版は触らない。"""
    n = latest_version(pdir) + 1
    dst = versions_dir(pdir) / f"v{n}"
    dst.mkdir(parents=True, exist_ok=True)
    for pattern in _SNAPSHOT_PATTERNS:
        for src in sorted(pdir.glob(pattern)):
            if src.is_file():
                shutil.copy2(src, dst / src.name)
    total, _ = pipeline.redteam_score(pdir)
    # trace の何行目までがこの版か。「v2 はどの呼び出しで出来たか」を後から引ける
    record(pdir, kind="version", version=n, redteam=total,
           trace_seq=trace.count(pdir))
    return n


# ── 採点履歴 ────────────────────────────────────────────


def scores_path(pdir: Path) -> Path:
    return pdir / SCORES_FILE


def record(pdir: Path, **row) -> None:
    row = {"at": datetime.now().isoformat(timespec="seconds"), **row}
    with scores_path(pdir).open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_events(pdir: Path) -> list[dict]:
    p = scores_path(pdir)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def best_score(pdir: Path) -> tuple[int | None, int | None]:
    """(最高点, その版)。まだ採点が無ければ (None, None)。"""
    best, best_v = None, None
    for e in load_events(pdir):
        if e.get("kind") == "score" and isinstance(e.get("score"), int):
            if best is None or e["score"] > best:
                best, best_v = e["score"], e.get("version")
    return best, best_v


# ── Telegram 推送 ───────────────────────────────────────


def push(job: dict, pdir: Path, version: int) -> bool:
    """版の摘要とファイルを Telegram へ。notify 未設定なら stdout に落ちる。"""
    from notify import send, send_document

    total, axes = pipeline.redteam_score(pdir)
    axes_line = ("紅隊自評 " + f"{total}/70（" +
                 "、".join(f"{k}{v}" for k, v in axes.items()) + "）"
                 if total is not None else "紅隊自評: 未採点")
    best, best_v = best_score(pdir)
    hist = (f"\n過去最高: {best}/100 (v{best_v})" if best is not None else "")
    text = (
        f"{job.get('company')} / {job.get('title')}\n"
        f"版: v{version}｜{axes_line}{hist}\n"
        f"檔案: `{pipeline.pack_dir(job).relative_to(pipeline.ROOT)}/`\n\n"
        f"評分（90 分合格，未達自動重寫）:\n"
        f"  /pscore {job['id']} <0-100> [備註]\n"
        f"  或 python3 -m proposal {job['id']} --score N --note \"...\""
    )
    ok = send(text, title=f"📐 提案パック v{version} [#{job['id']}]")
    deck_html = pdir / prompts.STAGES["deck"]["file"]
    if deck_html.exists():
        send_document(deck_html, caption=f"v{version} スライド（ブラウザで開く／P で PDF）")
    main_md = pdir / prompts.STAGES["main_case"]["file"]
    if main_md.exists():
        send_document(main_md, caption=f"v{version} 主提案")
    return ok


# ── 採点 → 書き直し ─────────────────────────────────────


def _score_feedback(pdir: Path, score: int, note: str) -> str:
    """人間の採点コメントを是正指示に翻訳する。note が無ければ紅隊の低軸で補う。"""
    lines = [f"# ★人間の採点が {score}/100（合格ライン {TARGET_SCORE}）。"
             "以下の指摘を最優先で直し、指摘されていない部分の質は落とさない★"]
    if note.strip():
        lines.append(f"採点者の指摘: {note.strip()}")
    total, axes = pipeline.redteam_score(pdir)
    if axes:
        worst = sorted(axes.items(), key=lambda kv: kv[1])[:2]
        lines.append("参考（紅隊の低評価軸）: "
                     + "、".join(f"{k}（{v}/10）" for k, v in worst))
    return "\n".join(lines)


def _resolve_scope(scope: str, note: str) -> str:
    """auto: note にスライド語（deck/PPT/スライド…）があれば deck だけ直す。

    スライドの見せ方と提案の中身の両方に指摘があるときは --scope full を明示する
    （機械に意図を当てさせない — 判定は「スライド語の有無」だけの単純規則）。
    """
    if scope != "auto":
        return scope
    return "deck" if note.strip() and _DECK_WORDS.search(note) else "full"


def handle_score(job: dict, score: int, note: str = "",
                 scope: str = "auto", facts: str = "") -> int:
    """採点を記録し、未達なら書き直して次の版を推送する。戻り値は exit code。"""
    from notify import send

    pdir = pipeline.pack_dir(job)
    if not pdir.exists():
        print(f"パックが無い: {pdir}。先に python3 -m proposal {job['id']} --iterate")
        return 1
    # 採点対象の版を確定する。--iterate を通さず生成したパックは未快照なので、
    # 採点された状態をまず v1 として残す（採点と成果物の対応が消えないように）
    version = latest_version(pdir) or snapshot(pdir)
    record(pdir, kind="score", version=version, score=score, note=note)
    print(f"  ✓ v{version} に採点 {score}/100 を記録")

    if score >= TARGET_SCORE:
        send(f"{job.get('company')} / {job.get('title')}\n"
             f"v{version} = {score}/100 ≥ {TARGET_SCORE}。この版で確定。\n"
             f"最終版: `{(versions_dir(pdir) / f'v{version}').relative_to(pipeline.ROOT)}/`",
             title=f"✅ 提案パック合格 [#{job['id']}]")
        print(f"  ✅ 合格（{score} ≥ {TARGET_SCORE}）。書き直しはしない。")
        return 0

    resolved = _resolve_scope(scope, note)
    fb = _score_feedback(pdir, score, note)
    stages = _SCOPE_STAGES.get(resolved, _FULL_STAGES)
    print(f"  ↻ {score} < {TARGET_SCORE} — scope={resolved} で書き直し"
          f"（{', '.join(stages)}）")

    results = []
    for name in stages:
        # 是正指示は起点の stage にだけ渡す。下流は上流の改稿を素材に普通に再生成
        # する（全 stage に同じ指示を貼ると、下流が指示への回答文を書き始める）
        extra = fb if name == stages[0] else ""
        results.append(pipeline.run_stage(job, name, pdir, facts, force=True,
                                          extra_feedback=extra))
    full = results + pipeline.existing_results(pdir, {r.name for r in results})
    pipeline.write_review(job, pdir, full, pipeline.capabilities(pdir, job),
                          bool(facts))
    pipeline.write_readme(job, pdir, full)

    new_version = snapshot(pdir)
    push(job, pdir, new_version)
    bad = [r for r in results if r.status in ("degraded", "failed")]
    print(f"  → v{new_version} 快照・推送済み"
          + (f"（⚠ {len(bad)} stage が閘門未通過）" if bad else ""))
    return 1 if bad else 0
