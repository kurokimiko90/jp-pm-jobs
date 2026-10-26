from pathlib import Path

from tools.knowledge_sources import MAX_CHARS_PER_SOURCE, KnowledgeSource, knowledge_block


def test_empty_when_no_sources():
    assert knowledge_block([]) == ""


def test_missing_file_is_skipped(tmp_path: Path):
    assert knowledge_block([KnowledgeSource("x", tmp_path / "none.md")]) == ""


def test_block_contains_label_and_body(tmp_path: Path):
    f = tmp_path / "kb.md"
    f.write_text("3週間でリリース", encoding="utf-8")
    block = knowledge_block([KnowledgeSource("チケット", f)])
    assert "## チケット" in block and "3週間でリリース" in block
    assert block.startswith("# 応募者本人の実践記録")


def test_long_file_is_truncated(tmp_path: Path):
    f = tmp_path / "kb.md"
    f.write_text("あ" * (MAX_CHARS_PER_SOURCE + 100), encoding="utf-8")
    block = knowledge_block([KnowledgeSource("長文", f)])
    assert "以下省略" in block
    assert block.count("あ") == MAX_CHARS_PER_SOURCE


def test_inner_headings_are_demoted(tmp_path: Path):
    f = tmp_path / "kb.md"
    f.write_text("# 題\n## 背景\n本文", encoding="utf-8")
    block = knowledge_block([KnowledgeSource("案件", f)])
    assert "\n### 題\n#### 背景\n" in block
