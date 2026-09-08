"""リクルートエージェント マイページ 求人検索 — CDP 直接爬取。

`recruiter_agent`（担当 CA の推薦メール）と同じ求人 ID 空間（/joboffers/{id}）を
使うため、**DB への書き込み source は 'recruiter_agent' で統一**する。
UNIQUE(source, source_id) がそのまま効き、メール推薦と検索結果は自動的に 1 件に
マージされる（`keyword` 欄で由来を区別: "search:{条件名}"）。

実測した r-agent マイページの仕様（2026-07）:
  - 検索条件は保存済み条件の URL がそのまま識別子。`?page=` 等のページ送りは
    **効かない**（無視されて 1 ページ目に戻る）。初期表示 100 件 →
    「さらに表示する」ボタン 1 クリックごとに +100 件が同一 DOM に追記される。
  - 一覧カードの href には tracking_id / request_ids が付いており、
    **裸 URL（/joboffers/{id} のみ）は「エラーが発生しました」になる**。
    よって詳細 JD は「一覧を開いた同じセッション内で、一覧が返した完全 href」
    でしか取得できない。DB に保存する url は裸 URL（メール由来と揃える）。
  - カードの class は CSS module ハッシュ付き（JobCard_jobOfferWrapper__xxxxx）
    なので前方一致で拾い、href での兜底も併用する。

設定は config/scraping.yaml の `ragent_search`（未設定なら自動 skip）。
`scrape()` は実装しない（provider registry には載らない）— scrape.py の
run_source が `search_all()` を直接呼ぶ。
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from tools.app_config import load as _load_app_config
from tools.salary_parser import parse_salary
from ._common import polite_sleep
from .recruiter_agent import NAV_LINES as _BASE_NAV_LINES

PROVIDER_META = {
    "id": "ragent_search",
    "name": "リクルートエージェント（求人検索）",
    "requires_login": True,
    "base_url": "https://mypage.r-agent.com",
    "description": "マイページの保存条件から求人一覧＋JD を直接取得（CDP）",
}

BASE = "https://mypage.r-agent.com"

_DEFAULTS = {
    "max_load_clicks": 2,   # 「さらに表示する」クリック回数（1 回 = +100 件）
    "min_salary_man": 0,    # 一覧段階の年収下限（万円）。0 = 絞らない
    "detail_limit": 60,     # 1 回の実行で開く詳細ページ数の上限
}

# 一覧カードの innerText に現れるラベル行
_LABEL_SALARY = "年収"
_LABEL_LOCATION = "勤務地"
_LABEL_HOLIDAY = "休日"
_CARD_SKIP_LINES = frozenset(["気になる", "興味なし", "応募する", "NEW"])


# 取得済み source_id の記録。DB だけでは足りない：`upsert_job` の「同公司併入」
# 分支（1 社 1 レコード方針）は既存 row を更新するだけで新しい source_id を残さない
# ため、DB 由来の seen_ids ではその求人を「未取得」と誤認して毎回開き直してしまう。
# → ここで別途 append-only の一覧を持ち、収斂を保証する。
_SEEN_PATH = Path(__file__).resolve().parent.parent / "output" / "ragent_search_seen.json"


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
        print(f"  [ragent_search] seen 記録の保存に失敗: {type(e).__name__}: {e}")


def _cfg() -> dict:
    raw = _load_app_config("scraping").get("ragent_search") or {}
    cfg = dict(_DEFAULTS)
    for k in _DEFAULTS:
        if raw.get(k) is not None:
            cfg[k] = int(raw[k])
    cfg["search_urls"] = raw.get("search_urls") or []
    cfg["keywords"] = raw.get("keywords") or []
    return cfg


def _load_more(page, clicks: int) -> None:
    """「さらに表示する」を clicks 回押して一覧を伸ばす（同一 DOM に追記される）。"""
    for i in range(clicks):
        try:
            btn = page.get_by_role("button", name="さらに表示する")
            btn.scroll_into_view_if_needed(timeout=8000)
            btn.click(timeout=8000)
        except Exception:
            break  # ボタンが無い = 全件表示済み
        polite_sleep(2.5, 4.0)


def _collect_cards(page) -> list[dict]:
    """一覧ページから全カードを抽出（href は tracking 付きの完全形）。"""
    return page.evaluate(
        """
        () => {
            const sel = 'a[class*="JobCard_jobOfferWrapper"], a[href*="/joboffers/"]';
            const seen = new Set();
            const out = [];
            for (const a of document.querySelectorAll(sel)) {
                const href = a.getAttribute('href') || '';
                const m = href.match(/\\/joboffers\\/(\\d+)/);
                if (!m || seen.has(m[1])) continue;
                seen.add(m[1]);
                out.push({
                    source_id: m[1],
                    href: href,
                    text: (a.innerText || '').trim(),
                });
            }
            return out;
        }
        """
    )


def _parse_card(card: dict) -> dict | None:
    """カード innerText を company / title / salary / location に分解。

    実測フォーマット:
        株式会社ＸＸＸ
        【職種名】…
        年収
        679〜1152万
        勤務地
        東京都渋谷区、…
        休日
        126日
        （タグ…）
    """
    lines = [l.strip() for l in (card.get("text") or "").splitlines() if l.strip()]
    lines = [l for l in lines if l not in _CARD_SKIP_LINES]
    if len(lines) < 2:
        return None

    company, title = lines[0], lines[1]
    salary_raw = ""
    location = ""
    for i, line in enumerate(lines[2:], start=2):
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if line == _LABEL_SALARY and not salary_raw:
            salary_raw = nxt
        elif line == _LABEL_LOCATION and not location:
            location = nxt
        elif line == _LABEL_HOLIDAY:
            continue

    smin, smax = parse_salary(f"{_LABEL_SALARY}\n{salary_raw}") if salary_raw else (None, None)
    return {
        "source_id": card["source_id"],
        "href": card["href"],
        "company": company,
        "title": title,
        "location": location,
        "salary_raw": salary_raw,
        "salary_min": smin,
        "salary_max": smax,
    }


# 詳細ページのタブ（初期表示は「募集要項」。企業概要は別タブ）
_DETAIL_TABS = ("選考・企業概要",)

# 詳細ページ本文に必ず混ざるナビ / ボタン行（JD ではないので落とす）。定義の唯一の
# 出所は recruiter_agent.NAV_LINES（structure_jd_lines も同じ集合で除去する）。
# ここではタブ見出しの「選考・企業概要」も落とす — 区分マーカーは _fetch_detail が
# 【選考・企業概要】として別途入れるため。
_NAV_LINES = _BASE_NAV_LINES | {"選考・企業概要"}


def _clean_detail(text: str) -> list[str]:
    """詳細ページの innerText からナビ行を落として行リストにする。"""
    return [
        line for line in (l.strip() for l in text.splitlines())
        if line and line not in _NAV_LINES
    ]


def _fetch_detail(page, href: str) -> str:
    """詳細ページ JD 本文を取得。tracking 付き href が必須（裸 URL はエラー）。

    タブを切り替えても innerText はページ全体を返す（＝ヘッダ部が丸ごと重複する）
    ため、追記は「前タブに無かった行」だけに絞る。
    """
    from .recruiter_agent import _extract_jd_text

    url = href if href.startswith("http") else BASE + href
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    raw = _extract_jd_text(page)
    if "エラーが発生しました" in raw:
        return ""

    lines = _clean_detail(raw)
    seen = set(lines)

    # 企業概要タブが押せれば差分のみ追記（選考プロセス / 企業情報）
    for tab in _DETAIL_TABS:
        try:
            page.get_by_text(tab, exact=True).first.click(timeout=5000)
            time.sleep(1.5)
            extra = [l for l in _clean_detail(_extract_jd_text(page)) if l not in seen]
        except Exception:
            continue
        if extra:
            lines.append(f"【{tab}】")
            lines.extend(extra)
            seen.update(extra)
    return "\n".join(lines).strip()


def _open_keyword_search(page, keyword: str) -> bool:
    """フリーワード検索を UI 操作で実行して一覧を表示する。

    結果ページの URL は `/job_search/free_word` のままでクエリを持たない
    （＝条件を URL として保存・再利用できない）ため、毎回この操作が必要。
    フロー: 入力 → サジェスト候補（chip）を選択 → 「検索（N件）」を押す。
    候補が出ないキーワード（英字など）は chip 化できないので False を返す。
    """
    page.goto(f"{BASE}/job_search/free_word", wait_until="domcontentloaded", timeout=45000)
    polite_sleep(2.5, 4.0)
    try:
        page.locator("input[type='text']").first.fill(keyword, timeout=10000)
    except Exception as e:
        print(f"    ✗ 入力欄が見つからない: {type(e).__name__}")
        return False
    polite_sleep(1.5, 2.5)

    try:  # サジェストの先頭を選ぶ（表記が正規化されるので文字列一致は使わない）
        page.locator("button[class*='ListItem_button']").first.click(timeout=6000)
    except Exception:
        print(f"    ✗ 『{keyword}』候補が出ないため skip（フリーワードは候補選択が必須）")
        return False
    polite_sleep(2.0, 3.0)

    # chip 選択後は条件ページ（絞り込みファセット付き）に変わる。実行ボタンは
    # 最下部にあり、スクロールしないと掴めないことがある。
    try:
        page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(1.5)
    except Exception:
        pass

    for label in ("検索", "この条件で検索"):  # ボタン文字は「検索（17件）」形式
        try:
            page.get_by_role("button", name=label).first.click(timeout=5000)
            polite_sleep(3.5, 5.0)
            return True
        except Exception:
            continue
    print(f"    ✗ 検索ボタンが見つからない（keyword={keyword}）")
    return False


def _search_one(page, name: str, url: str, cfg: dict, seen_ids: set[str],
                budget: int, keyword: str | None = None) -> list[dict]:
    """条件 1 本を処理。budget = 今回開いてよい詳細ページ数。

    `keyword` 指定時はフリーワード検索（UI 操作）、それ以外は保存条件 URL。
    """
    if keyword:
        print(f"  [ragent_search] キーワード『{keyword}』で検索…")
        if not _open_keyword_search(page, keyword):
            return []
    else:
        print(f"  [ragent_search] 条件『{name}』を開く…")
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        polite_sleep(3.0, 5.0)
    _load_more(page, cfg["max_load_clicks"])

    cards = [c for c in (_parse_card(c) for c in _collect_cards(page)) if c]
    print(f"  [ragent_search] 一覧 {len(cards)} 件")

    # 一覧段階のふるい分け（詳細ページを開く前に落とす = 一番高いコストの節約）
    from analyzer.role_filter import is_engineering_only

    targets: list[dict] = []
    skip_seen = skip_eng = skip_salary = 0
    for c in cards:
        if c["source_id"] in seen_ids:
            skip_seen += 1
            continue
        if is_engineering_only(c["title"]):
            skip_eng += 1
            continue
        if cfg["min_salary_man"] and c["salary_max"] and c["salary_max"] < cfg["min_salary_man"]:
            skip_salary += 1
            continue
        targets.append(c)

    print(
        f"  [ragent_search] 新規 {len(targets)} 件"
        f"（既知 {skip_seen} / 工程職 {skip_eng} / 年収不足 {skip_salary}）"
    )
    targets = targets[:budget]
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
            "source": "recruiter_agent",  # メール由来と同じ ID 空間に統合
            "source_id": c["source_id"],
            "title": c["title"][:200],
            "company": c["company"][:120],
            "location": c["location"][:120],
            "url": f"{BASE}/joboffers/{c['source_id']}",  # 裸 URL（tracking は保存しない）
            "raw_jd": raw_jd[:8000],
            "keyword": f"search:{name}"[:60],
            "salary_min": c["salary_min"],
            "salary_max": c["salary_max"],
            "scraped_at": datetime.now().isoformat(timespec="seconds"),
        })
        seen_ids.add(c["source_id"])
        print(f"    ✓ {c['company'][:20]} / {c['title'][:30]} ({len(raw_jd)} 字)")
        polite_sleep(1.5, 3.0)
    return jobs


def _targets(cfg: dict) -> list[tuple[str, str, str | None]]:
    """処理対象を (表示名, URL, keyword) のリストに揃える。

    キーワードを先に回す：件数が少なく（実測 17 件規模）マッチ精度が高いので、
    detail_limit の枠を先に使わせる。
    """
    out: list[tuple[str, str, str | None]] = [
        (str(kw), "", str(kw)) for kw in cfg["keywords"] if str(kw).strip()
    ]
    for e in cfg["search_urls"]:
        if e.get("url"):
            out.append((str(e.get("name") or "無題"), e["url"], None))
    return out


def search_all(page, seen_ids: set[str] | None = None) -> list[dict]:
    """config の全キーワード / 保存条件を順に処理して job dict のリストを返す。"""
    cfg = _cfg()
    targets = _targets(cfg)
    if not targets:
        print("  [ragent_search] config/scraping.yaml に ragent_search.search_urls / "
              "keywords が無いので skip")
        return []

    seen = set(seen_ids or set()) | _load_seen()
    remaining = cfg["detail_limit"]
    all_jobs: list[dict] = []

    for name, url, keyword in targets:
        if remaining <= 0:
            print(f"  [ragent_search] detail_limit={cfg['detail_limit']} 到達、以降は次回")
            break
        try:
            jobs = _search_one(page, name, url, cfg, seen, remaining, keyword=keyword)
        except Exception as e:
            print(f"  [ragent_search] 条件『{name}』失敗: {type(e).__name__}: {e}")
            continue
        finally:
            # 条件ごとに保存 — 途中で落ちても取得済み分は次回スキップできる
            _save_seen(seen)
        all_jobs.extend(jobs)
        remaining -= len(jobs)

    print(f"  [ragent_search] 合計 {len(all_jobs)} 件の JD 取得")
    return all_jobs
