"""tools.company_contact（官網直投探測）的迴歸測試 — 零網路、零 LLM。

鎖死三塊規則行為：
  1. email 抽取與信心分（採用専用 > 一般 > 排除項）
  2. 日本職缺判定（location 正向 / 海外排除 / 薪資髒資料 fallback JD CJK）
  3. 採用頁連結與應募フォーム偵測（中途優先、純新卒排除、求人媒體排除）
"""

import unittest
from pathlib import Path

from tools import direct_apply as da
from tools.company_contact import (
    extract_emails,
    find_careers_links,
    find_form_url,
    is_japan_job,
    is_jobboard,
    score_email,
)
from tools.direct_apply import pick_auto_draft_email


class TestEmailExtraction(unittest.TestCase):
    def test_saiyo_high_confidence(self):
        for email in ("saiyo@example.co.jp", "recruit@foo.jp", "jinji@bar.co.jp",
                      "careers@baz.io", "hr@qux.jp"):
            self.assertEqual(score_email(email), 0.9, email)

    def test_info_downgraded(self):
        self.assertEqual(score_email("info@example.co.jp"), 0.5)
        self.assertEqual(score_email("contact@example.co.jp"), 0.5)

    def test_excluded_locals(self):
        for email in ("noreply@example.co.jp", "no-reply@foo.jp",
                      "privacy@bar.jp", "webmaster@baz.jp"):
            self.assertIsNone(score_email(email), email)

    def test_asset_false_positive_excluded(self):
        self.assertIsNone(score_email("logo@2x.png"))

    def test_free_mail_downgraded(self):
        self.assertEqual(score_email("shop@gmail.com"), 0.45)

    def test_extract_sorted_and_deduped(self):
        text = ("応募は saiyo@example.co.jp まで。お問い合わせ: info@example.co.jp、"
                "再掲 saiyo@example.co.jp")
        emails = extract_emails(text, "jd")
        self.assertEqual([e["email"] for e in emails],
                         ["saiyo@example.co.jp", "info@example.co.jp"])
        self.assertEqual(emails[0]["confidence"], 0.9)
        self.assertEqual(emails[0]["source"], "jd")

    def test_placeholder_excluded(self):
        # r-agent 頁面實際出現過 abc@xxxxxx.co.jp（佔位範例）→ 必須整個排除
        for email in ("abc@xxxxxx.co.jp", "xyz@real.co.jp", "test@foo.co.jp",
                      "info@hoge.co.jp", "saiyo@sample.jp", "dummy@bar.co.jp"):
            self.assertIsNone(score_email(email), email)


class TestAutoDraftGate(unittest.TestCase):
    """pick_auto_draft_email — 高信心自動建草稿、低信心留人工確認。"""

    def test_high_confidence_picked(self):
        emails = [{"email": "saiyo@foo.co.jp", "confidence": 0.9, "source": "jd"}]
        self.assertEqual(pick_auto_draft_email(emails, 0.6), "saiyo@foo.co.jp")

    def test_low_confidence_rejected(self):
        emails = [{"email": "abc@bar.co.jp", "confidence": 0.4, "source": "page"}]
        self.assertIsNone(pick_auto_draft_email(emails, 0.6))

    def test_empty_list(self):
        self.assertIsNone(pick_auto_draft_email([], 0.6))

    def test_picks_highest_confidence(self):
        emails = [{"email": "info@foo.co.jp", "confidence": 0.5, "source": "jd"},
                  {"email": "recruit@foo.co.jp", "confidence": 0.9, "source": "jd"}]
        self.assertEqual(pick_auto_draft_email(emails, 0.6), "recruit@foo.co.jp")


class TestJapanJobFilter(unittest.TestCase):
    def test_japanese_locations(self):
        for loc in ("東京都 港区・一部在宅", "Tokyo, Japan (On-site)", "大阪府 大阪市",
                    "神奈川県 川崎市", "フルリモート", "東京都, 神奈川県, 千葉県"):
            self.assertTrue(is_japan_job({"location": loc, "raw_jd": ""}), loc)

    def test_overseas_locations(self):
        for loc in ("Berlin Office", "Toronto", "San Francisco, CA"):
            self.assertFalse(is_japan_job({"location": loc, "raw_jd": ""}), loc)

    def test_salary_string_falls_back_to_jd_cjk(self):
        jd_jp = "プロダクトマネージャーとして生成AIプロダクトの企画・開発推進を担当。" * 3
        self.assertTrue(is_japan_job({"location": "800万～2000万", "raw_jd": jd_jp}))

    def test_empty_location_english_jd_rejected(self):
        self.assertFalse(is_japan_job(
            {"location": "", "raw_jd": "We are hiring a PM in our SF office." * 5}))

    def test_empty_location_japanese_jd_accepted(self):
        jd = "当社は東京のAIスタートアップです。中途採用のPMを募集しています。" * 2
        self.assertTrue(is_japan_job({"location": None, "raw_jd": jd}))


class TestCareersDetection(unittest.TestCase):
    BASE = "https://example.co.jp/"

    def test_midcareer_link_prioritised(self):
        html = (
            '<a href="/recruit/shinsotsu">新卒採用</a>'
            '<a href="/recruit/career">中途採用</a>'
            '<a href="/about">会社概要</a>'
        )
        links = find_careers_links(html, self.BASE)
        self.assertIn("https://example.co.jp/recruit/career", links)
        # 純新卒頁排除
        self.assertNotIn("https://example.co.jp/recruit/shinsotsu", links)

    def test_jobboard_links_excluded(self):
        html = '<a href="https://www.green-japan.com/job/1">求人はこちら</a>'
        self.assertEqual(find_careers_links(html, self.BASE), [])

    def test_form_host_detected(self):
        html = '<a href="https://herp.careers/v1/example/xyz">応募する</a>'
        self.assertEqual(find_form_url(html, self.BASE),
                         "https://herp.careers/v1/example/xyz")

    def test_is_jobboard(self):
        self.assertTrue(is_jobboard("https://www.green-japan.com/job/1"))
        self.assertTrue(is_jobboard("https://jp.indeed.com/viewjob?jk=x"))
        self.assertFalse(is_jobboard("https://example.co.jp/recruit"))


class TestAttachments(unittest.TestCase):
    """添付は「最新の完成版 職務経歴書」＋「志望動機 JD 特化の履歴書」の 2 通。"""

    def setUp(self):
        import tempfile
        from unittest import mock
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pack = Path(self.tmp.name) / "42_Sample"
        self.pack.mkdir()
        self.shokumu = Path(self.tmp.name) / "shokumu.pdf"
        self.shokumu.write_bytes(b"%PDF-1.4 shokumu")
        self.global_rirekisho = Path(self.tmp.name) / "rirekisho.pdf"
        self.global_rirekisho.write_bytes(b"%PDF-1.4 rirekisho")
        for target, path in (("shokumu_pdf", self.shokumu),
                             ("rirekisho_pdf", self.global_rirekisho)):
            p = mock.patch.object(da.resume_assets, target, return_value=path)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(da.resume_assets, "attachment_names",
                              return_value={"shokumu": "氏名_職務経歴書.pdf",
                                            "rirekisho": "氏名_履歴書.pdf"})
        p.start()
        self.addCleanup(p.stop)

    def _attach(self):
        from email.mime.multipart import MIMEMultipart
        mime = MIMEMultipart()
        return mime, da._attach_documents(mime, self.pack)

    def test_falls_back_to_global_rirekisho(self):
        mime, names = self._attach()
        self.assertEqual(names, ["氏名_職務経歴書.pdf", "氏名_履歴書.pdf"])
        payloads = [p.get_payload(decode=True) for p in mime.get_payload()]
        self.assertIn(b"%PDF-1.4 rirekisho", payloads)

    def test_pack_tailored_pdf_wins(self):
        (self.pack / "05_rirekisho.html").write_text("<html></html>", encoding="utf-8")
        (self.pack / "05_rirekisho.pdf").write_bytes(b"%PDF-1.4 tailored")
        mime, names = self._attach()
        payloads = [p.get_payload(decode=True) for p in mime.get_payload()]
        self.assertIn(b"%PDF-1.4 tailored", payloads)
        self.assertNotIn(b"%PDF-1.4 rirekisho", payloads)
        self.assertEqual(names[1], "氏名_履歴書.pdf")  # 添付名は慣例どおり

    def test_missing_assets_do_not_crash(self):
        self.shokumu.unlink()
        self.global_rirekisho.unlink()
        _, names = self._attach()
        self.assertEqual(names, [])

    def test_manifest_lists_both_documents(self):
        text = da.write_pack_manifest(self.pack).read_text(encoding="utf-8")
        self.assertIn("氏名_職務経歴書.pdf", text)
        self.assertIn("氏名_履歴書.pdf", text)
        self.assertIn("志望動機", text)


if __name__ == "__main__":
    unittest.main()
