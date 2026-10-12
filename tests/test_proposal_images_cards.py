"""playbook の「能力ごとに 1 枚」画像生成の回帰テスト（codex 呼び出しはモック）。

他 6 stage が「1 stage 1 枚」なのに対し、playbook だけ `###` ブロックを数えた
枚数だけ画像を作る（**1 能力 = 1 call**）。ここで固定するのは 4 点:

- カード分割が見出し単位で正しく、本文に見出し行が残ること（図の見出しに使う）
- 枚数分だけ呼ばれ、1 枚失敗しても残りが作られること
- 能力が減ったとき、対応するカードの無い連番 PNG だけが消えること
- dry-run が LLM を呼ばず prompt を枚数分落とすこと
"""

from __future__ import annotations

from pathlib import Path

import pytest

from proposal import images, prompts
from proposal._llm import LLMUnavailable

PLAYBOOK_FILE = prompts.STAGES["playbook"]["file"]

_CARD = """\
### {name}
- **PM実務での表れ方**: 説明です。
- **想定シナリオ**: {name}が問われる状況を想定します。
- **進め方**:
  ① 一つ目を確認します。
  ② 二つ目を決めます。
- **注意点**:
  失敗する型です。避け方です。
- **思考ロジック**: 順番の理由です。
- **選考での問われ方と答えの論点**: 「問い」→ 論点です。
"""


def _job() -> dict:
    return {"id": 999, "company": "テスト株式会社", "title": "PdM"}


def _pack(tmp_path: Path, *names: str) -> Path:
    pdir = tmp_path / "999_test"
    pdir.mkdir(exist_ok=True)
    body = "# 能力プレイブック\n\n" + "\n".join(_CARD.format(name=n) for n in names)
    (pdir / PLAYBOOK_FILE).write_text(body, encoding="utf-8")
    return pdir


# ------------------------------------------------------------- カード分割

def test_playbook_is_a_card_image_stage_not_a_single_image_stage():
    assert "playbook" in images.CARD_IMAGE_STAGES
    assert "playbook" not in images.IMAGE_STAGES


def test_cards_of_splits_on_headings_and_keeps_the_heading_line():
    """見出し行を落とすと、図の見出しに能力名を使わせる指示が参照先を失う。"""
    md = "# 見出し\n\n" + _CARD.format(name="能力A") + _CARD.format(name="能力B")
    cards = images.cards_of(md)
    assert [n for n, _ in cards] == ["能力A", "能力B"]
    assert cards[0][1].startswith("### 能力A")
    assert "想定シナリオ" in cards[0][1]


def test_cards_of_returns_empty_for_a_document_without_cards():
    assert images.cards_of("# 見出しだけ\n\n本文です。") == []


def test_card_image_paths_are_zero_padded_and_sortable(tmp_path: Path):
    names = [images.card_image_path(tmp_path, "playbook", i).name
             for i in (1, 2, 10)]
    assert names[0] == "11_capability_playbook_01.png"
    assert names[2] == "11_capability_playbook_10.png"
    assert names == sorted(names)          # 連番が文字列ソートでも崩れない


def test_build_card_prompt_carries_the_capability_name_and_body():
    prompt = images.build_card_prompt("能力A", _CARD.format(name="能力A"))
    assert "能力A" in prompt
    assert "想定シナリオ" in prompt
    assert prompts.JA_IT_STYLE_RULES in prompt


# ------------------------------------------------------------- 生成の配線

def test_generate_cards_makes_one_image_per_capability(tmp_path: Path, monkeypatch):
    pdir = _pack(tmp_path, "能力A", "能力B", "能力C")
    calls = []

    def fake(prompt, dest, **kw):
        calls.append((prompt, Path(dest)))
        Path(dest).write_bytes(b"png")

    monkeypatch.setattr(images, "llm_image", fake)
    out = images.generate_cards(_job(), pdir)
    assert len(out) == 3 and len(calls) == 3
    assert [p.name for p in out] == ["11_capability_playbook_01.png",
                                     "11_capability_playbook_02.png",
                                     "11_capability_playbook_03.png"]
    # 各 prompt はそのカードの能力名だけを載せる（隣のカードを混ぜない）
    assert "能力A" in calls[0][0] and "能力C" not in calls[0][0]


def test_generate_cards_skips_existing_unless_forced(tmp_path: Path, monkeypatch):
    pdir = _pack(tmp_path, "能力A", "能力B")
    images.card_image_path(pdir, "playbook", 1).write_bytes(b"old")
    calls = []
    monkeypatch.setattr(images, "llm_image",
                        lambda p, d, **kw: (calls.append(d), Path(d).write_bytes(b"x")))
    images.generate_cards(_job(), pdir)
    assert len(calls) == 1                       # 01 は既存なので飛ばす
    images.generate_cards(_job(), pdir, force=True)
    assert len(calls) == 3                       # force なら 2 枚とも作り直す


def test_generate_cards_continues_after_one_failure(tmp_path: Path, monkeypatch):
    """1 枚の失敗で残りを捨てない（画像は本文と違い、部分的に揃っても価値がある）。"""
    pdir = _pack(tmp_path, "能力A", "能力B", "能力C")

    def flaky(prompt, dest, **kw):
        if "能力B" in prompt:
            raise RuntimeError("engine error")
        Path(dest).write_bytes(b"png")

    monkeypatch.setattr(images, "llm_image", flaky)
    out = images.generate_cards(_job(), pdir)
    assert [p.name for p in out] == ["11_capability_playbook_01.png",
                                     "11_capability_playbook_03.png"]


def test_generate_cards_propagates_llm_unavailable(tmp_path: Path, monkeypatch):
    """指揮中心自体が落ちているときは本文 stage と同じく中断する。"""
    pdir = _pack(tmp_path, "能力A", "能力B")

    def dead(prompt, dest, **kw):
        raise LLMUnavailable("miko-ws 停止")

    monkeypatch.setattr(images, "llm_image", dead)
    with pytest.raises(LLMUnavailable):
        images.generate_cards(_job(), pdir)


def test_generate_cards_raises_when_md_missing(tmp_path: Path):
    pdir = tmp_path / "999_test"
    pdir.mkdir()
    with pytest.raises(FileNotFoundError):
        images.generate_cards(_job(), pdir)


def test_generate_cards_raises_when_no_cards(tmp_path: Path):
    pdir = tmp_path / "999_test"
    pdir.mkdir()
    (pdir / PLAYBOOK_FILE).write_text("# 見出しだけ\n本文\n", encoding="utf-8")
    with pytest.raises(ValueError):
        images.generate_cards(_job(), pdir)


# ------------------------------------------------------------- 古い連番の掃除

def test_shrinking_capabilities_prunes_only_orphan_images(tmp_path: Path, monkeypatch):
    """能力が 3→2 に減ったら 03 は対応するカードが無い＝確実に古い。"""
    pdir = _pack(tmp_path, "能力A", "能力B", "能力C")
    monkeypatch.setattr(images, "llm_image",
                        lambda p, d, **kw: Path(d).write_bytes(b"png"))
    images.generate_cards(_job(), pdir)
    assert images.card_image_path(pdir, "playbook", 3).exists()

    _pack(tmp_path, "能力A", "能力B")          # md を 2 枚へ書き換え
    images.generate_cards(_job(), pdir, force=True)
    assert images.card_image_path(pdir, "playbook", 1).exists()
    assert images.card_image_path(pdir, "playbook", 2).exists()
    assert not images.card_image_path(pdir, "playbook", 3).exists()


def test_prune_never_touches_the_stage_level_images(tmp_path: Path, monkeypatch):
    """`01_company.png` 等を巻き込まない（glob が連番 2 桁付きに限定されていること）。"""
    pdir = _pack(tmp_path, "能力A")
    other = pdir / "01_company.png"
    other.write_bytes(b"keep")
    (pdir / "11_capability_playbook.png").write_bytes(b"keep")
    monkeypatch.setattr(images, "llm_image",
                        lambda p, d, **kw: Path(d).write_bytes(b"png"))
    images.generate_cards(_job(), pdir, force=True)
    assert other.exists()
    assert (pdir / "11_capability_playbook.png").exists()


# ------------------------------------------------------------- dry-run

def test_dry_run_writes_one_prompt_per_card_without_calling_llm(tmp_path: Path,
                                                                monkeypatch):
    pdir = _pack(tmp_path, "能力A", "能力B")

    def boom(*a, **kw):
        raise AssertionError("no_llm なのに呼ばれた")

    monkeypatch.setattr(images, "llm_image", boom)
    out = images.generate_cards(_job(), pdir, no_llm=True)
    assert len(out) == 2
    assert all(p.suffix == ".md" and p.exists() for p in out)
    assert "能力B" in out[1].read_text(encoding="utf-8")


def test_generate_all_includes_the_card_stage(tmp_path: Path, monkeypatch):
    pdir = _pack(tmp_path, "能力A", "能力B")
    monkeypatch.setattr(images, "llm_image",
                        lambda p, d, **kw: Path(d).write_bytes(b"png"))
    results = images.generate_all(_job(), pdir, stages=["playbook"])
    assert "2 枚" in results["playbook"]


# ------------------------------------------------------------- 部分生成（下見・1 枚直し）

def test_indices_limits_generation_to_the_named_cards(tmp_path: Path, monkeypatch):
    """版面の下見（1 枚だけ出す）と、md の 1 枚だけ直した後の作り直しに使う。"""
    pdir = _pack(tmp_path, "能力A", "能力B", "能力C")
    calls = []
    monkeypatch.setattr(images, "llm_image",
                        lambda p, d, **kw: (calls.append(Path(d).name),
                                            Path(d).write_bytes(b"png")))
    out = images.generate_cards(_job(), pdir, indices=[2])
    assert calls == ["11_capability_playbook_02.png"]
    assert [p.name for p in out] == ["11_capability_playbook_02.png"]


def test_indices_never_prunes(tmp_path: Path, monkeypatch):
    """一部しか見ていないので「余り」を判定できない。消したら事故。"""
    pdir = _pack(tmp_path, "能力A", "能力B", "能力C")
    monkeypatch.setattr(images, "llm_image",
                        lambda p, d, **kw: Path(d).write_bytes(b"png"))
    images.generate_cards(_job(), pdir)                    # 3 枚できる
    _pack(tmp_path, "能力A")                                # md を 1 枚へ
    images.generate_cards(_job(), pdir, indices=[1], force=True)
    assert images.card_image_path(pdir, "playbook", 3).exists()  # 残っている
    images.generate_cards(_job(), pdir, force=True)        # 全体を見たときに消える
    assert not images.card_image_path(pdir, "playbook", 3).exists()


# ------------------------------------------------- 縦横比の閘門（実測 8 枚中 1 枚が正方形）

def _sized_png(w: int, h: int) -> bytes:
    from PIL import Image
    import io
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(buf, format="PNG")
    return buf.getvalue()


def test_portrait_ok_judges_by_height_over_width(tmp_path: Path):
    tall, square = tmp_path / "t.png", tmp_path / "s.png"
    tall.write_bytes(_sized_png(864, 1821))
    square.write_bytes(_sized_png(1024, 1024))
    assert images.portrait_ok(tall) is True
    assert images.portrait_ok(square) is False


def test_portrait_ok_returns_none_when_unmeasurable(tmp_path: Path):
    """測れないときは None。合格でも不合格でもない（作り直しを回さない）。"""
    broken = tmp_path / "x.png"
    broken.write_bytes(b"not a png")
    assert images.portrait_ok(broken) is None


def test_square_output_is_regenerated_once(tmp_path: Path, monkeypatch):
    """正方形は下 2 区画が切れる（実測 05）。比率は機械が測れるので運任せにしない。"""
    pdir = _pack(tmp_path, "能力A")
    sizes = iter([(1024, 1024), (864, 1821)])       # 1 回目 正方形 → 2 回目 縦長
    calls = []

    def fake(prompt, dest, **kw):
        w, h = next(sizes)
        calls.append((w, h))
        Path(dest).write_bytes(_sized_png(w, h))

    monkeypatch.setattr(images, "llm_image", fake)
    out = images.generate_cards(_job(), pdir, force=True)
    assert calls == [(1024, 1024), (864, 1821)]     # 作り直しが 1 回走った
    assert len(out) == 1


def test_still_square_after_retry_is_kept_with_a_warning(tmp_path: Path,
                                                         monkeypatch, capsys):
    """2 回とも正方形なら諦めて残す — 課金を無限に回さない。目視を促す。"""
    pdir = _pack(tmp_path, "能力A")
    monkeypatch.setattr(images, "llm_image",
                        lambda p, d, **kw: Path(d).write_bytes(_sized_png(1024, 1024)))
    out = images.generate_cards(_job(), pdir, force=True)
    assert len(out) == 1                             # 捨てずに残す
    assert "目視" in capsys.readouterr().out


def test_unmeasurable_image_is_not_regenerated(tmp_path: Path, monkeypatch):
    """Pillow が無い環境で作り直しを回すと、同じものを課金しながら回し続ける。"""
    pdir = _pack(tmp_path, "能力A")
    calls = []
    monkeypatch.setattr(images, "llm_image",
                        lambda p, d, **kw: (calls.append(1), Path(d).write_bytes(b"x")))
    monkeypatch.setattr(images, "portrait_ok", lambda p: None)
    images.generate_cards(_job(), pdir, force=True)
    assert len(calls) == 1
