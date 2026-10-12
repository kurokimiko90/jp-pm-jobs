"""官網直投管線 — 探測窗口 → 公司調查 + 応募メール + 志望動機特化履歴書 → Telegram 審閱。

添付書類の方針:
  - 職務経歴書 = 手作りの最新完成版（tools.resume_assets）をそのまま添付（LLM 不介入）
  - 履歴書 = 同完成版を基底に「志望の動機など」欄だけ JD/公司事実で特化（tools.rirekisho_tailor）
  - 個人情報（氏名・生年月日・住所・写真）は基底 HTML にローカル保持、LLM には渡らない

流程（狀態機，存 direct_apply 表）：
  liveness 即時複查（httpx，零 LLM）→ 已下架 = skipped(closed)，不進入下一步
    → detect（規則+LLM fallback）→ detected / not_found
    → 生成投遞包（brief 1 call + 応募メール 1 call + 履歴書志望動機 1 call，全走 miko-ws）
    → pack_ready → 一律建 Gmail 草稿：高信心 email 填收件人（drafted）；
      低信心 / 無 email 收件人留空（停 pack_ready，人工確認窗口後在 Gmail
      補收件人，或 Web UI / Telegram 確認後重建）→ Telegram 推送（附文件）
    → 你在 Gmail 檢查草稿、手動寄出 → check_sent() 偵測 → sent + applications(channel=direct)

安全紀律：
  - **永不 send** — 只呼叫 drafts().create()，寄出永遠靠你在 Gmail 手動按
  - 自動草稿只在 email 信心 ≥ auto_draft_min_conf 時建；低信心仍需人工確認
  - 同 company_norm 已有應募 → 不進入流程（防重複投遞）

CLI:
    python3 -m tools.direct_apply --job-id 123              # 單筆全管線
    python3 -m tools.direct_apply --job-id 123 --dry-run    # 只探測，不寫 DB / 不呼叫 LLM
    python3 -m tools.direct_apply --backfill --limit 5      # 常駐掃描一輪（基礎評分>55・推薦分>80 日本職缺）
    python3 -m tools.direct_apply --create-draft 123        # 確認後建 Gmail 草稿
    python3 -m tools.direct_apply --check-sent              # 偵測已寄出草稿 → 標 applied
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from datetime import datetime
from pathlib import Path

from tools import app_config, resume_assets
from tools.company_contact import ContactResult, detect, extract_emails, is_japan_job, llm_extract
from tracker import db

ROOT = Path(__file__).parent.parent

MIN_SCORE = int(app_config.get("direct_apply", "min_score", 55))
MIN_RECOMMEND = int(app_config.get("direct_apply", "min_recommend", 80))
BATCH_LIMIT = int(app_config.get("direct_apply", "batch_limit", 5))
# pack_ready 後自動建 Gmail 草稿（永不 send）；低信心 email 仍停 pack_ready 等人工確認
AUTO_DRAFT = bool(app_config.get("direct_apply", "auto_draft", True))
AUTO_DRAFT_MIN_CONF = float(app_config.get("direct_apply", "auto_draft_min_conf", 0.6))


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _load_job(job_id: int) -> dict:
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        sys.exit(f"job_id {job_id} 不在 DB")
    return dict(row)


# ── 探測 ─────────────────────────────────────────────────


def run_detect(job: dict, *, use_llm: bool = True) -> ContactResult:
    """三層規則探測；抽不到窗口時 LLM fallback 補一次。"""
    result = detect(job)
    if result.apply_method == "none" and use_llm and result.pages_fetched:
        # 規則失敗 → 把抓到的採用頁重新取文本丟 LLM（1 call）
        import requests
        from tools.company_contact import UA, _strip_tags
        texts = []
        for url in result.pages_fetched[1:4]:  # 跳過搜尋結果頁
            try:
                r = requests.get(url, headers={"User-Agent": UA}, timeout=10)
                texts.append(f"[{url}]\n" + _strip_tags(r.text)[:3000])
            except Exception:
                continue
        if texts:
            found = llm_extract("\n\n".join(texts))
            email = (found.get("application_email") or "").strip()
            if email:
                result.emails = extract_emails(email, "llm") or []
            if found.get("form_url"):
                result.form_url = found["form_url"]
            if found.get("careers_url") and not result.careers_url:
                result.careers_url = found["careers_url"]
            result.notes = found.get("notes") or ""
            if result.emails:
                result.apply_method = "email"
            elif result.form_url:
                result.apply_method = "form"
    return result


def save_detect(job_id: int, result: ContactResult) -> None:
    db.upsert_direct_apply(
        job_id,
        status="detected" if result.apply_method != "none" else "not_found",
        apply_method=result.apply_method,
        careers_url=result.careers_url or None,
        form_url=result.form_url or None,
        emails_json=json.dumps(result.emails, ensure_ascii=False),
        official_domain=result.official_domain or None,
        detected_at=_now(),
        error=None,
    )


# ── 投遞包生成（重用 prep.py stages，冪等：已有檔案不重跑）──


def generate_pack(job: dict) -> Path | None:
    """brief + 応募メール + 志望動機を JD 特化した履歴書。已存在的產物跳過（省 LLM）。

    職務経歴書は手作りの最新完成版（tools.resume_assets）をそのまま添付するため
    生成しない。公司特化は「応募メール」と「履歴書の志望の動機など欄」が担う。
    """
    from prep import (APPLY_DIR, _detect_lang, _slug, apply_brief,
                      apply_en_brief_cover, apply_en_resume, apply_mail)

    lang = _detect_lang(job)
    slug = _slug(job.get("company") or "")
    pack_dir = APPLY_DIR / (f"{job['id']}_{slug}_en" if lang == "en" else f"{job['id']}_{slug}")
    pack_dir.mkdir(parents=True, exist_ok=True)

    if lang == "en":
        if not (pack_dir / "01_company_brief.md").exists():
            apply_en_brief_cover(job, "", pack_dir, False)
        if not (pack_dir / "04_resume_tailored.md").exists():
            apply_en_resume(job, "", pack_dir, False)
        mail_ok = (pack_dir / "03_cover_letter.md").exists()
    else:
        if not (pack_dir / "01_company_brief.md").exists():
            apply_brief(job, "", pack_dir, False)
        facts = ""
        brief = pack_dir / "01_company_brief.md"
        if brief.exists():
            facts = brief.read_text(encoding="utf-8")[:3000]
        if not (pack_dir / "06_oubo_mail.md").exists():
            apply_mail(job, facts, pack_dir, False)
        if not (pack_dir / "05_rirekisho.html").exists():
            try:
                from tools.rirekisho_tailor import generate as gen_rirekisho
                gen_rirekisho(job, pack_dir / "05_rirekisho.html", facts=facts,
                              prompts_dir=pack_dir / "_prompts", timeout=90)
            except Exception as e:
                print(f"  ✗ 志望動機特化履歴書 失敗: {e}", file=sys.stderr)
        write_pack_manifest(pack_dir)
        mail_ok = (pack_dir / "06_oubo_mail.md").exists()

    return pack_dir if mail_ok else None


def write_pack_manifest(pack_dir: Path) -> Path:
    """02_documents.md — 実際に添付される PDF と人手チェック項目（zero LLM）。"""
    tailored = pack_dir / "05_rirekisho.html"
    rirekisho = _rirekisho_pdf(pack_dir)
    shokumu = resume_assets.shokumu_pdf()
    names = resume_assets.attachment_names()
    try:
        rel_dir = pack_dir.relative_to(ROOT)
    except ValueError:
        rel_dir = pack_dir

    def _rel(p: Path | None) -> str:
        if p is None or not p.exists():
            return "（見つからない — config/resume.yaml を確認）"
        try:
            return f"`{p.relative_to(ROOT)}`"
        except ValueError:
            return f"`{p}`"

    lines = [
        "# 添付書類（Gmail 草稿に自動添付）\n",
        f"- {names['shokumu']} ← {_rel(shokumu)}（最新完成版・JD ごとの改変なし）",
        f"- {names['rirekisho']} ← {_rel(rirekisho)}"
        + ("（志望の動機など欄を JD 特化）" if tailored.exists() else "（特化生成に失敗 → 全局版）"),
        "\n## 送る前のチェック",
        "- ☐ 履歴書の志望動機が会社固有か（一般論になっていないか）",
        "- ☐ 数字・固有名詞がプロフィールの事実と一致するか",
        "- ☐ 応募メール本文の宛先・社名・職位名が正しいか",
        "\n## 再生成",
        "```bash",
        f"python3 -m tools.rirekisho_tailor --job-id {pack_dir.name.split('_')[0]} \\",
        f"  --facts-file {rel_dir}/01_company_brief.md --out {rel_dir}/05_rirekisho.html",
        "```",
    ]
    out = pack_dir / "02_documents.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def _mail_file(pack_dir: Path) -> Path | None:
    for name in ("06_oubo_mail.md", "03_cover_letter.md"):
        p = pack_dir / name
        if p.exists():
            return p
    return None


# ── 自動 Gmail 草稿（永不 send）──────────────────────────


def pick_auto_draft_email(emails: list[dict], min_conf: float = AUTO_DRAFT_MIN_CONF) -> str | None:
    """信心最高的候選 ≥ 門檻 → 回 email；否則 None（留給人工確認）。"""
    if not emails:
        return None
    top = max(emails, key=lambda e: e.get("confidence") or 0)
    if (top.get("confidence") or 0) >= min_conf:
        return (top.get("email") or "").strip() or None
    return None


def _auto_draft(job_id: int, emails: list[dict]) -> bool:
    """投遞包完成 → 一律建 Gmail 草稿（永不 send）。

    高信心 email → 填收件人、標 drafted；低信心 / 無 email → 收件人留空
    （人工確認窗口後在 Gmail 填上再寄），狀態停 pack_ready。
    失敗不擋流程（保持 pack_ready 可人工補）。"""
    email = pick_auto_draft_email(emails)
    if email:
        db.upsert_direct_apply(job_id, confirmed_email=email)
    try:
        return bool(create_gmail_draft(job_id, require_confirmed=False)) and bool(email)
    except (Exception, SystemExit) as e:  # create_gmail_draft CLI 路徑用 sys.exit
        print(f"  ⚠ 自動建草稿失敗（{e}）— 保持 pack_ready，可人工確認後重建",
              file=sys.stderr)
        return False


# ── 單筆全管線 ────────────────────────────────────────────


def process_one(job_id: int, *, dry_run: bool = False, notify: bool = True) -> str:
    """探測 → 生成 → 通知。回傳最終 status。dry_run 只探測、零寫入零 LLM。"""
    job = _load_job(job_id)

    from tools.blacklist import is_blacklisted
    reason = is_blacklisted(job.get("company") or "")
    if reason:
        if not dry_run:
            db.upsert_direct_apply(job_id, status="skipped", error=f"黑名單: {reason}")
        return "skipped(blacklist)"
    # jobs.liveness_status 多半陳舊或從未檢查過（daily_liveness.sh 未排程常駐，
    # 只覆蓋「7 天未更新且高分」的一小批），高分職缺常在被 direct_apply 選中時
    # 根本還沒被檢查過。生成投遞包前先即時複查一次（httpx，零 LLM token），
    # 避免對已下架職缺白跑探測 + 3 次 LLM 呼叫 + 建草稿。
    # LinkedIn 已知限制：關閉後 URL 仍回 200、無登入內文讀不到「已下架」banner，
    # httpx 判不準（需 --cdp 已登入瀏覽器複查，見 tools/liveness.py），此處僅
    # 對能被 httpx 準確判斷的來源（indeed_jp/green/ashby 等）有效，非本管線阻斷點。
    from tools.liveness import run_liveness_check
    check = run_liveness_check(job_id=job_id, dry_run=dry_run)
    if check["results"]:
        fresh = check["results"][0]["liveness"]
        # uncertain/error（如 Indeed 403 反爬蟲擋下）不是結論性訊號，不覆蓋既有值——
        # 避免把先前已確認的 expired 誤判回「可能還開放」而繼續往下跑
        if fresh in ("active", "expired", "dead", "closed"):
            job["liveness_status"] = fresh
    liveness = job.get("liveness_status")
    if liveness in ("expired", "dead", "closed"):
        if not dry_run:
            db.upsert_direct_apply(job_id, status="skipped", error=f"職缺已關閉: {liveness}")
        return "skipped(closed)"
    if not is_japan_job(job):
        if not dry_run:
            db.upsert_direct_apply(job_id, status="skipped", error="非日本勤務地")
        return "skipped(not_japan)"

    print(f"[direct_apply] #{job_id} {job.get('company')} — {job['title'][:36]}")
    result = run_detect(job, use_llm=not dry_run)
    print(f"  探測: method={result.apply_method} emails={len(result.emails)} "
          f"careers={result.careers_url or '-'} form={result.form_url or '-'}")
    if dry_run:
        for e in result.emails[:3]:
            print(f"    候選: {e['email']} conf={e['confidence']} ({e['source']})")
        return f"dry_run({result.apply_method})"

    save_detect(job_id, result)

    # not_found 也生成投遞包（手動填窗口後可直接用），但通知標記「找不到窗口」
    pack_dir = generate_pack(job)
    if pack_dir is None:
        db.upsert_direct_apply(job_id, status="failed", error="投遞包生成失敗（LLM）")
        return "failed"

    db.upsert_direct_apply(
        job_id, status="pack_ready", pack_dir=str(pack_dir.relative_to(ROOT)))
    db.update_pack_status(job_id, "apply", str(pack_dir.relative_to(ROOT)))

    # 一律在 Gmail 建好草稿（永不 send，寄出仍靠手動）；
    # 高信心 email 填收件人，其餘留空收件人等人工確認
    drafted = False
    if AUTO_DRAFT:
        drafted = _auto_draft(job_id, result.emails)

    if notify:
        from notify.events import notify_direct_apply_ready
        notify_direct_apply_ready(job_id)
    return "drafted" if drafted else "pack_ready"


# ── 常駐掃描一輪 ──────────────────────────────────────────


def _run_one(job: dict, dry_run: bool) -> str:
    try:
        return process_one(job["id"], dry_run=dry_run)
    except Exception as e:
        print(f"  ✗ #{job['id']} 失敗: {e}", file=sys.stderr)
        if not dry_run:
            db.upsert_direct_apply(job["id"], status="failed", error=str(e)[:300])
        return "failed"


def backfill(limit: int = BATCH_LIMIT, *, dry_run: bool = False) -> dict:
    """選件（基礎評分>門檻 / 推薦分>門檻 / 日本 / 未應募 / 未處理）→ 逐筆 process_one。

    miko-ws 自癒：輪前 health 檢查，掛掉先 kickstart 重啓；輪中單筆失敗時
    用 miko_llm.probe()（真呼叫探活 — health 會在 brain 池死光時誤報 ok）
    判斷 gateway 是否實際可用，不可用 → kickstart 重啓一次（每輪上限 1 次）
    並重試該筆（generate_pack 冪等，已生成的產物不重耗）；重啓後仍不可用
    → 中止本輪，failed 記錄下輪自動重試。
    """
    if not app_config.get("direct_apply", "enabled", True):
        print("[direct_apply] config 已停用（direct_apply.enabled=false）")
        return {"processed": 0}

    from tools.blacklist import flag_blacklisted
    flag_blacklisted()  # 選件前先同步 blacklist.yaml → jobs.blacklisted，避免用舊值選中新加的黑名單公司

    from tools import miko_llm
    if not dry_run and not miko_llm.ensure_available():
        print("[direct_apply] miko-ws 不可用且重啓失敗 → 跳過本輪（記錄不動，下輪重試）",
              file=sys.stderr)
        return {"processed": 0, "gateway_down": 1}

    candidates = [
        dict(r) for r in db.direct_apply_candidates(MIN_RECOMMEND, MIN_SCORE, limit)
    ]
    japan = [j for j in candidates if is_japan_job(j)][:limit]
    print(f"[direct_apply] 候選 {len(candidates)} 筆 → 日本職缺 {len(japan)} 筆"
          f"（基礎評分>{MIN_SCORE}，推薦分>{MIN_RECOMMEND}，本輪上限 {limit}）")

    stats: dict[str, int] = {"processed": 0}
    restarted = False
    for job in japan:
        status = _run_one(job, dry_run)
        if status == "failed" and not dry_run and not miko_llm.probe():
            # probe 是真呼叫探活：health 誤報 ok（gateway 活著但 brain 池死光）
            # 時也能偵測到，才觸發重啓；單筆自身原因的失敗不動 gateway
            if not restarted and miko_llm.restart_gateway():
                restarted = True
                print(f"  ⟳ miko-ws 已重啓 → 重試 #{job['id']}")
                status = _run_one(job, dry_run)
            elif not miko_llm.is_available():
                stats["processed"] += 1
                stats[status] = stats.get(status, 0) + 1
                stats["gateway_down"] = 1
                print("[direct_apply] miko-ws 不可用且無法恢復 → 中止本輪（下輪重試）",
                      file=sys.stderr)
                break
        stats["processed"] += 1
        stats[status] = stats.get(status, 0) + 1
    print(f"[direct_apply] 本輪結果: {stats}")
    return stats


# ── Gmail 草稿（永不 send）───────────────────────────────


def create_gmail_draft(job_id: int, *, require_confirmed: bool = True) -> str | None:
    """建含附件的 Gmail 草稿（永不 send）。回傳 draft_id。

    require_confirmed=True（CLI 路徑）：email 未確認即中止。
    require_confirmed=False（管線自動建）：無確認 email 時收件人留空，
    人工確認窗口後在 Gmail 補收件人再寄；狀態不進 drafted。
    已有舊草稿時先刪再建（冪等，避免草稿箱重複）。"""
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    row = db.get_direct_apply(job_id)
    if row is None:
        sys.exit(f"#{job_id} 沒有直投記錄，先跑 --job-id {job_id}")
    to_email = row["confirmed_email"]
    if not to_email and require_confirmed:
        sys.exit(f"#{job_id} email 未確認（安全閘：確認後才建草稿）")

    pack_dir = ROOT / (row["pack_dir"] or "")
    mail_path = _mail_file(pack_dir) if row["pack_dir"] else None
    if mail_path is None:
        sys.exit(f"#{job_id} 找不到応募メール檔，先跑 --job-id {job_id}")

    text = mail_path.read_text(encoding="utf-8").strip()
    subject = ""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith(("件名:", "件名：", "Subject:")):
            subject = line.split(":", 1)[-1].split("：", 1)[-1].strip()
            lines = lines[:i] + lines[i + 1:]
            break
    body = "\n".join(lines).strip()
    if not subject:
        job = _load_job(job_id)
        subject = f"【応募】{job['title'][:30]}"

    mime = MIMEMultipart()
    if to_email:
        mime["To"] = to_email
    mime["Subject"] = subject
    mime.attach(MIMEText(body, "plain", "utf-8"))

    attached = _attach_documents(mime, pack_dir)
    for name in attached:
        print(f"  附件: {name}")

    from inbox.auth import get_service
    svc = get_service()
    if row["gmail_draft_id"]:  # 重跑 → 換掉舊草稿，避免草稿箱重複
        try:
            svc.users().drafts().delete(userId="me", id=row["gmail_draft_id"]).execute()
        except Exception:
            pass  # 舊草稿已被手動刪 / 已寄，直接建新的
    message = {"raw": base64.urlsafe_b64encode(mime.as_bytes()).decode()}
    draft = svc.users().drafts().create(userId="me", body={"message": message}).execute()
    draft_id = draft.get("id")
    if draft_id:
        db.upsert_direct_apply(
            job_id,
            status="drafted" if to_email else "pack_ready",
            gmail_draft_id=draft_id)
        print(f"✓ Gmail 草稿已建（未寄出）: {draft_id} → "
              f"{to_email or '（收件人留空，確認窗口後填）'}")
    return draft_id


def _rirekisho_pdf(pack_dir: Path) -> Path | None:
    """附件用履歴書 PDF：志望動機を JD 特化した pack 内版を優先、無ければ全局版。

    pack 内 HTML はあるが PDF が無い（PDF 変換だけ失敗した）場合はここで再変換。
    """
    tailored = pack_dir / "05_rirekisho.html"
    if tailored.exists():
        pdf = tailored.with_suffix(".pdf")
        if pdf.exists():
            return pdf
        try:
            from resume.jp.render import export_pdf
            return export_pdf(tailored)
        except Exception as e:
            print(f"  ⚠ 特化履歴書 PDF 変換失敗（{e}）→ 全局版で添付", file=sys.stderr)
    fallback = resume_assets.rirekisho_pdf()
    return fallback if fallback.exists() else None


def _attach_documents(mime, pack_dir: Path) -> list[str]:
    """職務経歴書（最新完成版）+ 履歴書（志望動機 JD 特化版）PDF を添付。

    ファイル名は日本の慣例「氏名_職務経歴書.pdf」。氏名はローカルの
    resume/jp/data.yaml からのみ取得（LLM には一切渡らない）。
    """
    from email.mime.application import MIMEApplication

    names = resume_assets.attachment_names()
    shokumu = resume_assets.shokumu_pdf()
    pairs: list[tuple[Path | None, str]] = [
        (shokumu if shokumu.exists() else None, names["shokumu"]),
        (_rirekisho_pdf(pack_dir), names["rirekisho"]),
    ]

    attached: list[str] = []
    for path, fname in pairs:
        if path is None or not path.exists():
            continue
        part = MIMEApplication(path.read_bytes(), _subtype="pdf")
        part.add_header("Content-Disposition", "attachment", filename=("utf-8", "", fname))
        mime.attach(part)
        attached.append(fname)
    if not attached:
        print("  ⚠ 添付書類が 1 件も見つからない（config/resume.yaml を確認）",
              file=sys.stderr)
    return attached


# ── 寄出偵測（drafts 消失 + SENT 有對應信 → applied）─────


def check_sent(*, notify: bool = True) -> int:
    """status=drafted 的草稿：Gmail 草稿已消失且 SENT 有寄給該地址的信
    → 標 sent + applications(channel=direct)。回傳轉換筆數。"""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT job_id, gmail_draft_id, confirmed_email FROM direct_apply "
            "WHERE status = 'drafted' AND gmail_draft_id IS NOT NULL "
            "AND COALESCE(confirmed_email, '') != ''").fetchall()
    if not rows:
        return 0

    from googleapiclient.errors import HttpError

    from inbox.auth import get_service
    svc = get_service()
    converted = 0
    for r in rows:
        try:
            svc.users().drafts().get(userId="me", id=r["gmail_draft_id"]).execute()
            continue  # 草稿還在 → 未寄
        except HttpError as e:
            if e.resp.status != 404:
                continue  # API 錯誤，下輪再試
        # 草稿消失：查 SENT 佐證（防「刪除草稿」誤判為寄出）
        sent_hit = svc.users().messages().list(
            userId="me", q=f"in:sent to:{r['confirmed_email']}", maxResults=1,
        ).execute().get("messages")
        if sent_hit:
            db.record_application(r["job_id"], "applied",
                                  notes="官網直投（Gmail）", channel="direct")
            db.upsert_direct_apply(r["job_id"], status="sent")
            converted += 1
            if notify:
                from notify.events import notify_direct_apply_sent
                notify_direct_apply_sent(r["job_id"])
        else:
            db.upsert_direct_apply(
                r["job_id"], status="detected",
                error="草稿被刪除但 SENT 無對應信 — 請重建草稿或手動標記", gmail_draft_id=None)
    return converted


# ── CLI ──────────────────────────────────────────────────


def main() -> None:
    p = argparse.ArgumentParser(description="官網直投管線")
    p.add_argument("--job-id", type=int, help="單筆全管線")
    p.add_argument("--backfill", action="store_true", help="常駐掃描一輪")
    p.add_argument("--limit", type=int, default=BATCH_LIMIT, help="backfill 本輪上限")
    p.add_argument("--dry-run", action="store_true",
                   help="只探測（不寫 DB / 不呼叫 LLM / 不通知）")
    p.add_argument("--create-draft", type=int, metavar="JOB_ID",
                   help="已確認 email → 建 Gmail 草稿（永不 send）")
    p.add_argument("--check-sent", action="store_true", help="偵測已寄出 → 標 applied")
    args = p.parse_args()

    db.init_db()
    if args.job_id:
        print(process_one(args.job_id, dry_run=args.dry_run))
    elif args.backfill:
        backfill(args.limit, dry_run=args.dry_run)
    elif args.create_draft:
        create_gmail_draft(args.create_draft)
    elif args.check_sent:
        n = check_sent()
        print(f"寄出偵測: {n} 筆轉 applied")
    else:
        p.print_help()


if __name__ == "__main__":
    main()
