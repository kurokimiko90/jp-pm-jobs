"""CLI orchestrator：掃收件匣 → 分類 → 對應職缺 → 寫 inbox_mails → 生成 Gmail 草稿 → notify。

用法:
    python3 -m inbox.reply                 # 掃近 7 天未讀：分類 + 生成草稿 + Telegram 通知
    python3 -m inbox.reply --hours 15 --all # 精確掃最近 15 小時（自動輪詢用）
    python3 -m inbox.reply --days 14
    python3 -m inbox.reply --dry-run       # 只分類入庫，不碰 Gmail 草稿、不通知
    python3 -m inbox.reply --all           # 含已讀

永不自動寄信：草稿一律人工在 Gmail 確認後手動寄。
"""
from __future__ import annotations

import argparse

from inbox.fetch import fetch_recent
from inbox.rule_classify import classify_by_rules
from inbox.match import match_job
from inbox.store import init_inbox_db, list_pending_drafts, msg_exists, upsert_mail

# 不自動生成草稿的分類（schedule_confirmed 是系統通知，不需回信，
# 由 inbox.schedule 抽出確定日時寫入 applications.next_event；
# application_ack 是應募受付の自動返信，由 inbox.application_ack 寫入 applications；
# rejection 是不合格通知，不需回信，由 inbox.rejection_ack 寫入 applications）
_NO_DRAFT = {"other", "rejection", "schedule_confirmed", "application_ack"}
# 每輪補生上限：LLM 半故障（呼叫慢）時避免單輪掃描拖過 30 分鐘間隔，積壓分多輪消化
_RETRY_MAX_PER_RUN = 8
# 草稿門檻：規則分類的網域 fallback（conf 0.4，如 LinkedIn 通知信）只入庫不生草稿
_DRAFT_MIN_CONF = 0.5


def _retry_pending(days: int, exclude: set[str], hours: int | None = None) -> int:
    """補生先前 LLM 故障未成的草稿（自癒）。本輪剛失敗的（exclude）留給下輪，避免連打故障中的 LLM。

    正文優先用 DB 的 body_raw；舊資料未存正文則按 msg id 回 Gmail 重抓。
    """
    pending = [
        r for r in list_pending_drafts(days, tuple(_NO_DRAFT), _DRAFT_MIN_CONF, hours=hours)
        if r["gmail_msg_id"] not in exclude
    ][:_RETRY_MAX_PER_RUN]
    if not pending:
        return 0
    from inbox.draft import draft_for  # 延遲 import
    from inbox.fetch import fetch_by_id

    print(f"♻ 補生先前失敗的草稿：{len(pending)} 封")
    drafted = 0
    for rec in pending:
        try:
            rec["body"] = rec.get("body_raw") or ""
            if not rec["body"]:
                mail = fetch_by_id(rec["gmail_msg_id"])
                rec["body"] = mail.get("body", "")
                rec["sender_email"] = mail.get("sender_email")
            if draft_for(rec):
                drafted += 1
        except Exception as e:  # 單封失敗不中斷整批
            print(f"  ⚠ 補生失敗 {(rec.get('subject') or '')[:30]}: {e}")
    return drafted


def _notify_confirmed(confirmed: list[dict]) -> None:
    """面接日程確定を Telegram 通知（公司＋日時直接見える形）。"""
    lines = []
    for c in confirmed:
        when = f"{c['date']}({c['weekday']}) {c['time']}"
        tag = "" if c.get("job_id") else "（応募記録に未対応・要手動確認）"
        lines.append(f"・{c.get('company') or '?'}  {when}{tag}")
    try:
        from notify import send
        send("📅 面接日程確定\n" + "\n".join(lines))
    except Exception as e:
        print(f"  （日程通知略過：{e}）")


def run(
    days: int = 7, hours: int | None = None, max_results: int = 30, dry_run: bool = False,
    unread_only: bool = True, starred_only: bool = False,
) -> list[dict]:
    init_inbox_db()
    mails = fetch_recent(days=days, hours=hours, max_results=max_results,
                         unread_only=unread_only, starred_only=starred_only)
    print(f"拉到 {len(mails)} 封郵件")

    from inbox.application_ack import apply_ack, notify_applied
    from inbox.rejection_ack import apply_rejection, notify_rejected
    from inbox.schedule import apply_schedule

    processed: list[dict] = []
    confirmed: list[dict] = []
    applied: list[dict] = []
    rejected: list[dict] = []
    for mail in mails:
        if msg_exists(mail["gmail_msg_id"]):
            continue
        cls = classify_by_rules(mail)  # 規則式分類（LLM 版 classify_mail 暫停使用）
        job_id = match_job(mail)
        record = {**mail, **cls, "job_id": job_id, "status": "classified",
                  "body_raw": mail.get("body")}  # 存正文供草稿補生（LLM 故障自癒）
        print(f"  [{cls['category']:>16}] conf={cls['confidence']:.2f} "
              f"job_id={job_id}  {mail.get('subject', '')[:40]}")
        if cls["category"] == "other":
            continue  # 廣告/電子報/自動群發信箱：不入庫，不佔 dedup 名額，下次照樣重掃但不留痕

        if cls["category"] == "application_ack" and not dry_run:
            try:
                ack = apply_ack(mail)
            except Exception as e:  # 單封失敗不中斷整批
                ack = None
                print(f"  ⚠ 応募受付抽出失敗 {mail.get('subject', '')[:30]}: {e}")
            if ack:
                record["job_id"] = ack.get("job_id") or job_id
                record["summary"] = (
                    f"応募受付 {ack['company']}／{ack['title'][:40]}"
                    f"（{ack['reason']}）"
                )
                if ack["recorded"]:
                    print(f"    ↳ applications 記録: job#{ack['job_id']} applied")
                    applied.append(ack)
                elif ack["reason"] == "already":
                    print(f"    ↳ 既存記録あり job#{ack['job_id']}（{ack['status']}）、更新なし")
                else:
                    print(f"    ↳ 対応する求人が DB に無し: {ack['company']}")

        if cls["category"] == "rejection" and not dry_run:
            try:
                infos = apply_rejection(mail)
            except Exception as e:  # 單封失敗不中斷整批
                infos = []
                print(f"  ⚠ 不合格抽出失敗 {mail.get('subject', '')[:30]}: {e}")
            if infos:
                record["job_id"] = infos[0].get("job_id") or job_id
                record["summary"] = "不合格 " + "／".join(
                    f"{i['company']}（{i['reason_code']}）" for i in infos
                )[:200]
                for i in infos:
                    if i["reason_code"] == "recorded":
                        print(f"    ↳ applications 記録: job#{i['job_id']} rejected")
                        rejected.append(i)
                    elif i["reason_code"] == "already":
                        print(f"    ↳ 既に rejected job#{i['job_id']}、更新なし")
                    else:
                        print(f"    ↳ 対応する求人が DB に無し: {i['company']}")

        if cls["category"] == "schedule_confirmed" and not dry_run:
            try:
                info = apply_schedule(mail)
            except Exception as e:  # 單封失敗不中斷整批
                info = None
                print(f"  ⚠ 日程抽出失敗 {mail.get('subject', '')[:30]}: {e}")
            if info:
                record["job_id"] = info.get("job_id") or job_id
                if info.get("job_id") and info.get("next_event"):
                    record["summary"] = f"日程確定 → {info['next_event']}"
                    print(f"    ↳ next_event: {info['next_event']}"
                          f"{'' if info['changed'] else '（同値、更新なし）'}")
                else:
                    print(f"    ↳ 対応する応募が見つからず: {info.get('company', '?')}")
                if info.get("changed") or not info.get("job_id"):
                    confirmed.append(info)

        upsert_mail(record)
        processed.append(record)
        if not dry_run:
            try:
                from notify.events import notify_inbox_important
                notify_inbox_important(record)
            except Exception as e:  # 通知失敗不影響入庫
                print(f"  （重要信通知略過：{e}）")

    if applied:
        notify_applied(applied)
    if rejected:
        notify_rejected(rejected)
    if confirmed:
        _notify_confirmed(confirmed)

    if dry_run:
        print(f"\n[dry-run] {len(processed)} 封已分類入庫，未生成草稿。")
        return processed

    # Phase 3: 生成 Gmail 草稿（永不 send）+ 通知
    from inbox.draft import draft_for  # 延遲 import

    drafted = 0
    for rec in processed:
        if rec["category"] in _NO_DRAFT or rec["confidence"] < _DRAFT_MIN_CONF:
            continue
        try:
            if draft_for(rec):
                drafted += 1
        except Exception as e:  # 單封失敗不中斷整批
            print(f"  ⚠ 草稿生成失敗 {rec.get('subject', '')[:30]}: {e}")

    drafted += _retry_pending(days, exclude={r["gmail_msg_id"] for r in processed}, hours=hours)

    print(f"\n✅ {drafted} 封已生成 Gmail 草稿待確認")
    if drafted:
        try:
            from notify import send
            send(f"📬 {drafted} 封招聘郵件已生成 Gmail 草稿待確認")
        except Exception as e:
            print(f"  （通知略過：{e}）")
    return processed


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--hours", type=int, help="精確的滾動掃描窗口（例如 15）")
    ap.add_argument("--max-results", type=int, default=30)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--all", action="store_true", help="含已讀郵件")
    ap.add_argument("--starred", action="store_true", help="只掃已加星號的郵件（is:starred）")
    args = ap.parse_args()
    if args.hours is not None and args.hours <= 0:
        ap.error("--hours 必須大於 0")
    run(days=args.days, hours=args.hours, max_results=args.max_results, dry_run=args.dry_run,
        unread_only=not args.all, starred_only=args.starred)
