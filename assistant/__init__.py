"""AI 職涯助手 — 對話式問答，事實全部來自本專案資料庫（零編造）。

    from assistant.chat import answer
    reply = answer("這週有哪些職缺要跟進？", channel="web")

子模組：
    context.py  — 白名單資料檢索（jobs/applications/gap_batches/followups）
    chat.py     — 問答主邏輯（PII 去識別化 + 取引先遮蔽 + 引用格式）
    store.py    — 對話紀錄（data/practice.sqlite 的 assistant_turns 表）
    digest.py   — 每日/每週總結（python3 -m assistant.digest daily|weekly）
    bot.py      — 專用 Telegram daemon（獨立 bot token，見 CLAUDE.md）
"""
