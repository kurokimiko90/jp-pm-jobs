"""r-agent マイページ「気になる」一覧 — CDP 直接爬取。

`/interests` は自分でチェックを付けた（気になる）求人のみを表示する一覧ページ。
カード DOM / href 仕様は `ragent_search` の検索結果一覧と完全に同一（同じ
JobCard コンポーネント）なので、抽出ロジックはそちらの private helper を再利用する。

同じ `/joboffers/{id}` 空間を使うため、**DB への書き込み source は
'recruiter_agent' で統一**（UNIQUE(source, source_id) で他経路と自動マージ、
`keyword='mypage_interest'` で由来を区別）。

**「同公司併入」はしない**（`upsert_job(..., allow_company_dup=True)`）。本人が
星を付けた求人は他サイト経由で同じ会社が既に在庫にあっても独立した row を作る —
併入すると r-agent の source_id / URL / JD が残らず、r-agent 経由で応募できない。

実測（2026-08）: 「さらに表示する」ボタンは無い＝一覧は 1 ページで全件表示
（気になる登録数がそもそも少ないため未検証だが、ボタンが出た場合に備えて
`ragent_search._load_more` と同じ関数を流用できるようにしてある）。

`scrape()` は実装しない（provider registry には載らない）— scrape.py の
run_source が `fetch_all()` を直接呼ぶ（`ragent_search` と同じ特判方式）。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from tracker.db import mark_starred
from ._common import polite_sleep
from .ragent_search import _collect_cards, _fetch_detail, _load_more, _parse_card

PROVIDER_META = {
    "id": "ragent_interests",
    "name": "リクルートエージェント（気になる一覧）",
    "requires_login": True,
    "base_url": "https://mypage.r-agent.com",
    "description": "マイページの「気になる」登録求人を直接取得（CDP）",
}

BASE = "https://mypage.r-agent.com"
INTERESTS_URL = f"{BASE}/interests"

_MAX_LOAD_CLICKS = 3  # 万一「さらに表示する」が出た場合の保険（未確認だが備える）
_CLOSED_LABEL = "受付終了"

# 取得済み source_id の記録（ragent_search と同じ理由：company_norm 併入で
# DB seen_ids だけでは足りない）。
_SEEN_PATH = Path(__file__).resolve().parent.parent / "output" / "ragent_interests_seen.json"


def _load_seen() -> set[str]:
    try:
        data = json.loads(_SEEN_PATH.read_text(encoding="utf-8"))
        return {str(x) for x in data.get("source_ids", [])}
    except Exception:
        return set()


def _save_seen(ids: set[str]) -> None:
    try:
        _SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        _SEEN_PATH.write_text(
            json.dumps({"source_ids": sorted(ids)}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as e:
        print(f"  [ragent_interests] seen 記録の保存に失敗: {type(e).__name__}: {e}")


def fetch_all(page, seen_ids: set[str] | None = None) -> list[dict]:
    """「気になる」一覧を全件開き、新規分のみ JD を取得して job dict のリストを返す。"""
    from analyzer.role_filter import is_engineering_only

    print("  [ragent_interests] 「気になる」一覧を開く…")
    page.goto(INTERESTS_URL, wait_until="domcontentloaded", timeout=45000)
    polite_sleep(2.5, 4.0)
    _load_more(page, _MAX_LOAD_CLICKS)

    cards = [c for c in (_parse_card(c) for c in _collect_cards(page)) if c]
    print(f"  [ragent_interests] 一覧 {len(cards)} 件")

    # 一覧に出ている＝星付き。既に他経路（メール推薦 / 求人検索）で入っている row にも
    # 印を追記しておく（印が無いと dedup_fuzzy の星付き優先が効かず、他源の JD が長い
    # というだけで消される）。
    live_ids = [c["source_id"] for c in cards if _CLOSED_LABEL not in (c["company"], c["title"])]
    marked = mark_starred("recruiter_agent", live_ids)
    if marked:
        print(f"  [ragent_interests] 既存 {marked} 件に星付き印を追記")

    seen = set(seen_ids or set()) | _load_seen()
    targets = []
    skip_seen = skip_eng = skip_closed = 0
    for c in cards:
        # 受付終了の求人はカードの行構成がずれる（1 行目が「受付終了」＝会社名の位置）。
        # JD も開けないのでここで落とす。
        if _CLOSED_LABEL in (c["company"], c["title"]):
            skip_closed += 1
            continue
        if c["source_id"] in seen:
            skip_seen += 1
            continue
        if is_engineering_only(c["title"]):
            skip_eng += 1
            continue
        targets.append(c)

    print(
        f"  [ragent_interests] 新規 {len(targets)} 件"
        f"（既知 {skip_seen} / 工程職 {skip_eng} / 受付終了 {skip_closed}）"
    )
    if not targets:
        return []

    jobs: list[dict] = []
    for c in targets:
        try:
            raw_jd = _fetch_detail(page, c["href"])
        except Exception as e:
            print(f"    ✗ {c['company'][:20]} — {type(e).__name__}")
            raw_jd = ""
        if not raw_jd:
            print(f"    ✗ {c['company'][:20]} — JD 取得失敗")
            continue
        jobs.append({
            "source": "recruiter_agent",  # 検索/メール由来と同じ ID 空間に統合
            "source_id": c["source_id"],
            "title": c["title"][:200],
            "company": c["company"][:120],
            "location": c["location"][:120],
            "url": f"{BASE}/joboffers/{c['source_id']}",  # 裸 URL（他経路と揃える）
            "raw_jd": raw_jd[:8000],
            "keyword": "mypage_interest",
            "salary_min": c["salary_min"],
            "salary_max": c["salary_max"],
            "scraped_at": datetime.now().isoformat(timespec="seconds"),
        })
        seen.add(c["source_id"])
        print(f"    ✓ {c['company'][:20]} / {c['title'][:30]} ({len(raw_jd)} 字)")
        polite_sleep(1.5, 3.0)

    _save_seen(seen)
    print(f"  [ragent_interests] 合計 {len(jobs)} 件の JD 取得")
    return jobs
