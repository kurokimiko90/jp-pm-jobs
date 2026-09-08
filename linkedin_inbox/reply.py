"""CLI orchestrator：CDP 連 LinkedIn Messaging → 抓未讀 → 規則分類（只留招聘相關）
→ 生成回覆草稿（存檔，不送出）→ Telegram 通知。

用法:
    python3 -m linkedin_inbox.reply             # 抓未讀、分類、生成草稿、通知
    python3 -m linkedin_inbox.reply --dry-run   # 只分類入庫，不生成草稿

永不自動發送：LinkedIn 無官方草稿 API，草稿只存在 DB + output/linkedin_drafts/*.txt，
人工複製貼上到 LinkedIn 網頁後手動按送出。
"""
from __future__ import annotations

import argparse
import os

from playwright.sync_api import sync_playwright

from tools.app_config import load as _load_app_config
from tools.cdp import ensure_cdp
from linkedin_inbox.fetch import fetch_unread
from linkedin_inbox.rule_classify import classify
from linkedin_inbox.store import (
    conversation_exists, init_linkedin_db, list_pending_drafts, upsert_conversation,
)

_CFG = _load_app_config("scraping").get("linkedin_cdp", {}) or {}
CDP_PORT = _CFG.get("port", 9253)
CDP_USER_DATA_DIR = _CFG.get("user_data_dir", "~/.chrome-linkedin")
# job scraper (scrape.py) は起動時に /jobs を開くが、本モジュールは Messaging を扱うため
# 独自の start_url を使う（既存 Chrome が起動済みなら無関係、fetch_unread が goto し直す）。
CDP_START_URL = "https://www.linkedin.com/messaging/"

_DRAFT_MIN_CONF = 0.5
_RETRY_MAX_PER_RUN = 8


def _retry_pending() -> int:
    """補生先前 LLM 故障未成的草稿（自癒，比照 inbox/reply.py 的 _retry_pending）。"""
    pending = list_pending_drafts(_DRAFT_MIN_CONF)[:_RETRY_MAX_PER_RUN]
    if not pending:
        return 0
    from linkedin_inbox.draft import draft_for  # 延遲 import

    print(f"♻ 補生先前失敗的草稿：{len(pending)} 件")
    drafted = 0
    for conv in pending:
        try:
            if draft_for(conv):
                drafted += 1
        except Exception as e:  # 單件失敗不中斷整批
            print(f"  ⚠ 補生失敗 {conv.get('sender_name', '')}: {e}")
    return drafted


def run(dry_run: bool = False, max_conversations: int = 20) -> list[dict]:
    init_linkedin_db()
    user_data_dir = os.path.expanduser(str(CDP_USER_DATA_DIR))

    proc = ensure_cdp(CDP_PORT, user_data_dir, CDP_START_URL)
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
            ctx = browser.contexts[0]
            page = ctx.new_page()
            conversations = fetch_unread(page, max_conversations=max_conversations)
            page.close()
            browser.close()
    finally:
        if proc is not None:
            proc.terminate()

    print(f"抓到 {len(conversations)} 段未讀對話")

    processed: list[dict] = []
    for conv in conversations:
        if conversation_exists(conv["conversation_id"]):
            continue  # 已處理過（跨輪掃描去重）
        cls = classify(conv)
        status = "classified" if cls["category"] == "recruiting" else "skipped"
        record = {**conv, **cls, "status": status}
        print(f"  [{cls['category']:>10}] conf={cls['confidence']:.2f}  {conv.get('sender_name', '')}")
        upsert_conversation(record)
        if cls["category"] == "recruiting":
            processed.append(record)

    if dry_run:
        print(f"\n[dry-run] {len(processed)} 段招聘相關對話已分類入庫，未生成草稿。")
        return processed

    from linkedin_inbox.draft import draft_for  # 延遲 import

    drafted = 0
    drafted_who: list[str] = []
    for conv in processed:
        if conv["confidence"] < _DRAFT_MIN_CONF:
            continue
        try:
            if draft_for(conv):
                drafted += 1
                drafted_who.append(conv.get("sender_name") or "?")
        except Exception as e:  # 單件失敗不中斷整批
            print(f"  ⚠ 草稿生成失敗 {conv.get('sender_name', '')}: {e}")

    drafted += _retry_pending()

    print(f"\n✅ {drafted} 件已生成回覆草稿（未送出，見 output/linkedin_drafts/）")
    if drafted:
        try:
            from notify import send

            names = "、".join(drafted_who) if drafted_who else ""
            send(
                f"💼 LinkedIn {drafted} 件招聘訊息已生成回覆草稿待確認（未送出）\n"
                f"{names}\n"
                f"→ output/linkedin_drafts/ 查看，人工複製貼上到 LinkedIn 後手動送出"
            )
        except Exception as e:
            print(f"  （通知略過：{e}）")
    return processed


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-conversations", type=int, default=20)
    args = ap.parse_args()
    run(dry_run=args.dry_run, max_conversations=args.max_conversations)
