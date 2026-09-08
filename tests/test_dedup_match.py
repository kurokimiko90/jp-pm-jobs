"""tools.dedup_match 與入庫前模糊查重的迴歸測試。

固定案例取自實測的 recruit_agent / recruiter_agent 重複組（標題相似 98-100%）
與唯一職缺（≤16%），鎖死 0.85 門檻的判定行為。
"""

import sqlite3
import unittest

from tools.dedup_match import (
    TITLE_SIM_THRESHOLD,
    find_duplicate,
    level_profile,
    normalize_company,
    normalize_title,
    title_similarity,
)


def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE jobs (id INTEGER PRIMARY KEY, source TEXT, "
        "company TEXT, title TEXT, company_norm TEXT, raw_jd TEXT)"
    )
    return conn


def _seed(conn, source, company, title, raw_jd=None):
    conn.execute(
        "INSERT INTO jobs (source, company, title, company_norm, raw_jd) "
        "VALUES (?, ?, ?, ?, ?)",
        (source, company, title, normalize_company(company), raw_jd),
    )
    return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]


class TestNormalize(unittest.TestCase):
    def test_company_strips_legal_form(self):
        self.assertEqual(normalize_company("株式会社ホゲホゲ"), "ホゲホゲ")
        self.assertEqual(
            normalize_company("DEMOCO HOLDINGS株式会社"), "democo"
        )
        # 全形/半形統一
        self.assertEqual(
            normalize_company("ｄｅｍｏｃｏ株式会社"), normalize_company("democo株式会社")
        )

    def test_company_english_suffix_word_boundary(self):
        # 實測漏抓：Meltwater Group ↔ Meltwater（英文 Group 需去除）
        self.assertEqual(
            normalize_company("Meltwater Group"), normalize_company("Meltwater")
        )
        # Corporation 必須整字去除，不可砍成 oration
        self.assertEqual(normalize_company("ABC Corporation"), "abc")
        # word boundary：Groupon 不可被誤傷
        self.assertEqual(normalize_company("Groupon"), "groupon")

    def test_company_strips_paren_note(self):
        # 實測漏抓：株式会社demo(四ツ谷) ↔ 株式会社demo(六本木)
        self.assertEqual(
            normalize_company("株式会社demo(四ツ谷)"),
            normalize_company("株式会社demo(六本木)"),
        )

    def test_company_alias_ja_en(self):
        # 實測漏抓：LY Corporation (linkedin) ↔ LINEヤフー株式会社 (indeed)
        self.assertEqual(
            normalize_company("LY Corporation"),
            normalize_company("LINEヤフー株式会社"),
        )
        self.assertEqual(
            normalize_company("Rakuten"), normalize_company("楽天グループ株式会社")
        )

    def test_title_strips_bullet_prefix(self):
        a = normalize_title("【プロダクトマネージャー(PdM)】オリパスタジアム/０→１")
        b = normalize_title("・【プロダクトマネージャー(PdM)】オリパスタジアム/0→1")
        self.assertEqual(a, b)


class TestSimilarity(unittest.TestCase):
    def test_duplicate_pair_high(self):
        sim = title_similarity(
            "【PM候補】伊藤忠商事×ファミマのベンチャー/リアル-デジタルを繋ぐ次世代広告",
            "【PM候補】伊藤忠商事×ファミマのベンチャー/リアル-デジタルを繋ぐ次世代広告",
        )
        self.assertGreaterEqual(sim, TITLE_SIM_THRESHOLD)

    def test_different_roles_low(self):
        sim = title_similarity(
            "《412》【虎ノ門/プロダクトマネージャー（エンジニア出身PdM）】",
            "株式会社サンプルキャリア",
        )
        self.assertLess(sim, TITLE_SIM_THRESHOLD)


class TestFindDuplicate(unittest.TestCase):
    def test_cross_source_duplicate_hit(self):
        conn = _make_db()
        kept = _seed(
            conn, "recruiter_agent", "株式会社ホゲホゲ",
            "・【プロダクトマネージャー(PdM)】オリパスタジアム/0→1フェーズ/裁量大",
        )
        new_job = {
            "company": "株式会社ホゲホゲ",
            "title": "【プロダクトマネージャー(PdM)】オリパスタジアム/０→１フェーズ/裁量大",
        }
        self.assertEqual(find_duplicate(conn, new_job), kept)

    def test_same_company_different_role_miss(self):
        conn = _make_db()
        _seed(conn, "recruiter_agent", "株式会社サンプルキャリア", "データサイエンティスト")
        new_job = {
            "company": "サンプルキャリア株式会社",
            "title": "《412》【虎ノ門/プロダクトマネージャー（エンジニア出身PdM）】",
        }
        self.assertIsNone(find_duplicate(conn, new_job))

    def test_empty_company_miss(self):
        conn = _make_db()
        _seed(conn, "x", "", "プロダクトマネージャー")
        self.assertIsNone(find_duplicate(conn, {"company": "", "title": "プロダクトマネージャー"}))


# 共用假 JD：公司 boilerplate 佔比極高的模板（實測 ashby/greenhouse 型態）
_BOILERPLATE_JD = (
    "About us: We build AI products for enterprises across the globe. "
    "Our mission is to make work meaningful for everyone. " * 30
)


class TestJdSecondLine(unittest.TestCase):
    """raw_jd ≥0.92 第二道防線的行為鎖定（2026-07 收緊：僅跨來源 + 職級守衛）。"""

    def test_same_source_boilerplate_not_merged(self):
        # 實測誤殺案例：ashby 同公司「Agent Development PM」德語/葡語兩個崗位
        # JD 相似度 0.97+，但 source_id 不同 = 不同刊登，不可合併
        conn = _make_db()
        _seed(
            conn, "ashby-api", "Example AI",
            "Product Manager, Agent Development (German speaking)",
            _BOILERPLATE_JD + " You will build agents for the German market.",
        )
        new_job = {
            "source": "ashby-api",
            "company": "Example AI",
            "title": "Product Manager, Agent Development (Brazilian Portuguese speaking)",
            "raw_jd": _BOILERPLATE_JD + " You will build agents for the Brazilian market.",
        }
        self.assertIsNone(find_duplicate(conn, new_job))

    def test_cross_source_retitled_repost_merged(self):
        # JD 防線的本職：同一職缺跨來源換標題再現（標題中度相似 + JD 幾乎相同）
        conn = _make_db()
        jd = _BOILERPLATE_JD + " 資産管理サービスのPdMとしてロードマップを牽引。"
        kept = _seed(
            conn, "indeed_jp", "株式会社サンプル",
            "【プロダクトマネージャー】資産管理サービス/新機能の企画推進",
            jd,
        )
        new_job = {
            "source": "recruiter_agent",
            "company": "株式会社サンプル",
            "title": "プロダクトマネージャー(資産管理サービス)企画推進担当",
            "raw_jd": jd,
        }
        self.assertEqual(find_duplicate(conn, new_job), kept)

    def test_senior_regular_shared_jd_not_merged(self):
        # 實測誤殺案例：senior 與 regular 共用 JD 模板 → 職級守衛擋下
        conn = _make_db()
        jd = _BOILERPLATE_JD + " 1,700万人が利用する資産管理サービス。"
        _seed(
            conn, "indeed_jp", "サンプルフォワード",
            "【シニアプロダクトマネージャー】資産管理/形成サービス",
            jd,
        )
        new_job = {
            "source": "recruiter_agent",
            "company": "サンプルフォワード",
            "title": "【プロダクトマネージャー】資産管理/形成サービス",
            "raw_jd": jd,
        }
        self.assertIsNone(find_duplicate(conn, new_job))


class TestLevelProfile(unittest.TestCase):
    def test_levels_detected(self):
        self.assertEqual(level_profile("シニアPdM"), frozenset({"senior"}))
        self.assertEqual(level_profile("Senior Product Manager"), frozenset({"senior"}))
        self.assertEqual(level_profile("プロダクトマネージャー"), frozenset())

    def test_lead_excludes_verb_usage(self):
        # 「リードする」は動詞、職級ではない
        self.assertEqual(level_profile("プロダクトをリードするPdM"), frozenset())
        self.assertEqual(level_profile("リードPdM"), frozenset({"lead"}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
