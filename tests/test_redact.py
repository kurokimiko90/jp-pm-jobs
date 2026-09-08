"""tools.redact（取引先ブランド名の遮蔽）— 零網路、零 LLM。

config を差し替えて検証するので、本人環境の config/redaction.yaml には依存しない。
"""

import os
import tempfile
import unittest
from pathlib import Path

RULES = """
drop_parenthetical: true
terms:
  - match: ["V POINT", "Ponta", "d ポイント", "KIPS"]
    replace: "大手共通ポイント"
  - match: ["NTT", "KDDI"]
    replace: "通信キャリア"
"""


class RedactTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        Path(self.tmp.name, "redaction.yaml").write_text(RULES, encoding="utf-8")
        self._old = os.environ.get("APP_CONFIG_DIR")
        os.environ["APP_CONFIG_DIR"] = self.tmp.name
        self.addCleanup(self._restore)

        import importlib
        from tools import app_config, redact
        importlib.reload(app_config)   # CONFIG_DIR はモジュール読み込み時に確定するため
        self.redact = importlib.reload(redact)

    def _restore(self):
        import importlib
        from tools import app_config, redact
        if self._old is None:
            os.environ.pop("APP_CONFIG_DIR", None)
        else:
            os.environ["APP_CONFIG_DIR"] = self._old
        importlib.reload(app_config)
        importlib.reload(redact)


class TestRedact(RedactTestBase):
    def test_terms_generalised(self):
        clean, hits = self.redact.redact("V POINT の接続では NTT・KDDI と調整した。")
        self.assertNotIn("V POINT", clean)
        self.assertNotIn("NTT", clean)
        self.assertNotIn("KDDI", clean)
        self.assertIn("大手共通ポイント", clean)
        self.assertIn("通信キャリア", clean)
        self.assertEqual(set(hits), {"V POINT", "NTT", "KDDI"})

    def test_parenthetical_enumeration_dropped(self):
        clean, _ = self.redact.redact(
            "8+ のポイントブランド（V POINT・Ponta・d ポイント・KIPS 等）を統合。")
        self.assertEqual(clean, "8+ のポイントブランドを統合。")

    def test_duplicate_replacements_collapsed(self):
        clean, _ = self.redact.redact("V POINT と Ponta と KIPS を接続")
        self.assertEqual(clean.count("大手共通ポイント"), 1)

    def test_ascii_word_boundary(self):
        """短い ASCII 語が別単語の一部を壊さない。"""
        clean, hits = self.redact.redact("NTTドコモではなく NTTData でもない NTT-X")
        self.assertNotIn("NTTData", hits)  # 語境界で守られる
        self.assertIn("NTTData", clean)

    def test_clean_text_untouched(self):
        text = "StarPay Biz の PdM として要件定義から本番リリースまで推進。"
        clean, hits = self.redact.redact(text)
        self.assertEqual(clean, text)   # 自社プロダクト名は消さない
        self.assertEqual(hits, [])

    def test_scan_finds_残存(self):
        self.assertEqual(self.redact.scan("Ponta 連携"), ["Ponta"])
        self.assertEqual(self.redact.scan("大手共通ポイント連携"), [])


class TestNoConfigIsNoop(unittest.TestCase):
    """config/redaction.yaml が無い環境では完全な no-op（開源既定）。"""

    def test_noop_without_config(self):
        import importlib
        with tempfile.TemporaryDirectory() as tmp:
            old = os.environ.get("APP_CONFIG_DIR")
            os.environ["APP_CONFIG_DIR"] = tmp
            try:
                from tools import app_config, redact
                importlib.reload(app_config)
                redact = importlib.reload(redact)
                text = "V POINT と NTT はそのまま残る"
                self.assertEqual(redact.redact(text), (text, []))
                self.assertEqual(redact.scan(text), [])
            finally:
                if old is None:
                    os.environ.pop("APP_CONFIG_DIR", None)
                else:
                    os.environ["APP_CONFIG_DIR"] = old
                from tools import app_config as ac, redact as rd
                importlib.reload(ac)
                importlib.reload(rd)


if __name__ == "__main__":
    unittest.main()
