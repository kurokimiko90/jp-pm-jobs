"""Mock interview — LLM 扮演面試官。

用法:
    python3 -m interview.mock --mode behavioral --minutes 45
    python3 -m interview.mock --mode product-sense --company mercari --lang ja
    python3 -m interview.mock --mode case --minutes 30 --difficulty senior

流程：
    1. LLM 扮演 hiring manager (可指定公司)
    2. 一題一題問，等用戶 typing 回答（按兩次 Enter 結束輸入）
    3. LLM 給 follow-up
    4. 結束 → feedback (clarity / structure / specifics / depth / Japan etiquette)
    5. log 寫到 interview/mocks/{date}-{mode}.md
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from . import _llm

MOCKS_DIR = Path(__file__).parent / "mocks"

SYSTEM_TEMPLATES = {
    "behavioral": """\
You are a senior hiring manager at {company} conducting a {minutes}-minute behavioral interview \
for a Senior Product Manager position. Ask 5-7 STAR-format questions across leadership, conflict, \
failure, mentorship, and decision-making. After each candidate answer, give one tight follow-up \
that probes specifics (numbers, who else was involved, what changed afterward). \
Language: {lang_instruction}. Do not give feedback during the interview — save it for the end.
""",
    "product-sense": """\
You are a senior product leader at {company} conducting a {minutes}-minute product sense interview \
for a Senior PM. Start with one major design / improvement question (e.g., "How would you improve X?"), \
push 3-5 layers deep with follow-ups about users, metrics, tradeoffs, and edge cases. \
Then ask one quick fire question. Language: {lang_instruction}. \
Do not give feedback mid-interview.
""",
    "case": """\
You are a strategy partner at {company} interviewing a Senior PM candidate. Present one strategic \
case (market entry, prioritization, build/buy/partner, or competitive response). \
Push the candidate to structure their approach, quantify assumptions, and recommend a clear action. \
{minutes} minutes. Language: {lang_instruction}.
""",
}

FEEDBACK_TEMPLATE = """\
The following is a mock interview transcript. Provide structured feedback on the candidate:

1. **Clarity** — was the structure easy to follow? (1-5 + brief)
2. **Structure** — did they use a recognizable framework? (1-5 + brief)
3. **Specifics** — were claims backed by numbers, names, dates? (1-5 + brief)
4. **Depth** — did follow-ups reveal solid thinking or shallow recall? (1-5 + brief)
5. **Japan etiquette** (skip if lang=en) — keigo accuracy, humility, framing of retirement/transfer reasons? (1-5 + brief)
6. **Top 3 things to improve before the real thing**

Be honest, not encouraging. The candidate needs to know where the cracks are.

Transcript:
{transcript}
"""


def multi_line_input(prompt: str = "→ ") -> str:
    print(prompt, end="", flush=True)
    lines: list[str] = []
    blank_count = 0
    while True:
        try:
            line = input()
        except EOFError:
            break
        if not line.strip():
            blank_count += 1
            if blank_count >= 1 and lines:
                break
        else:
            blank_count = 0
            lines.append(line)
    return "\n".join(lines).strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=list(SYSTEM_TEMPLATES.keys()), required=True)
    parser.add_argument("--minutes", type=int, default=45)
    parser.add_argument("--company", default="a top Japanese SaaS company")
    parser.add_argument("--lang", choices=["en", "ja"], default="en")
    parser.add_argument("--difficulty", choices=["mid", "senior", "principal"], default="senior")
    args = parser.parse_args()

    MOCKS_DIR.mkdir(exist_ok=True)
    lang_inst = "English" if args.lang == "en" else "Japanese (business 敬語)"
    system = SYSTEM_TEMPLATES[args.mode].format(
        company=args.company, minutes=args.minutes, lang_instruction=lang_inst
    )

    print(f"\n=== Mock Interview: {args.mode} ({args.lang}) @ {args.company} ===")
    print(f"預計 {args.minutes} 分鐘。回答後按兩次 Enter 提交。Ctrl-C 中斷。\n")

    history: list[dict] = []
    # 開場
    first_turn = _llm.chat_turn(history, "Begin the interview now.", system=system)
    print(f"\n[Interviewer]\n{first_turn}\n")
    history.append({"role": "assistant", "content": first_turn})

    transcript_lines = [f"# Mock Interview — {args.mode} ({args.lang})",
                        f"Company: {args.company}",
                        f"Date: {datetime.now().isoformat(timespec='seconds')}",
                        "",
                        f"## Interviewer (opening)\n{first_turn}\n"]

    turn = 0
    try:
        while turn < 12:  # safety cap
            user_input = multi_line_input("[You] (按兩次 Enter 結束) → ")
            if not user_input:
                continue
            if user_input.lower() in ("/end", "/quit", "/done"):
                break
            history.append({"role": "user", "content": user_input})
            transcript_lines.append(f"## You\n{user_input}\n")

            reply = _llm.chat_turn(history, "(continue)", system=system)
            history.append({"role": "assistant", "content": reply})
            transcript_lines.append(f"## Interviewer\n{reply}\n")
            print(f"\n[Interviewer]\n{reply}\n")

            if any(end in reply.lower() for end in ["that's all", "thank you for your time", "面接は以上"]):
                break
            turn += 1
    except KeyboardInterrupt:
        print("\n\n中斷します。")

    # Feedback
    print("\n=== フィードバック生成中 ===\n")
    transcript = "\n".join(transcript_lines)
    feedback_prompt = FEEDBACK_TEMPLATE.format(transcript=transcript)
    try:
        feedback = _llm.call(feedback_prompt, timeout=180)
    except RuntimeError as e:
        feedback = f"(feedback 失敗: {e})"
    print(feedback)

    # Save log
    today = datetime.now().strftime("%Y-%m-%d-%H%M")
    log_path = MOCKS_DIR / f"{today}-{args.mode}-{args.company.replace(' ', '_')}.md"
    log_path.write_text(transcript + "\n\n## Feedback\n\n" + feedback, encoding="utf-8")
    print(f"\n✓ log: {log_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
