"""実行履歴 — どの prompt 版でどの閘門が落ちたかを溜める（零 LLM）。

自己進化の土台。prompt を直したときに「本当に良くなったのか」を、感想ではなく
閘門の通過率で見るためにある。1 行 1 stage 実行の JSONL。

出力を汚さないよう `output/proposal/_history.jsonl` に置く（pack の外）。
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
PATH = ROOT / "output" / "proposal" / "_history.jsonl"

# 指摘文から閘門の識別子だけを取り出す（本文は job ごとに違うので統計に使えない）
_GATE_RE = re.compile(r"\[(Gate [A-H]|Deck [A-E]|LLM)\]")


def gate_ids(errors: list[str]) -> list[str]:
    out: list[str] = []
    for e in errors or []:
        m = _GATE_RE.search(e)
        if m:
            out.append(m.group(1))
    return out


def record(job: dict, stage: str, version: str, status: str, attempts: int,
           errors: list[str], elapsed: float = 0.0) -> None:
    """1 stage 実行を追記する。失敗しても本流を止めない。"""
    row = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "job_id": job.get("id"),
        "company": job.get("company"),
        "stage": stage,
        "version": version,
        "status": status,
        "attempts": attempts,
        "gates": gate_ids(errors),
        "elapsed": round(elapsed, 1),
    }
    try:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        with PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def load() -> list[dict]:
    if not PATH.exists():
        return []
    rows = []
    for line in PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def stats() -> dict:
    """stage × prompt 版ごとの一発通過率と、よく落ちる閘門。

    「一発通過率」= attempts 1 で ok になった割合。是正再生成が要る＝prompt が
    その論点を伝えられていない、という読み方をする。
    """
    buckets: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"runs": 0, "first_pass": 0, "degraded": 0, "failed": 0,
                 "attempts": 0, "gates": defaultdict(int)})
    for r in load():
        if r.get("status") in ("cached", "skipped"):
            continue
        b = buckets[(r.get("stage", "?"), r.get("version", "?"))]
        b["runs"] += 1
        b["attempts"] += r.get("attempts", 0)
        if r.get("status") == "ok" and r.get("attempts", 0) <= 1:
            b["first_pass"] += 1
        if r.get("status") == "degraded":
            b["degraded"] += 1
        if r.get("status") == "failed":
            b["failed"] += 1
        for g in r.get("gates") or []:
            b["gates"][g] += 1
    out = {}
    for (stage, version), b in buckets.items():
        runs = max(b["runs"], 1)
        out[f"{stage}@{version}"] = {
            "runs": b["runs"],
            "first_pass_rate": round(b["first_pass"] / runs, 2),
            "avg_attempts": round(b["attempts"] / runs, 2),
            "degraded": b["degraded"],
            "failed": b["failed"],
            "top_gates": sorted(b["gates"].items(), key=lambda x: -x[1])[:4],
        }
    return out


def frequent_gates(min_hits: int = 2) -> list[tuple[str, int]]:
    """繰り返し落ちている閘門 — prompt を直す優先順位。"""
    counter: dict[str, int] = defaultdict(int)
    for r in load():
        for g in r.get("gates") or []:
            counter[g] += 1
    return sorted([(g, n) for g, n in counter.items() if n >= min_hits],
                  key=lambda x: -x[1])
