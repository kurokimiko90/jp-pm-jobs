from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class NarrationSection:
    id: str
    title: str
    text: str
    source_file: str
    category: str
    ordinal: int

    def to_dict(self) -> dict:
        data = asdict(self)
        data["text_hash"] = hashlib.sha256(self.text.encode("utf-8")).hexdigest()
        return data


_HEADING_RE = re.compile(r"^(##|###)\s+(.+?)\s*$", re.MULTILINE)


def _normalize_text(text: str) -> str:
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = text.replace("**", "")
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", text)
    text = re.sub(r"^\s*[-*]\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+\.\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*>\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\{\{.*?\}\}", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line and line != "-").strip()


def _extract_heading_block(text: str, heading: str) -> str:
    pattern = re.compile(rf"^###\s+{re.escape(heading)}\s*$", re.MULTILINE)
    match = pattern.search(text)
    if not match:
        return ""
    start = match.end()
    next_match = re.compile(r"^###\s+", re.MULTILINE).search(text, start)
    end = next_match.start() if next_match else len(text)
    return text[start:end].strip()


def _extract_section_after(text: str, heading: str) -> str:
    pattern = re.compile(rf"^##\s+{re.escape(heading)}(?:\s*（.*?）)?\s*$", re.MULTILINE)
    match = pattern.search(text)
    if not match:
        return ""
    start = match.end()
    next_match = re.compile(r"^##\s+", re.MULTILINE).search(text, start)
    end = next_match.start() if next_match else len(text)
    return text[start:end].strip()


def _split_subsections(text: str) -> list[tuple[str, str]]:
    matches = list(re.finditer(r"^###\s+(.+?)\s*$", text, re.MULTILINE))
    if not matches:
        return []
    blocks: list[tuple[str, str]] = []
    for idx, match in enumerate(matches):
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        blocks.append((match.group(1).strip(), text[start:end].strip()))
    return blocks


def _build_company_summary(path: Path) -> list[NarrationSection]:
    text = path.read_text(encoding="utf-8")
    title = text.splitlines()[0].lstrip("#").strip() if text else path.stem
    blocks = [
        _extract_heading_block(text, "事業内容"),
        _extract_heading_block(text, "カルチャー観察"),
    ]
    summary = _normalize_text("\n\n".join(block for block in blocks if block))
    if not summary:
        return []
    if len(summary) > 700:
        summary = summary[:700].rsplit("。", 1)[0].strip() + "。"
    return [NarrationSection(
        id="company-summary",
        title=f"{title} 会社摘要",
        text=summary,
        source_file=path.name,
        category="company_summary",
        ordinal=1,
    )]


def _build_shibou_oral(path: Path) -> list[NarrationSection]:
    text = path.read_text(encoding="utf-8")
    oral = _extract_section_after(text, "5. 面接口頭版")
    blocks = _split_subsections(oral)
    sections: list[NarrationSection] = []
    for idx, (title, body) in enumerate(blocks, start=1):
        normalized = _normalize_text(body)
        if not normalized:
            continue
        sections.append(NarrationSection(
            id=f"oral-{idx:02d}",
            title=title,
            text=normalized,
            source_file=path.name,
            category="oral_answer",
            ordinal=idx,
        ))
    return sections


def _build_interview_qa(path: Path) -> list[NarrationSection]:
    text = path.read_text(encoding="utf-8")
    blocks = _split_subsections(text)
    sections: list[NarrationSection] = []
    for idx, (title, body) in enumerate(blocks, start=1):
        body = re.split(r"\n##\s+", body, maxsplit=1)[0]
        normalized = _normalize_text(body)
        if not normalized:
            continue
        qa_title = title
        narration = _normalize_text(f"{qa_title}\n{normalized}")
        sections.append(NarrationSection(
            id=f"qa-{idx:02d}",
            title=qa_title,
            text=narration,
            source_file=path.name,
            category="interview_qa",
            ordinal=idx,
        ))
    return sections


def collect_sections(kind: str, pack_dir: Path, preset: str = "core") -> list[NarrationSection]:
    if kind not in {"apply", "prep"}:
        raise ValueError(f"unsupported kind: {kind}")

    sections: list[NarrationSection] = []
    if kind == "apply":
        brief = pack_dir / "01_company_brief.md"
        if brief.exists():
            sections.extend(_build_company_summary(brief))
        shibou = pack_dir / "03_shibou_doki.md"
        if shibou.exists():
            sections.extend(_build_shibou_oral(shibou))
    else:
        for name in ("03_interview_qa.md", "01_interview_qa.md"):
            qa = pack_dir / name
            if qa.exists():
                sections.extend(_build_interview_qa(qa))
                break
        for name in ("02_shibou_doki.md", "03_shibou_doki.md"):
            shibou = pack_dir / name
            if shibou.exists():
                sections.extend(_build_shibou_oral(shibou))
                break
        brief = pack_dir / "01_company_brief.md"
        if brief.exists():
            sections.extend(_build_company_summary(brief))

    if preset == "core":
        return sections
    raise ValueError(f"unsupported preset: {preset}")
