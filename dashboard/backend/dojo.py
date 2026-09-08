"""めんせつ道場：QA md 解析成題卡 + 練習 session 與自評紀錄。"""
import random
import re

from fastapi import APIRouter, Body, HTTPException

from paths import PREP_DIR, QBANK_DIR
from practice_db import execute, query

router = APIRouter(prefix="/api/dojo")

Q_RE = re.compile(r"^#{2,3} (Q[\d\-]+)[\.、]\s*(.+)$")
SECTION_RE = re.compile(r"^## (?!Q[\d\-]+[\.、])(.+)$")


def sync_cards() -> int:
    """掃 output/prep/*/03_interview_qa.md → upsert qa_cards。回傳卡片總數。"""
    for p in sorted(PREP_DIR.iterdir()):
        qa = p / "03_interview_qa.md"
        if not p.is_dir() or not qa.exists():
            continue
        company = p.name.split("_", 1)[-1] if "_" in p.name else p.name
        section, qno, question, buf = None, None, None, []

        def flush():
            if question:
                execute(
                    "INSERT INTO qa_cards (prep_dir, company, section, qno, question, answer) "
                    "VALUES (?,?,?,?,?,?) ON CONFLICT(prep_dir, qno, question) "
                    "DO UPDATE SET answer=excluded.answer, section=excluded.section",
                    (p.name, company, section, qno, question, "\n".join(buf).strip()))

        for line in qa.read_text(encoding="utf-8").splitlines():
            if m := SECTION_RE.match(line):
                flush(); question, buf = None, []
                section = m.group(1).strip()
            elif m := Q_RE.match(line):
                flush()
                qno, question, buf = m.group(1), m.group(2).strip(), []
            elif question is not None:
                buf.append(line)
        flush()
    # 基礎練習題卡（interview/question-bank/dojo_base_*.md）
    base_dir = QBANK_DIR
    for md in sorted(base_dir.glob("dojo_base_*.md")):
        lang_tag = md.stem.replace("dojo_base_", "").upper()
        company_label = f"共通({lang_tag})"
        section, qno, question, buf = None, None, None, []

        def flush_base():
            if question:
                execute(
                    "INSERT INTO qa_cards (prep_dir, company, section, qno, question, answer) "
                    "VALUES (?,?,?,?,?,?) ON CONFLICT(prep_dir, qno, question) "
                    "DO UPDATE SET answer=excluded.answer, section=excluded.section",
                    (md.name, company_label, section, qno, question, "\n".join(buf).strip()))

        for line in md.read_text(encoding="utf-8").splitlines():
            if m := SECTION_RE.match(line):
                flush_base(); question, buf = None, []
                section = m.group(1).strip()
            elif m := Q_RE.match(line):
                flush_base()
                qno, question, buf = m.group(1), m.group(2).strip(), []
            elif question is not None:
                buf.append(line)
        flush_base()

    return query("SELECT COUNT(*) n FROM qa_cards")[0]["n"]


@router.get("/cards")
def cards(company: str = ""):
    n = sync_cards()
    where, params = "", ()
    if company:
        where, params = "WHERE c.company = ?", (company,)
    rows = query(f"""
        SELECT c.*, COUNT(r.id) reviews,
               SUM(r.grade='o') ok, SUM(r.grade='d') delta, SUM(r.grade='x') ng,
               MAX(r.reviewed_at) last_reviewed,
               (SELECT grade FROM reviews WHERE card_id=c.id ORDER BY reviewed_at DESC LIMIT 1) last_grade
        FROM qa_cards c LEFT JOIN reviews r ON r.card_id = c.id
        {where} GROUP BY c.id ORDER BY c.prep_dir, c.id""", params)
    companies = query("SELECT company, COUNT(*) n FROM qa_cards GROUP BY company")
    return {"total": n, "companies": companies, "cards": rows}


@router.get("/session")
def session(mode: str = "flash", company: str = "", n: int = 8):
    sync_cards()
    where, params = "", ()
    if company:
        where, params = "WHERE c.company = ?", (company,)
    rows = query(f"""
        SELECT c.id, c.company, c.section, c.qno, c.question, c.answer,
               (SELECT grade FROM reviews WHERE card_id=c.id ORDER BY reviewed_at DESC LIMIT 1) last_grade,
               (SELECT COUNT(*) FROM reviews WHERE card_id=c.id) n_reviews
        FROM qa_cards c {where}""", params)
    if not rows:
        raise HTTPException(404, "No cards available — run prep to generate 03_interview_qa.md")
    if mode == "weak":
        rows = [r for r in rows if r["last_grade"] in ("d", "x")]
        if not rows:
            return {"mode": mode, "cards": [], "note": "沒有弱點題 — 全部 ○"}
        rows.sort(key=lambda r: (r["last_grade"] != "x", r["n_reviews"]))
    elif mode == "mock":
        random.shuffle(rows)
        rows = rows[:n]
    else:  # flash：×△ 優先 → 沒練過的 → 練過 ○ 的
        prio = {"x": 0, "d": 1, None: 2, "o": 3}
        rows.sort(key=lambda r: (prio.get(r["last_grade"], 2), r["n_reviews"]))
        rows = rows[:n]
    return {"mode": mode, "cards": rows}


@router.post("/review")
def review(body: dict = Body(...)):
    card_id, grade = body.get("card_id"), body.get("grade")
    if grade not in ("o", "d", "x") or not card_id:
        raise HTTPException(400, "card_id + grade(o/d/x) 必填")
    execute("INSERT INTO reviews (card_id, grade, mode, duration_sec) VALUES (?,?,?,?)",
            (card_id, grade, body.get("mode", "flash"), body.get("duration_sec")))
    return {"ok": True}


@router.get("/stats")
def stats():
    days = [r["d"] for r in query(
        "SELECT DISTINCT date(reviewed_at) d FROM reviews ORDER BY d DESC")]
    streak = 0
    if days:
        from datetime import date, timedelta
        cur = date.today()
        if days[0] != cur.isoformat():  # 今天還沒練，從昨天起算
            cur -= timedelta(days=1)
        for d in days:
            if d == cur.isoformat():
                streak += 1
                cur -= timedelta(days=1)
            else:
                break
    total = query("SELECT COUNT(*) n, SUM(grade='o') ok, SUM(grade='d') d, SUM(grade='x') x FROM reviews")[0]
    weak = query("""
        SELECT c.id, c.company, c.qno, c.question FROM qa_cards c
        WHERE (SELECT grade FROM reviews WHERE card_id=c.id ORDER BY reviewed_at DESC LIMIT 1) IN ('d','x')
        ORDER BY c.company, c.id""")
    today_n = query("SELECT COUNT(*) n FROM reviews WHERE date(reviewed_at)=date('now','localtime')")[0]["n"]
    return {"streak": streak, "today": today_n, "total": total, "weak": weak}
