"""日程確定 → 面接パック（prep.py {id} interview）自動生成のトリガ。

「面接が日程に登記された」ことを起点に 1 本走らせる。inbox.schedule の書き込み
経路に直接ぶら下げず applications を毎回スキャンする方式にした — Dashboard や
Telegram bot から手で next_event を入れた場合も同じ扱いになるため。

ガード（面接パックは LLM 重量級・1 本で十数分かかる）:
  - 今日以降の面接だけ（過ぎた日程は対象外）
  - 1 ラウンド max_per_run 本まで（既定 1）
  - **既にパックがある job は再生成しない** — 手編集した 01_interview_qa.md が
    prep.py の再実行で消えるため（CLAUDE.md「/hire-audit」節の警告）。
    代わりに「既存パックあり」を 1 回だけ通知して人間に判断させる
  - 失敗は max_attempts 回まで（notify.dedupe の escalation を流用）
  - slides / voice は既定 stage から外す（slides は品質未達、voice は指揮中心の
    ChatGPT アカウントを十数分占有するので在席時に手動で回す）

CLI:
    python3 -m inbox.prep_trigger --dry-run     # 対象だけ表示（生成しない）
    python3 -m inbox.prep_trigger               # 対象を 1 本生成
    python3 -m inbox.prep_trigger --job-id 123 --force   # 既存パックでも強制再生成
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from notify import send
from notify.dedupe import already_sent, escalation_state, mark_sent
from tools import app_config
from tracker import db

ROOT = Path(__file__).parent.parent
PREP_DIR = ROOT / "output" / "prep"
LOCK_DIR = ROOT / "output" / "logs" / ".interview_prep.lock"

_KIND_ATTEMPT = "auto_interview_prep"     # escalation：試行回数の上限管理
_KIND_DONE = "auto_interview_prep_done"   # 終端（成功 or 既存パックで見送り）

_DATE_RE = re.compile(r"(\d{4})/(\d{1,2})/(\d{1,2})")

# 既定 stage：slides（品質未達）と voice（十数分・指揮中心占有）は外す
_DEFAULT_STAGES = ["qa", "jikoshoukai", "checklist", "script"]


def _cfg(key: str, default):
    return app_config.get("prep", key, default)


def _event_date(next_event: str | None) -> date | None:
    m = _DATE_RE.search(next_event or "")
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def pack_exists(job_id: int) -> bool:
    """面接パック生成済み判定 — 想定問答が実体としてあるかで見る。

    jobs.interview_pack（DB）ではなくファイルを見るのは、_archive へ移した／消した
    パックを「まだある」と誤判定しないため。notify/bot.py の qa ボタンと同じ signal。
    """
    return any(PREP_DIR.glob(f"{job_id}_*/01_interview_qa.md"))


def pending(today: date | None = None) -> list[dict]:
    """日程確定済み・今日以降・未処理の面接を、直近の日程順で返す。"""
    today = today or date.today()
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT a.job_id, a.next_event, j.company, j.title "
            "FROM applications a JOIN jobs j ON j.id = a.job_id "
            "WHERE a.next_event IS NOT NULL AND a.status != 'rejected'",
        ).fetchall()

    out: list[dict] = []
    for r in rows:
        event_date = _event_date(r["next_event"])
        if event_date is None or event_date < today:
            continue
        out.append({
            "job_id": r["job_id"],
            "company": r["company"] or "?",
            "title": r["title"] or "",
            "next_event": r["next_event"],
            "event_date": event_date,
            "has_pack": pack_exists(r["job_id"]),
        })
    return sorted(out, key=lambda d: d["event_date"])


@contextmanager
def _lock():
    """mkdir による原子ロック。前ラウンドの生成が続いている間は本ラウンドを飛ばす
    （面接パックは 30 分の launchd 間隔をまたぐことがある）。"""
    LOCK_DIR.parent.mkdir(parents=True, exist_ok=True)
    try:
        LOCK_DIR.mkdir()
    except FileExistsError:
        yield False
        return
    try:
        yield True
    finally:
        LOCK_DIR.rmdir()


def _label(item: dict) -> str:
    return f"{item['company']} — {item['title']}"[:80]


def _pack_path(job_id: int) -> str:
    hits = sorted(PREP_DIR.glob(f"{job_id}_*"))
    return str(hits[0].relative_to(ROOT)) if hits else "output/prep/"


def run_pack(job_id: int, stages: list[str], timeout: int) -> tuple[bool, str]:
    """prep.py {id} interview を実行。(成功したか, 出力末尾) を返す。"""
    proc = subprocess.run(
        [sys.executable, "prep.py", str(job_id), "interview", "--stage", ",".join(stages)],
        cwd=ROOT, capture_output=True, text=True, timeout=timeout,
    )
    tail = (proc.stderr or proc.stdout or "")[-400:]
    return proc.returncode == 0, tail


def process(item: dict, *, dry_run: bool = False, force: bool = False) -> str | None:
    """1 件処理。実際に何かした場合のみ状況文字列を返す（no-op は None）。"""
    job_id = item["job_id"]
    ref = f"{job_id}:{item['event_date'].isoformat()}"

    if not force and already_sent(_KIND_DONE, ref):
        return None

    if item["has_pack"] and not force:
        # 再生成すると手編集した 01_interview_qa.md が消える。人間に判断させる。
        if dry_run:
            return f"[既存パックあり・見送り] #{job_id} {_label(item)}"
        send(f"{_label(item)}\n{item['next_event']}\n"
             f"既に面接パックがあるため自動再生成は見送りました：{_pack_path(job_id)}\n"
             f"作り直す場合は手動で prep.py {job_id} interview",
             title=f"📁 面接パック既存 [#{job_id}]",
             buttons=[[{"text": "📋 想定問答", "callback_data": f"qa:{job_id}"}]])
        mark_sent(_KIND_DONE, ref)
        return f"既存パックあり・通知のみ #{job_id}"

    stages = list(_cfg("auto_interview_stages", _DEFAULT_STAGES))
    max_attempts = int(_cfg("auto_interview_max_attempts", 2))
    timeout = int(_cfg("auto_interview_timeout_sec", 3600))

    count, _ = escalation_state(_KIND_ATTEMPT, ref)
    if count >= max_attempts and not force:
        return None

    if dry_run:
        return f"[生成対象] #{job_id} {_label(item)} · {item['next_event']} · stage={','.join(stages)}"

    # 実行前に試行を記録：同時／次ラウンドが同じ job を掴まないようにする
    mark_sent(_KIND_ATTEMPT, f"{ref}:{count + 1}")
    try:
        ok, tail = run_pack(job_id, stages, timeout)
    except subprocess.TimeoutExpired:
        ok, tail = False, f"timeout（{timeout}s）"

    if ok:
        mark_sent(_KIND_DONE, ref)
        send(f"{_label(item)}\n{item['next_event']}\n輸出：{_pack_path(job_id)}\n"
             f"stage: {', '.join(stages)}",
             title=f"🎤 面接パック生成完了 [#{job_id}]",
             buttons=[[{"text": "📋 想定問答", "callback_data": f"qa:{job_id}"}]])
        return f"生成完了 #{job_id}"

    send(f"{_label(item)}\n{item['next_event']}\n"
         f"（{count + 1}/{max_attempts} 回目）\n{tail}",
         title=f"⚠ 面接パック生成失敗 [#{job_id}]")
    return f"生成失敗 #{job_id}"


def run(*, limit: int | None = None, dry_run: bool = False,
        force: bool = False, job_id: int | None = None) -> list[str]:
    """対象を拾って順に処理。実際に動いた件の状況文字列リストを返す。"""
    if not _cfg("auto_interview_pack", True):
        return []
    limit = limit if limit is not None else int(_cfg("auto_interview_max_per_run", 1))

    items = pending()
    if job_id is not None:
        items = [i for i in items if i["job_id"] == job_id]

    results: list[str] = []
    for item in items:
        if len(results) >= limit:
            break
        status = process(item, dry_run=dry_run, force=force)
        if status:
            results.append(status)
    return results


def main() -> None:
    p = argparse.ArgumentParser(description="日程確定済みの面接 → 面接パック自動生成")
    p.add_argument("--dry-run", action="store_true", help="対象を表示するだけ")
    p.add_argument("--limit", type=int, default=None, help="1 回の生成本数上限")
    p.add_argument("--job-id", type=int, default=None, help="特定 job のみ")
    p.add_argument("--force", action="store_true",
                   help="既存パック・試行上限を無視して生成（手編集は上書きされる）")
    args = p.parse_args()

    with _lock() as acquired:
        if not acquired:
            print("[prep_trigger] 前ラウンドの生成が継続中 → 本ラウンドは見送り")
            return
        results = run(limit=args.limit, dry_run=args.dry_run,
                      force=args.force, job_id=args.job_id)

    if not results:
        print("[prep_trigger] 対象なし")
        return
    for line in results:
        print(f"[prep_trigger] {line}")


if __name__ == "__main__":
    main()
