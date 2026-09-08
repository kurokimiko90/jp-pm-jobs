"""LLM 呼び出しの全履歴 — prompt / 応答 / 書いた brain / 時刻を残す。

**なぜ要るか。** 成果物（`05_main_proposal.md` 等）には閘門を通った最終稿しか
残らない。実際に見たいのはそこではなく「1 回目はなぜ落ちたのか」「是正で何が
変わったのか」「どの brain がいつ書いたのか」で、そこは今まで全部捨てていた
（prompt は `--no-llm` のときだけ、しかも同名で上書き）。

置き場所は `output/proposal/{pack}/_trace/`:

    index.jsonl                   1 行 1 呼び出し（grep・統計用の索引）
    prompts/{digest}.md           prompt 全文（内容尋址 — 同一 prompt は 1 つ）
    out/{seq}_{stage}_a{n}.md     応答全文（**閘門を落ちたものも残す**）

閘門を落ちた応答こそ残す価値がある。何を直せば通ったのかは、落ちた本文が無いと
後から分からない。

⚠ **Telegram へは絶対に添付しない。** trace には去識別化後のプロフィール全文と
JD が prompt ごと入っている。`output/` は `.gitignore` / `.publicignore` 済み
なので repo には出ないが、送信経路に載せると意味が無くなる
（`iterate.push` は deck と主提案だけを送る — そこを変えないこと）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent

DIR_NAME = "_trace"
INDEX_FILE = "index.jsonl"


# ── 置き場所 ────────────────────────────────────────────


def trace_dir(pdir: Path) -> Path:
    return pdir / DIR_NAME


def index_path(pdir: Path) -> Path:
    return trace_dir(pdir) / INDEX_FILE


def prompt_path(pdir: Path, digest: str) -> Path:
    return trace_dir(pdir) / "prompts" / f"{digest}.md"


def out_path(pdir: Path, seq: int, stage: str, attempt: int) -> Path:
    return trace_dir(pdir) / "out" / f"{seq:04d}_{stage}_a{attempt}.md"


# ── 書く ───────────────────────────────────────────────


def load(pdir: Path) -> list[dict]:
    p = index_path(pdir)
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def count(pdir: Path) -> int:
    """記録済みの呼び出し数。iterate が版と対応付けるために使う。"""
    return len(load(pdir))


def record(pdir: Path, *, job: dict, stage: str, attempt: int, prompt: str,
           output: str = "", meta: dict | None = None, gate: str = "",
           errors: list[str] | None = None, elapsed: float = 0.0,
           prompt_version: str = "", pack_version: int | None = None) -> int:
    """1 回の LLM 呼び出しを残す。失敗しても本流を止めない（記録は副作用）。

    gate: pass / fail / error（error = LLM 呼び出し自体が失敗）
    戻り値: seq（0 = 記録できなかった）
    """
    meta = meta or {}
    errors = errors or []
    try:
        seq = count(pdir) + 1
        digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]

        pp = prompt_path(pdir, digest)
        if not pp.exists():                       # 内容尋址 — 同一 prompt は 1 つ
            pp.parent.mkdir(parents=True, exist_ok=True)
            pp.write_text(prompt, encoding="utf-8")

        of = ""
        if output:
            op = out_path(pdir, seq, stage, attempt)
            op.parent.mkdir(parents=True, exist_ok=True)
            op.write_text(output, encoding="utf-8")
            of = str(op.relative_to(trace_dir(pdir)))

        row = {
            "seq": seq,
            "at": datetime.now().isoformat(timespec="seconds"),
            "job_id": job.get("id"),
            "company": job.get("company"),
            "stage": stage,
            "attempt": attempt,
            "prompt_version": prompt_version,
            "prompt_digest": digest,
            "prompt_chars": len(prompt),
            "engine_requested": meta.get("engine_requested"),
            "brain": meta.get("brain"),           # None = 記録できなかった
            "brains": meta.get("brains") or [],
            "llm_ms": meta.get("llm_ms"),
            "queued_ms": meta.get("queued_ms"),
            "task_id": meta.get("task_id"),
            "elapsed": round(elapsed, 1),
            "out_chars": len(output),
            "out_file": of,
            "gate": gate,
            "errors": errors[:8],
            "pack_version": pack_version,
        }
        if meta.get("meta_unavailable"):
            row["meta_unavailable"] = meta["meta_unavailable"]

        ip = index_path(pdir)
        ip.parent.mkdir(parents=True, exist_ok=True)
        with ip.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return seq
    except Exception:
        return 0


# ── 読む ───────────────────────────────────────────────


def resolve_pack(job_id: int) -> Path | None:
    hits = sorted((ROOT / "output" / "proposal").glob(f"{job_id}_*"))
    for d in hits:
        if d.is_dir():
            return d
    return None


def _fmt_row(r: dict) -> str:
    at = (r.get("at") or "")[11:19]
    brain = r.get("brain") or "?"
    gate = {"pass": "✓", "fail": "✗", "error": "!"}.get(r.get("gate"), "-")
    ms = r.get("llm_ms")
    dur = f"{ms / 1000:.0f}s" if ms else f"{r.get('elapsed', 0):.0f}s"
    gates = ",".join(_gate_ids(r.get("errors") or []))
    return (f"{r.get('seq'):>4} {at} {gate} {r.get('stage', ''):<11}"
            f"a{r.get('attempt', 0)} {brain:<12} {dur:>5} "
            f"{r.get('out_chars', 0):>6}字 {gates}")


def _gate_ids(errors: list[str]) -> list[str]:
    import re
    out = []
    for e in errors:
        m = re.search(r"\[(Gate [A-H]|Deck [A-E]|LLM)\]", e)
        if m and m.group(1) not in out:
            out.append(m.group(1))
    return out


def show(pdir: Path, seq: int) -> None:
    rows = {r.get("seq"): r for r in load(pdir)}
    r = rows.get(seq)
    if not r:
        sys.exit(f"seq {seq} が無い（1〜{max(rows) if rows else 0}）")
    print(f"■ seq {seq}  {r.get('stage')} attempt {r.get('attempt')}  "
          f"{r.get('at')}")
    print(f"  brain: {r.get('brain') or '記録なし'}"
          f"（要求: {r.get('engine_requested')}, 試行: {r.get('brains')}）")
    print(f"  prompt 版: {r.get('prompt_version')} / digest {r.get('prompt_digest')}"
          f" / {r.get('prompt_chars')} 字")
    print(f"  閘門: {r.get('gate')}  {r.get('errors') or ''}")
    pp = prompt_path(pdir, r.get("prompt_digest", ""))
    print("\n" + "=" * 70 + "\n【入力 prompt】\n" + "=" * 70)
    print(pp.read_text(encoding="utf-8") if pp.exists() else "（prompt 本文なし）")
    print("\n" + "=" * 70 + "\n【応答】\n" + "=" * 70)
    op = trace_dir(pdir) / (r.get("out_file") or "")
    print(op.read_text(encoding="utf-8") if r.get("out_file") and op.exists()
          else "（応答なし — LLM 呼び出し自体が失敗）")


def prune(pdir: Path, keep: int) -> tuple[int, int]:
    """古い呼び出しの本文だけ消す。索引（index.jsonl）は消さない。

    prompt は 1 回 2〜5 万字あり、迭代を重ねると効いてくる。何を呼んだかの
    索引は全部残し、本文だけ直近 keep 件に絞る。
    """
    rows = load(pdir)
    old = rows[:-keep] if keep > 0 else rows
    keep_digests = {r.get("prompt_digest") for r in rows[-keep:]} if keep > 0 else set()
    n_out = n_prompt = 0
    for r in old:
        if r.get("out_file"):
            p = trace_dir(pdir) / r["out_file"]
            if p.exists():
                p.unlink()
                n_out += 1
    for p in sorted((trace_dir(pdir) / "prompts").glob("*.md")):
        if p.stem not in keep_digests:
            p.unlink()
            n_prompt += 1
    return n_out, n_prompt


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="python3 -m proposal.trace",
        description="提案パックの LLM 呼び出し履歴（prompt・応答・brain・時刻）")
    ap.add_argument("job_id", type=int)
    ap.add_argument("--stage", default=None, help="この stage だけ")
    ap.add_argument("--show", type=int, default=None, metavar="SEQ",
                    help="その 1 回の prompt と応答を全文表示")
    ap.add_argument("--failed", action="store_true", help="閘門を落ちたものだけ")
    ap.add_argument("--prune", action="store_true", help="古い本文を消す（索引は残す）")
    ap.add_argument("--keep", type=int, default=20, help="--prune で残す件数")
    args = ap.parse_args()

    pdir = resolve_pack(args.job_id)
    if pdir is None:
        sys.exit(f"job_id {args.job_id} のパックが output/proposal/ に無い")

    if args.prune:
        n_out, n_prompt = prune(pdir, args.keep)
        print(f"応答 {n_out} 件・prompt {n_prompt} 件を削除（索引は全件保持）")
        return 0

    if args.show is not None:
        show(pdir, args.show)
        return 0

    rows = load(pdir)
    if args.stage:
        rows = [r for r in rows if r.get("stage") == args.stage]
    if args.failed:
        rows = [r for r in rows if r.get("gate") in ("fail", "error")]
    if not rows:
        print(f"記録なし: {index_path(pdir).relative_to(ROOT)}")
        return 0

    print(f"■ {rows[0].get('company')} (job_id={args.job_id})  {len(rows)} 呼び出し")
    print(f"  {pdir.relative_to(ROOT)}/{DIR_NAME}/\n")
    print(" seq 時刻     閘門 stage      試 brain          時間   応答量 落ちた閘門")
    for r in rows:
        print(_fmt_row(r))

    brains: dict[str, int] = {}
    for r in rows:
        brains[r.get("brain") or "記録なし"] = brains.get(r.get("brain") or "記録なし", 0) + 1
    total_ms = sum(r.get("llm_ms") or 0 for r in rows)
    print(f"\n  brain 別: " + "、".join(f"{k} {v} 回" for k, v in
                                        sorted(brains.items(), key=lambda x: -x[1])))
    print(f"  LLM 実時間 合計: {total_ms / 1000:.0f}s"
          f"／閘門通過 {sum(1 for r in rows if r.get('gate') == 'pass')} 回"
          f"・不合格 {sum(1 for r in rows if r.get('gate') == 'fail')} 回"
          f"・呼び出し失敗 {sum(1 for r in rows if r.get('gate') == 'error')} 回")
    print(f"\n  全文を見る: python3 -m proposal.trace {args.job_id} --show <seq>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
