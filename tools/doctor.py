#!/usr/bin/env python3
"""Pipeline 健康檢查 — 一鍵驗證整個求職系統是否正常。

Zero-token：純檔案檢查 + DB 查詢。

用法:
    python3 -m tools.doctor          # 完整檢查
    python3 -m tools.doctor --json   # JSON 輸出
    python3 -m tools.doctor --fix    # 嘗試自動修復可修復的問題

檢查項目:
    1. DB 連線與表結構
    2. 必要目錄和文件
    3. Scraper provider 可用性
    4. 數據完整性（孤兒記錄、缺欄位）
    5. 過期職缺比例
    6. Pipeline 依賴（Python packages）
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"


class Check:
    def __init__(self, name: str, category: str):
        self.name = name
        self.category = category
        self.status: str = "pending"
        self.message: str = ""
        self.fix_applied: bool = False

    def ok(self, msg: str = "") -> "Check":
        self.status = "ok"
        self.message = msg
        return self

    def warn(self, msg: str) -> "Check":
        self.status = "warn"
        self.message = msg
        return self

    def fail(self, msg: str) -> "Check":
        self.status = "fail"
        self.message = msg
        return self

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "category": self.category,
            "status": self.status,
            "message": self.message,
        }


def check_db() -> list[Check]:
    checks = []
    c = Check("DB 檔案存在", "database")
    db_path = DATA_DIR / "jobs.sqlite"
    if not db_path.exists():
        checks.append(c.fail(f"找不到 {db_path}"))
        return checks
    checks.append(c.ok(str(db_path)))

    try:
        from tracker import db as tdb
        c2 = Check("DB 連線", "database")
        with tdb.connect() as conn:
            tables = [
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            ]
        if "jobs" not in tables:
            checks.append(c2.fail("缺少 jobs 表"))
        elif "applications" not in tables:
            checks.append(c2.warn("缺少 applications 表"))
        else:
            checks.append(c2.ok(f"表: {', '.join(sorted(tables))}"))
    except Exception as e:
        checks.append(Check("DB 連線", "database").fail(str(e)))

    return checks


def check_data_integrity() -> list[Check]:
    checks = []
    try:
        from tracker import db as tdb

        with tdb.connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            c = Check("職缺總數", "data")
            if total == 0:
                checks.append(c.warn("DB 中無職缺"))
            else:
                checks.append(c.ok(f"{total} 筆"))

            no_url = conn.execute("SELECT COUNT(*) FROM jobs WHERE url IS NULL OR url = ''").fetchone()[0]
            c2 = Check("缺 URL 職缺", "data")
            if no_url > 0:
                checks.append(c2.warn(f"{no_url} 筆缺少 URL"))
            else:
                checks.append(c2.ok("全部有 URL"))

            no_jd = conn.execute("SELECT COUNT(*) FROM jobs WHERE raw_jd IS NULL OR raw_jd = ''").fetchone()[0]
            c3 = Check("缺 JD 全文", "data")
            ratio = round(no_jd / total * 100, 1) if total else 0
            if ratio > 50:
                checks.append(c3.warn(f"{no_jd}/{total} 筆缺 JD（{ratio}%）"))
            else:
                checks.append(c3.ok(f"{total - no_jd}/{total} 筆有 JD（{100 - ratio}%）"))

            no_score = conn.execute("SELECT COUNT(*) FROM jobs WHERE score IS NULL").fetchone()[0]
            c4 = Check("未評分職缺", "data")
            if no_score > total * 0.5 and total > 0:
                checks.append(c4.warn(f"{no_score}/{total} 筆未評分"))
            else:
                checks.append(c4.ok(f"{total - no_score}/{total} 筆已評分"))

            cutoff = (date.today() - timedelta(days=14)).isoformat()
            stale = conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE last_seen < ?", (cutoff,)
            ).fetchone()[0]
            c5 = Check("14 天未更新職缺", "data")
            if stale > total * 0.3 and total > 0:
                checks.append(c5.warn(f"{stale}/{total} 筆超過 14 天未更新"))
            else:
                checks.append(c5.ok(f"{stale} 筆"))

            sources = dict(conn.execute(
                "SELECT source, COUNT(*) FROM jobs GROUP BY source"
            ).fetchall())
            checks.append(Check("來源分佈", "data").ok(json.dumps(sources, ensure_ascii=False)))

            tables = [
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            ]
            if "applications" in tables:
                orphans = conn.execute(
                    "SELECT COUNT(*) FROM applications a "
                    "LEFT JOIN jobs j ON j.id = a.job_id "
                    "WHERE j.id IS NULL"
                ).fetchone()[0]
                c6 = Check("Application 孤兒記錄", "data")
                if orphans > 0:
                    checks.append(c6.warn(f"{orphans} 筆 application 指向不存在的 job"))
                else:
                    checks.append(c6.ok("無孤兒"))

                dup_apps = conn.execute(
                    "SELECT job_id, COUNT(*) as cnt FROM applications "
                    "GROUP BY job_id HAVING cnt > 1"
                ).fetchall()
                c7 = Check("Application 重複投遞", "data")
                if dup_apps:
                    checks.append(c7.warn(f"{len(dup_apps)} 個 job_id 有重複投遞"))
                else:
                    checks.append(c7.ok("無重複"))

            cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
            if "liveness_status" in cols:
                liveness_stats = dict(conn.execute(
                    "SELECT COALESCE(liveness_status, 'unchecked') AS s, COUNT(*) "
                    "FROM jobs GROUP BY s"
                ).fetchall())
                c8 = Check("Liveness 檢測狀態", "data")
                checks.append(c8.ok(json.dumps(liveness_stats, ensure_ascii=False)))

    except Exception as e:
        checks.append(Check("數據完整性", "data").fail(str(e)))

    return checks


def check_directories(do_fix: bool = False) -> list[Check]:
    checks = []
    required_dirs = [
        DATA_DIR,
        OUTPUT_DIR,
        ROOT / "resume",
        ROOT / "interview",
        ROOT / "scrapers",
        ROOT / "analyzer",
        ROOT / "tools",
        ROOT / "tracker",
    ]
    for d in required_dirs:
        c = Check(f"目錄 {d.name}/", "filesystem")
        if d.exists():
            checks.append(c.ok())
        elif do_fix:
            d.mkdir(parents=True, exist_ok=True)
            c.fix_applied = True
            checks.append(c.ok("已建立"))
        else:
            checks.append(c.warn(f"不存在: {d}"))
    return checks


def check_providers() -> list[Check]:
    checks = []
    c = Check("Provider 發現", "scrapers")
    try:
        from scrapers.provider import discover_providers
        providers = discover_providers()
        if not providers:
            checks.append(c.warn("未發現任何 provider"))
        else:
            names = [p.id for p in providers]
            checks.append(c.ok(f"{len(providers)} 個: {', '.join(names)}"))
    except Exception as e:
        checks.append(c.fail(str(e)))
    return checks


def check_dependencies() -> list[Check]:
    checks = []
    required = ["httpx", "playwright"]
    optional = ["openai"]
    for pkg in required:
        c = Check(f"依賴 {pkg}", "deps")
        try:
            importlib.import_module(pkg)
            checks.append(c.ok())
        except ImportError:
            checks.append(c.fail(f"pip install {pkg}"))
    for pkg in optional:
        c = Check(f"依賴 {pkg}（選用）", "deps")
        try:
            importlib.import_module(pkg)
            checks.append(c.ok())
        except ImportError:
            checks.append(c.warn(f"未安裝，部分功能不可用"))
    return checks


def run_doctor(do_fix: bool = False) -> dict:
    all_checks: list[Check] = []
    all_checks.extend(check_db())
    all_checks.extend(check_data_integrity())
    all_checks.extend(check_directories(do_fix))
    all_checks.extend(check_providers())
    all_checks.extend(check_dependencies())

    summary = {"ok": 0, "warn": 0, "fail": 0}
    for c in all_checks:
        summary[c.status] = summary.get(c.status, 0) + 1

    healthy = summary["fail"] == 0
    return {
        "healthy": healthy,
        "summary": summary,
        "checks": [c.to_dict() for c in all_checks],
    }


def print_report(result: dict) -> None:
    icons = {"ok": "✅", "warn": "⚠️", "fail": "❌", "pending": "⏳"}
    print("=" * 50)
    print("  jp-pm-jobs Pipeline 健康檢查")
    print("=" * 50)

    current_cat = ""
    for c in result["checks"]:
        if c["category"] != current_cat:
            current_cat = c["category"]
            print(f"\n[{current_cat}]")
        icon = icons.get(c["status"], "?")
        msg = f" — {c['message']}" if c["message"] else ""
        print(f"  {icon} {c['name']}{msg}")

    s = result["summary"]
    print(f"\n{'='*50}")
    status = "HEALTHY" if result["healthy"] else "UNHEALTHY"
    print(f"  {status} | ✅ {s['ok']}  ⚠️ {s['warn']}  ❌ {s['fail']}")
    print("=" * 50)


def main() -> None:
    p = argparse.ArgumentParser(description="Pipeline 健康檢查")
    p.add_argument("--json", action="store_true", help="JSON 輸出")
    p.add_argument("--fix", action="store_true", help="嘗試自動修復")
    args = p.parse_args()

    result = run_doctor(do_fix=args.fix)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_report(result)

    sys.exit(0 if result["healthy"] else 1)


if __name__ == "__main__":
    main()
