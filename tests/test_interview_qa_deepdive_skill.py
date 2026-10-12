"""Tests for the repo-local interview-qa-deepdive deterministic audit."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".agents/skills/interview-qa-deepdive/scripts/audit_qa.py"
SPEC = importlib.util.spec_from_file_location("interview_qa_deepdive_audit", SCRIPT)
assert SPEC and SPEC.loader
AUDIT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = AUDIT
SPEC.loader.exec_module(AUDIT)


QA = """# QA

## 職務経歴の成果を反推した再構成QA

### Q8. 8社への展開をどう実現しましたか

**確認済み事実**：8社への展開を担当しました。

**再構成仮説**：共通工程を整理した可能性があります。

**回答（60秒以内）**：共通工程を整理し、展開を進めました。

**深掘り質問と回答**

- **根因は何ですか？**
  確認の重複です。
- **本人の貢献は何ですか？**
  判断項目を整理しました。
- **どう測りましたか？**
  {{要確認：対象期間}}
"""


def test_compliant_section_passes():
    findings = AUDIT.audit_text(QA, {"8"})
    assert not [item for item in findings if item.level == "error"]


def test_unsupported_number_is_an_error():
    findings = AUDIT.audit_text(QA.replace("8社への展開を担当", "99社への展開を担当"), {"8"})
    assert any(item.code == "unsupported-number" and item.level == "error" for item in findings)


def test_missing_followups_and_labels_are_errors():
    broken = QA.replace("**確認済み事実**", "**事実**").replace(
        "- **どう測りましたか？**\n  {{要確認：対象期間}}",
        "",
    )
    findings = AUDIT.audit_text(broken, {"8"})
    codes = {item.code for item in findings if item.level == "error"}
    assert "missing-label" in codes
    assert "too-few-followups" in codes


def test_team_credit_disclaimer_is_not_an_overclaim():
    text = QA.replace(
        "**回答（60秒以内）**：共通工程を整理し、展開を進めました。",
        "**回答（60秒以内）**：これは私一人の成果とは言わず、チームの成果として説明します。",
    )
    findings = AUDIT.audit_text(text, {"8"})
    assert not [item for item in findings if item.code == "ownership-overclaim"]
