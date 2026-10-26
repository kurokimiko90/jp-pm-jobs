"""tools.ragent_crossmatch の社名照合（英語社名 → 日本語社名）。"""

from tools.ragent_crossmatch import _same_company, pick_match, search_keyword


def test_english_name_matches_japanese_entity():
    assert _same_company("Daiichi Sankyo", "第一三共株式会社")
    assert _same_company("KPMGジャパン／KPMG Japan", "ＫＰＭＧコンサルティング株式会社")
    assert _same_company("Giorgio Armani", "ジョルジオ　アルマーニ　ジャパン株式会社")


def test_unmapped_names_still_need_exact_match():
    assert _same_company("株式会社原石", "株式会社原石")
    assert not _same_company("Meta", "株式会社サンプル")


def test_search_keyword_uses_japanese_name():
    assert search_keyword("Panasonic") == "パナソニック"
    assert search_keyword("株式会社原石") == "原石"


def test_group_companies_with_close_titles_are_ambiguous():
    job = {"company": "Daiichi Sankyo", "title": "DX推進担当",
           "salary_min": None, "salary_max": None}
    cards = [
        {"company": "第一三共株式会社", "title": "DX推進担当", "source_id": "1"},
        {"company": "第一三共ヘルスケア株式会社", "title": "DX推進担当", "source_id": "2"},
    ]
    status, card, _ = pick_match(job, cards)
    assert status == "ambiguous" and card is None
