"""tools.rirekisho_tailor（履歴書 志望動機の JD 特化）— 零網路、零 LLM。

鎖死四塊行為：
  1. prompt に PII（実名・連絡先・生年月日・住所）が入らない
  2. freeform-block の置換で「経験」行の原文と他セクションが壊れない
  3. LLM 出力の JSON 抽出と字数/数字接地の警告
  4. 画像の base64 内嵌と作成日の更新
"""

import unittest
from pathlib import Path

from tools import rirekisho_tailor as rt

BASE_FIXTURE = """<!DOCTYPE html>
<html lang="ja"><body>
<main>
  <div class="as-of">2026年7月25日 現在</div>
  <div class="photo-slot"><img src="assets/photo.JPG" alt="証明写真"></div>
  <div class="field-value">1980年1月1日（満46歳）</div>
  <section class="section">
    <h2 class="section-title"><span class="index">3</span><span>免許・資格</span></h2>
    <table><tbody><tr><td>N1</td></tr></tbody></table>
  </section>

  <section class="section">
    <h2 class="section-title"><span class="index">4</span><span>志望の動機など</span></h2>
    <div class="freeform-block">
      <div class="freeform-row">
        <div class="freeform-label">志望領域</div>
        <div class="freeform-text">旧・志望領域</div>
      </div>
      <div class="freeform-row">
        <div class="freeform-label">経験</div>
        <div class="freeform-text">エンジニア出身として<strong>約9年間</strong>従事。</div>
      </div>
      <div class="freeform-row">
        <div class="freeform-label">アピール</div>
        <div class="freeform-text">旧・アピール</div>
      </div>
    </div>
  </section>

  <section class="section">
    <h2 class="section-title"><span class="index">5</span><span>本人希望記入欄</span></h2>
    <div class="conditions"><div class="condition-value">960万円</div></div>
  </section>
</main>
</body></html>
"""

JOB = {
    "id": 1,
    "company": "株式会社サンプル",
    "title": "プロダクトマネージャー",
    "location": "東京都",
    "tier": "ai_startup",
    "salary_min": 900,
    "salary_max": 1200,
    "raw_jd": "LLM 基盤プロダクトの PdM を募集。要件: B2B SaaS の 0→1 経験。",
}


class TestPromptPII(unittest.TestCase):
    """prompt は JD + 会社事実 + 去識別化 profile のみ（基底 HTML は入らない）。"""

    def test_prompt_has_no_pii(self):
        deid = "positioning:\n  headline: PdM\nskills:\n  - Python\n"
        prompt = rt.build_prompt(JOB, "資金調達: シリーズB", deid)
        from tools.deid import load_resume_contact
        rc = load_resume_contact()
        for key in ("name_ja", "email", "phone"):
            val = str(rc.get(key) or "").strip()
            if val:
                self.assertNotIn(val, prompt, f"{key} が prompt に漏れている")
        # 基底 HTML 固有の PII 文字列も入らない
        self.assertNotIn("1980年1月1日", prompt)
        self.assertNotIn("証明写真", prompt)

    def test_prompt_contains_job_and_profile(self):
        prompt = rt.build_prompt(JOB, "", "positioning: PdM")
        self.assertIn("株式会社サンプル", prompt)
        self.assertIn("B2B SaaS", prompt)
        self.assertIn("positioning: PdM", prompt)
        self.assertIn("貴社", prompt)  # 書面語ルールを明示している

    def test_keep_context_is_facts_only(self):
        """固定行は職業事実のみ（prompt に入れても PII にならない）。"""
        ctx = rt.keep_context(BASE_FIXTURE)
        self.assertIn("約9年間", ctx)
        self.assertNotIn("<strong>", ctx)      # タグは剥がす
        self.assertNotIn("1980年1月1日", ctx)  # 固定行以外は入らない
        prompt = rt.build_prompt(JOB, "", "x", ctx)
        self.assertIn("約9年間", prompt)
        self.assertIn("矛盾", prompt)  # 年数の食い違い防止ルールを明示

    def test_jd_truncated(self):
        job = dict(JOB, raw_jd="あ" * (rt.MAX_JD_CHARS + 500))
        prompt = rt.build_prompt(job, "", "x")
        # prompt 定型文にも「あ」が数個あるため余裕を持たせる
        self.assertLessEqual(prompt.count("あ"), rt.MAX_JD_CHARS + 10)


class TestExtractFields(unittest.TestCase):
    def test_plain_json(self):
        raw = '{"志望領域": "A", "志望動機": "B", "アピール": "C"}'
        self.assertEqual(rt.extract_fields(raw),
                         {"志望領域": "A", "志望動機": "B", "アピール": "C"})

    def test_code_fence_and_prose(self):
        raw = 'はい。\n```json\n{"志望動機": "B", "アピール": "C"}\n```\n以上'
        self.assertEqual(rt.extract_fields(raw), {"志望動機": "B", "アピール": "C"})

    def test_empty_values_rejected(self):
        with self.assertRaises(ValueError):
            rt.extract_fields('{"志望動機": "", "アピール": "  "}')

    def test_garbage_rejected(self):
        with self.assertRaises(ValueError):
            rt.extract_fields("すみません、生成できません")


class TestCheckFields(unittest.TestCase):
    def test_over_limit_warns(self):
        fields = {"志望動機": "あ" * (rt.MAX_CHARS["志望動機"] + 1)}
        self.assertTrue(any("上限" in w for w in rt.check_fields(fields, "")))

    def test_ungrounded_number_warns(self):
        warnings = rt.check_fields({"アピール": "37 社の導入を担当"}, "9年間 / 3 プロダクト")
        self.assertTrue(any("37" in w for w in warnings))

    def test_grounded_number_ok(self):
        self.assertEqual(rt.check_fields({"アピール": "約9年間、3 件"}, "9年間 3 件"), [])


class TestInject(unittest.TestCase):
    FIELDS = {"志望領域": "LLM 基盤 PdM", "志望動機": "貴社の**Graph**基盤に",
              "アピール": "0→1 の実績"}

    def setUp(self):
        self.html = rt.inject(BASE_FIXTURE, self.FIELDS, today="2026年7月26日")

    def test_llm_fields_replaced(self):
        self.assertIn("LLM 基盤 PdM", self.html)
        self.assertIn("0→1 の実績", self.html)
        self.assertNotIn("旧・志望領域", self.html)
        self.assertNotIn("旧・アピール", self.html)

    def test_keep_row_preserved_verbatim(self):
        self.assertIn("エンジニア出身として<strong>約9年間</strong>従事。", self.html)

    def test_row_order(self):
        idx = [self.html.index(l) for l in ("志望領域", "経験", "志望動機", "アピール")]
        self.assertEqual(idx, sorted(idx))

    def test_md_bold_rendered_and_escaped(self):
        self.assertIn("<strong>Graph</strong>", self.html)
        escaped = rt.inject(BASE_FIXTURE, {"志望動機": "<script>x</script>"})
        self.assertNotIn("<script>", escaped)
        self.assertIn("&lt;script&gt;", escaped)

    def test_other_sections_untouched(self):
        for keep in ("免許・資格", "本人希望記入欄", "960万円",
                     "1980年1月1日（満46歳）"):
            self.assertIn(keep, self.html)

    def test_as_of_updated(self):
        self.assertIn("2026年7月26日 現在", self.html)
        self.assertNotIn("2026年7月25日", self.html)

    def test_missing_block_raises(self):
        with self.assertRaises(ValueError):
            rt.inject("<html><body>no block</body></html>", self.FIELDS)

    def test_partial_fields_skip_missing_rows(self):
        html = rt.inject(BASE_FIXTURE, {"志望動機": "のみ"})
        self.assertIn("のみ", html)
        self.assertNotIn("旧・志望領域", html)
        self.assertIn("経験", html)  # 固定行は残る


class TestEmbedAssets(unittest.TestCase):
    def test_relative_image_becomes_base64(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "assets").mkdir()
            (base / "assets" / "photo.JPG").write_bytes(b"\xff\xd8\xffhello")
            html = rt.inject(BASE_FIXTURE, {"志望動機": "x"}, base_dir=base)
            self.assertIn("data:image/jpeg;base64,", html)
            self.assertNotIn('src="assets/photo.JPG"', html)

    def test_missing_image_left_as_is(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            html = rt.inject(BASE_FIXTURE, {"志望動機": "x"}, base_dir=Path(tmp))
            self.assertIn('src="assets/photo.JPG"', html)

    def test_remote_url_untouched(self):
        html = rt.embed_assets('<img src="https://x.test/a.png">', Path("/tmp"))
        self.assertIn('src="https://x.test/a.png"', html)


if __name__ == "__main__":
    unittest.main()
