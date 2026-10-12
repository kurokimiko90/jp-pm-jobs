"""Demo Mode 種子資料 — 灌 40 筆虛構職缺，供開源訪客快速看到 dashboard 長相。

    python3 -m tools.seed_demo           # 灌入 demo 資料（先清掉舊 demo 資料，冪等）
    python3 -m tools.seed_demo --clear   # 只清除 demo 資料

所有 demo 職缺 source='demo'，與真實資料互不干擾；--clear 連同 applications 與
demo gap_batch 一起移除。DB 已有非 demo 資料時會警告並要求 --force（避免誤混）。

公司名/JD 全部虛構，分數分布/漏斗狀態模擬真實使用一個月後的樣子。
"""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
from datetime import date, timedelta

from tracker.db import DB_PATH, connect, init_db

RNG = random.Random(42)  # 固定 seed，每次灌入結果一致

TODAY = date.today()


def _d(days_ago: int) -> str:
    return (TODAY - timedelta(days=days_ago)).isoformat()


# ── 虛構公司池（名稱均為虛構，如與現實公司雷同純屬巧合） ──────────
COMPANIES = {
    "ai_startup": [
        "株式会社NeuraWorks", "LayerCraft株式会社", "株式会社ミライ言語", "Synapse Field株式会社",
        "株式会社アルゴノート", "PromptForge株式会社", "株式会社コグニタス", "VectorPath株式会社",
        "株式会社エージェント工房", "Fluent AI Lab株式会社", "株式会社トークンリバー",
        "株式会社ディープ紺屋", "Kizuna Intelligence株式会社", "株式会社モデルシップ",
    ],
    "mega_venture": [
        "株式会社スカイモール", "ペイブリッジ株式会社", "株式会社グランポート",
        "株式会社ワークスフィア", "メディアタウン株式会社", "株式会社フィンネスト",
        "株式会社トラベルドア", "ゲームフロント株式会社", "株式会社ヘルスループ",
        "株式会社エデュポート",
    ],
    "traditional_sier": [
        "日本総合システム開発株式会社", "株式会社セントラル情報サービス", "東雲コンピュータ株式会社",
        "株式会社アークシステムズ", "大和ITソリューションズ株式会社", "株式会社フジミ情報技術",
        "みなとシステムエンジニアリング株式会社", "株式会社エヌ・ティ・データ工業",
        "北斗ソフトウェア株式会社", "株式会社シーガル電子計算", "京浜システムズ株式会社",
        "株式会社オリオン情報",
    ],
    "unknown": [
        "株式会社ノースゲート", "サウスピア株式会社", "株式会社イーストリンク", "ウェストブルーム株式会社",
    ],
}

TITLES = [
    "プロダクトマネージャー（AI SaaS）", "シニアプロダクトマネージャー", "PdM／プロダクト企画",
    "AIプロダクトマネージャー", "プロダクトオーナー", "テクニカルプロダクトマネージャー",
    "プロダクトマネージャー（新規事業）", "LLMプロダクト企画・PdM", "プラットフォームPdM",
    "プロダクトマネージャー（データ基盤）",
]

LOCATIONS = ["東京都渋谷区", "東京都港区", "東京都千代田区", "大阪市北区", "リモート可（東京）", "フルリモート"]

JD_TEMPLATE = """【ポジション】{title}
【会社概要】{company}は{domain}領域のプロダクトを展開しています。（本データはデモ用の架空求人です）
【業務内容】
・プロダクトロードマップの策定と実行
・ユーザーヒアリングとデータ分析に基づく仕様策定
・エンジニア／デザイナーと協働したアジャイル開発推進
{extra}【求める経験】
・PdM または関連職種 3年以上
・データドリブンな意思決定の経験
【想定年収】{smin}万円〜{smax}万円
"""

DOMAINS = ["AI SaaS", "Fintech", "HR Tech", "ヘルスケア", "EC", "教育", "業務システム", "エンタメ"]

GAP_REASONS_GO = [
    "PM 核心經驗與 JD 高度吻合，AI 實作能力是強差異化，年收帶重疊。",
    "職責範圍與過往產品線幾乎一致，跨職能協作經驗直接可用。",
    "技術棧重合度高，JD 明確歡迎工程背景 PM，建議優先投遞。",
]
GAP_REASONS_IMPROVE = [
    "核心能力符合但領域知識需補課，建議先做 1-2 天領域調研再投。",
    "JD 要求英語商務會話，需在面試前準備英語自我介紹與案例敘事。",
    "經驗年數略低於要求，建議用量化成果補強敘事後再投。",
]
GAP_REASONS_SKIP = [
    "JD 實質是工程職掛 PM 名，方向不符。",
    "要求 10 年以上大組織管理經驗，差距過大。",
    "薪資帶明顯低於目標範圍，機會成本高。",
]


def _make_jobs() -> list[dict]:
    jobs = []
    i = 0
    for tier, names in COMPANIES.items():
        for name in names:
            i += 1
            # 分數分布：ai_startup 偏高、sier 偏低、unknown 居中偏低
            base = {"ai_startup": (62, 92), "mega_venture": (55, 85),
                    "traditional_sier": (40, 68), "unknown": (45, 70)}[tier]
            score = RNG.randint(*base)
            smin = RNG.choice([600, 700, 800, 900, 1000])
            smax = smin + RNG.choice([200, 300, 400, 500])
            first_seen = RNG.randint(0, 21)
            extra = "・LLM／生成AI を活用した機能企画\n" if tier == "ai_startup" else ""
            jobs.append({
                "source": "demo", "source_id": f"demo-{i:03d}",
                "url": f"https://example.com/demo/jobs/{i:03d}",
                "title": RNG.choice(TITLES), "company": name,
                "location": RNG.choice(LOCATIONS),
                "keyword": "product manager",
                "first_seen": _d(first_seen), "last_seen": _d(RNG.randint(0, min(first_seen, 3))),
                "salary_min": smin, "salary_max": smax,
                "score": score, "tier": tier, "tier_conf": 0.9,
                "posting_type": "agent" if RNG.random() < 0.15 else "direct",
                "domain": RNG.choice(DOMAINS), "extra": extra,
            })
    RNG.shuffle(jobs)
    return jobs


def _gap_json(score: int, company: str) -> tuple[str, int]:
    rec = min(95, max(30, score + RNG.randint(-5, 12)))
    verdict = "go" if rec >= 80 else "improve" if rec >= 65 else "skip"
    reason = RNG.choice(
        GAP_REASONS_GO if verdict == "go"
        else GAP_REASONS_IMPROVE if verdict == "improve" else GAP_REASONS_SKIP)
    payload = {
        "requirements": ["PdM 経験 3年以上", "データ分析に基づく意思決定", "アジャイル開発推進"],
        "matched": ["PdM 経験", "データドリブン意思決定"],
        "gaps": [] if verdict == "go" else ["領域知識", "英語商務會話"][: 1 if verdict == "improve" else 2],
        "recommend_score": rec, "recommend_reason": reason, "verdict": verdict,
    }
    return json.dumps(payload, ensure_ascii=False), rec


def _batch_summary(scored: list[dict]) -> str:
    tiers: dict[str, list] = {"go": [], "improve": [], "skip": []}
    for j in scored:
        tiers[j["verdict"]].append({
            "id": j["id"], "company": j["company"], "title": j["title"],
            "rec": j["rec"], "reason": j["reason"],
        })
    n_go, n_imp, n_skip = len(tiers["go"]), len(tiers["improve"]), len(tiers["skip"])
    portrait = (
        "## 候選人橫向畫像（デモデータ）\n\n"
        f"**整體分布**：{len(scored)} 筆職缺中，rec≥80 共 {n_go} 筆（go），"
        f"65-79 共 {n_imp} 筆（improve），<65 共 {n_skip} 筆（skip）。\n\n"
        "**最佳適配層**：AI スタートアップの PdM 職缺與候選人畫像重合度最高；"
        "傳統 SIer 的職缺多為工程職掛 PM 名，實質適配度低。\n\n"
        "*本報告由 demo 種子資料生成，用於展示推薦度報告頁的呈現形式。*"
    )
    return json.dumps({
        "portrait": portrait,
        "common_gaps": [
            {"theme": "英語商務會話能力", "count": 12, "severity": "高", "nature": "真短板"},
            {"theme": "跨領域知識缺口", "count": 9, "severity": "中", "nature": "混合"},
            {"theme": "AI 實作成果未充分呈現於履歷", "count": 7, "severity": "中", "nature": "敘事問題"},
        ],
        "tiers": tiers,
        "actions": [
            {"title": "AI 實作成果重新框架", "detail": "將個人專案敘事升格為可複製的工程化框架。",
             "type": "敘事優化", "roi": "高", "effort": "3-5 天",
             "steps": ["列出實作項目", "對應 JD 關鍵字", "改寫履歷要點"]},
            {"title": "英語面試準備", "detail": "準備英語自我介紹與 2 個 STAR 案例。",
             "type": "能力補強", "roi": "中", "effort": "1-2 週",
             "steps": ["寫講稿", "錄音自評", "模擬面試"]},
        ],
    }, ensure_ascii=False)


# 應募漏斗：sier 樣本 10 筆（6 筆書類落ち）讓「調校建議卡」在 demo 裡直接可見
FUNNEL_PLAN = [
    ("traditional_sier", "rejected", "shorui", "experience"), ("traditional_sier", "rejected", "shorui", "unspecified"),
    ("traditional_sier", "rejected", "shorui", "age"), ("traditional_sier", "rejected", "shorui", "experience"),
    ("traditional_sier", "rejected", "shorui", "language"), ("traditional_sier", "rejected", "shorui", "unspecified"),
    ("traditional_sier", "applied", None, None), ("traditional_sier", "applied", None, None),
    ("traditional_sier", "recruiter", None, None), ("traditional_sier", "rejected", "ichiji", "experience"),
    ("ai_startup", "applied", None, None), ("ai_startup", "recruiter", None, None),
    ("ai_startup", "tech", None, None), ("ai_startup", "onsite", None, None),
    ("ai_startup", "offer", None, None),
    ("mega_venture", "applied", None, None), ("mega_venture", "recruiter", None, None),
]


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """全新 DB（開源訪客 setup.sh 剛跑完）缺增量遷移的表/欄，冪等補齊。"""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS gap_batches ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, "
        "source_filter TEXT, min_score INTEGER, job_count INTEGER DEFAULT 0, summary_json TEXT)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS followups ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER NOT NULL REFERENCES jobs(id), "
        "logged_at DATE NOT NULL, note TEXT NOT NULL, method TEXT DEFAULT 'email')")
    for table, col, coltype in (
        ("jobs", "posting_type", "TEXT"),
        ("jobs", "gap_analyzed_at", "TEXT"),
        ("jobs", "blacklisted", "INTEGER"),
        ("applications", "next_event", "TEXT"),
        ("applications", "rejection_stage", "TEXT"),
        ("applications", "rejection_reason", "TEXT"),
    ):
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")
        except sqlite3.OperationalError:
            pass


def clear_demo(conn: sqlite3.Connection) -> int:
    _ensure_schema(conn)
    conn.execute(
        "DELETE FROM applications WHERE job_id IN (SELECT id FROM jobs WHERE source = 'demo')")
    conn.execute("DELETE FROM gap_batches WHERE source_filter = 'demo'")
    cur = conn.execute("DELETE FROM jobs WHERE source = 'demo'")
    return cur.rowcount


def seed(force: bool = False) -> None:
    init_db()
    with connect() as conn:
        _ensure_schema(conn)
        non_demo = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE source != 'demo'").fetchone()[0]
        if non_demo and not force:
            print(f"⚠️  DB 已有 {non_demo} 筆非 demo 職缺（{DB_PATH}）。")
            print("   demo 資料會與真實資料混在同一列表（source='demo' 可辨識、--clear 可移除）。")
            print("   確定要混入請加 --force。")
            sys.exit(1)

        removed = clear_demo(conn)
        if removed:
            print(f"[demo] 先清除舊 demo 資料 {removed} 筆")

        jobs = _make_jobs()
        scored: list[dict] = []
        cur = conn.execute("INSERT INTO gap_batches (created_at, source_filter, min_score, job_count) "
                           "VALUES (datetime('now','localtime'), 'demo', 0, 0)")
        batch_id = cur.lastrowid

        by_tier: dict[str, list[int]] = {}
        for j in jobs:
            raw_jd = JD_TEMPLATE.format(
                title=j["title"], company=j["company"], domain=j["domain"],
                extra=j["extra"], smin=j["salary_min"], smax=j["salary_max"])
            # 約 6 成職缺有 gap 分析（模擬批次跑過 --top N 的狀態）
            has_gap = RNG.random() < 0.6
            gap_json, rec = _gap_json(j["score"], j["company"]) if has_gap else (None, None)
            cur = conn.execute(
                "INSERT INTO jobs (source, source_id, url, title, company, location, raw_jd, "
                "keyword, first_seen, last_seen, salary_min, salary_max, score, tier, tier_conf, "
                "posting_type, gap_analysis, recommend_score, gap_batch_id, gap_analyzed_at, company_norm) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (j["source"], j["source_id"], j["url"], j["title"], j["company"], j["location"],
                 raw_jd, j["keyword"], j["first_seen"], j["last_seen"], j["salary_min"],
                 j["salary_max"], j["score"], j["tier"], j["tier_conf"], j["posting_type"],
                 gap_json, rec, batch_id if has_gap else None,
                 _d(RNG.randint(0, 5)) if has_gap else None, j["company"]))
            job_id = cur.lastrowid
            by_tier.setdefault(j["tier"], []).append(job_id)
            if has_gap:
                d = json.loads(gap_json)
                scored.append({"id": job_id, "company": j["company"], "title": j["title"],
                               "rec": rec, "verdict": d["verdict"], "reason": d["recommend_reason"]})

        conn.execute("UPDATE gap_batches SET job_count = ?, summary_json = ? WHERE id = ?",
                     (len(scored), _batch_summary(scored), batch_id))

        # 應募漏斗（含 1 筆逾期跟進展示老化膠囊：applied 超過 7 天）
        pool = {t: list(ids) for t, ids in by_tier.items()}
        n_apps = 0
        for idx, (tier, status, rej_stage, rej_reason) in enumerate(FUNNEL_PLAN):
            if not pool.get(tier):
                continue
            job_id = pool[tier].pop()
            applied_ago = RNG.randint(3, 14)
            updated_ago = applied_ago if status == "applied" else RNG.randint(0, 4)
            if idx == 6:  # 固定造一筆逾期（applied 停留 10 天 > 節奏 7 天）
                applied_ago = updated_ago = 10
            conn.execute(
                "INSERT INTO applications (job_id, status, applied_at, last_updated, "
                "rejection_stage, rejection_reason, next_event) VALUES (?,?,?,?,?,?,?)",
                (job_id, status, _d(applied_ago), _d(updated_ago), rej_stage, rej_reason,
                 "6/30 14:00 一次面接 @Zoom" if status == "recruiter" and n_apps % 2 == 0 else None))
            n_apps += 1

        print(f"[demo] 已灌入 {len(jobs)} 筆虛構職缺、{len(scored)} 筆 gap 分析、"
              f"1 個推薦度報告批次、{n_apps} 筆應募記錄 → {DB_PATH}")
        print("[demo] 啟動 dashboard 查看：cd dashboard && bash run.sh")
        print("[demo] 移除 demo 資料：python3 -m tools.seed_demo --clear")


def main() -> None:
    ap = argparse.ArgumentParser(description="Demo Mode 種子資料")
    ap.add_argument("--clear", action="store_true", help="只清除 demo 資料")
    ap.add_argument("--force", action="store_true", help="DB 已有真實資料仍強制混入")
    args = ap.parse_args()
    if args.clear:
        with connect() as conn:
            n = clear_demo(conn)
        print(f"[demo] 已清除 demo 資料 {n} 筆職缺（含關聯 applications / gap_batch）")
        return
    seed(force=args.force)


if __name__ == "__main__":
    main()
