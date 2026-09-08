#!/usr/bin/env python3
"""Deterministic safety gate for achievement-reconstruction interview QA.

This script does not call an LLM. It checks:
- required fact/hypothesis/answer/follow-up structure
- contiguous Q numbering and minimum follow-up count
- unsupported Arabic numbers against authoritative evidence files
- risky metrics that lack an explicit verification marker
- overly long spoken answers and common ownership overclaims

Usage:
    python3 .agents/skills/interview-qa-deepdive/scripts/audit_qa.py \
      --qa output/prep/2615_SepteniJapan/03_interview_qa.md \
      --evidence data/candidate_profile.yaml \
      --evidence resume/jp/data.yaml
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path


DEFAULT_SECTION = "職務経歴の成果を反推"
Q_RE = re.compile(r"^###\s+Q(\d+)\.\s*(.+?)\s*$", re.MULTILINE)
H2_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
NUMBER_RE = re.compile(r"(?<![A-Za-z])\d+(?:,\d{3})*(?:\.\d+)?")
FOLLOWUP_RE = re.compile(r"^-\s+\*\*.+?(?:\?|？)\*\*\s*$", re.MULTILINE)
REQUIRED_LABELS = (
    "**確認済み事実**",
    "**再構成仮説**",
    "**回答（",
    "**深掘り質問と回答**",
)
HYPOTHESIS_MARKERS = ("仮説", "可能性", "考えられ", "推測", "要確認", "未確認")
METRIC_MARKERS = ("倍", "％", "%", "分の", "→", "削減", "短縮", "半分")
OWNERSHIP_PATTERNS = (
    re.compile(r"私一人(?:で|の)"),
    re.compile(r"私が.{0,30}(?:全て|すべて|単独で).{0,20}(?:達成|実現|獲得)"),
)


@dataclass(frozen=True)
class Finding:
    level: str
    code: str
    message: str
    qid: int | None = None
    line: int | None = None


def normalize_number(token: str) -> str:
    return token.replace(",", "")


def extract_section(text: str, heading_fragment: str = DEFAULT_SECTION) -> tuple[str, int]:
    """Return the matching H2 section and its source offset."""

    headings = list(H2_RE.finditer(text))
    for index, heading in enumerate(headings):
        if heading_fragment not in heading.group(1):
            continue
        start = heading.end()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        return text[start:end], start
    raise ValueError(f"H2 section containing {heading_fragment!r} was not found")


def split_questions(section: str) -> list[tuple[int, str, int]]:
    marks = list(Q_RE.finditer(section))
    rows: list[tuple[int, str, int]] = []
    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(section)
        rows.append((int(mark.group(1)), section[mark.start():end].strip(), mark.start()))
    return rows


def evidence_numbers(paths: list[Path]) -> set[str]:
    numbers: set[str] = set()
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(f"evidence file not found: {path}")
        for match in NUMBER_RE.finditer(path.read_text(encoding="utf-8")):
            numbers.add(normalize_number(match.group()))
    return numbers


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _answer_text(block: str) -> str:
    start = re.search(r"\*\*回答（.*?\）\*\*：?", block)
    if not start:
        return ""
    end = block.find("**深掘り質問と回答**", start.end())
    if end < 0:
        end = len(block)
    answer = block[start.end():end]
    answer = re.sub(r"\*\*.+?\*\*", "", answer)
    return re.sub(r"\s+", "", answer)


def _unsupported_numbers(
    block: str,
    known: set[str],
    qid: int,
    absolute_offset: int,
    full_text: str,
) -> list[Finding]:
    findings: list[Finding] = []
    # Q IDs and oral-duration labels are structure, not career claims.
    scan = re.sub(r"^(###\s+Q)\d+", r"\1", block, count=1)
    for match in NUMBER_RE.finditer(scan):
        token = match.group()
        normalized = normalize_number(token)
        suffix = scan[match.end():match.end() + 5]
        prefix = scan[max(0, match.start() - 5):match.start()]
        line_text = scan[scan.rfind("\n", 0, match.start()) + 1:
                         scan.find("\n", match.end()) if scan.find("\n", match.end()) >= 0 else len(scan)]
        if normalized in {"30", "45", "60"} and "秒" in suffix:
            continue
        if re.search(r"^\s*\d+[.)、]\s", line_text):
            continue
        if normalized in known:
            continue
        local_line = _line_number(scan, match.start())
        absolute_line = _line_number(full_text, absolute_offset) + local_line - 1
        context = re.sub(r"\s+", " ", line_text).strip()
        findings.append(Finding(
            "error",
            "unsupported-number",
            f"number {token!r} is absent from evidence: {context}",
            qid,
            absolute_line,
        ))
    return findings


def audit_text(
    qa_text: str,
    known_numbers: set[str],
    heading_fragment: str = DEFAULT_SECTION,
    min_drills: int = 3,
    max_answer_chars: int = 420,
) -> list[Finding]:
    findings: list[Finding] = []
    try:
        section, section_offset = extract_section(qa_text, heading_fragment)
    except ValueError as exc:
        return [Finding("error", "missing-section", str(exc))]

    questions = split_questions(section)
    if not questions:
        return [Finding("error", "missing-questions", "no numbered Q blocks in target section")]

    qids = [qid for qid, _, _ in questions]
    expected = list(range(min(qids), max(qids) + 1))
    if qids != expected:
        findings.append(Finding(
            "error",
            "question-numbering",
            f"question IDs must be unique and contiguous: actual={qids}, expected={expected}",
        ))

    for qid, block, local_offset in questions:
        absolute_offset = section_offset + local_offset
        for label in REQUIRED_LABELS:
            if label not in block:
                findings.append(Finding(
                    "error",
                    "missing-label",
                    f"required label missing: {label}",
                    qid,
                    _line_number(qa_text, absolute_offset),
                ))

        hypothesis = ""
        if "**再構成仮説**" in block:
            hypothesis = block.split("**再構成仮説**", 1)[1]
            hypothesis = hypothesis.split("**回答（", 1)[0]
        if hypothesis and not any(marker in hypothesis for marker in HYPOTHESIS_MARKERS):
            findings.append(Finding(
                "warning",
                "uncaveated-hypothesis",
                "reconstruction hypothesis lacks uncertainty language",
                qid,
                _line_number(qa_text, absolute_offset),
            ))

        deep = block.split("**深掘り質問と回答**", 1)[1] if "**深掘り質問と回答**" in block else ""
        drill_count = len(FOLLOWUP_RE.findall(deep))
        if drill_count < min_drills:
            findings.append(Finding(
                "error",
                "too-few-followups",
                f"found {drill_count} follow-ups; require at least {min_drills}",
                qid,
                _line_number(qa_text, absolute_offset),
            ))

        answer = _answer_text(block)
        if answer and len(answer) > max_answer_chars:
            findings.append(Finding(
                "warning",
                "long-answer",
                f"spoken answer has {len(answer)} characters; target <= {max_answer_chars}",
                qid,
                _line_number(qa_text, absolute_offset),
            ))

        if any(marker in block for marker in METRIC_MARKERS) and "{{要確認" not in block:
            findings.append(Finding(
                "warning",
                "metric-without-verification",
                "high-risk metric appears without a {{要確認：...}} item",
                qid,
                _line_number(qa_text, absolute_offset),
            ))

        for pattern in OWNERSHIP_PATTERNS:
            if pattern.search(block):
                if "私一人の成果とは言わ" in block:
                    continue
                findings.append(Finding(
                    "warning",
                    "ownership-overclaim",
                    f"possible sole-ownership overclaim matched {pattern.pattern!r}",
                    qid,
                    _line_number(qa_text, absolute_offset),
                ))

        findings.extend(_unsupported_numbers(
            block,
            known_numbers,
            qid,
            absolute_offset,
            qa_text,
        ))

    return findings


def render_human(findings: list[Finding]) -> str:
    errors = [item for item in findings if item.level == "error"]
    warnings = [item for item in findings if item.level == "warning"]
    status = "PASS" if not errors else "REVIEW"
    lines = [
        f"{status}: errors={len(errors)} warnings={len(warnings)}",
    ]
    for item in findings:
        where = ""
        if item.qid is not None:
            where += f" Q{item.qid}"
        if item.line is not None:
            where += f" line {item.line}"
        lines.append(f"- {item.level.upper()} [{item.code}]{where}: {item.message}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit reconstructed interview QA without an LLM")
    parser.add_argument("--qa", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, action="append", required=True)
    parser.add_argument("--section", default=DEFAULT_SECTION)
    parser.add_argument("--min-drills", type=int, default=3)
    parser.add_argument("--max-answer-chars", type=int, default=420)
    parser.add_argument("--report", type=Path, default=None, help="optional JSON report path")
    args = parser.parse_args()

    if not args.qa.is_file():
        parser.error(f"QA file not found: {args.qa}")
    try:
        known = evidence_numbers(args.evidence)
    except FileNotFoundError as exc:
        parser.error(str(exc))

    findings = audit_text(
        args.qa.read_text(encoding="utf-8"),
        known,
        heading_fragment=args.section,
        min_drills=max(1, args.min_drills),
        max_answer_chars=max(120, args.max_answer_chars),
    )
    print(render_human(findings))

    if args.report:
        payload = {
            "qa": str(args.qa),
            "evidence": [str(path) for path in args.evidence],
            "section": args.section,
            "status": "REVIEW" if any(x.level == "error" for x in findings) else "PASS",
            "findings": [asdict(item) for item in findings],
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return 2 if any(item.level == "error" for item in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
