"""投遞用の完成済み書類（職務経歴書 / 履歴書）の単一情報源。

手作りの最新完成版を投遞に使う。差し替えは config/resume.yaml の 4 キーだけ
（缺檔 = 下記の既定値）。パイプライン側（direct_apply / prep / match_brief /
resume_tailor）は必ずここ経由で参照し、パスをハードコードしない。

    from tools import resume_assets
    resume_assets.shokumu_pdf()      # 職務経歴書 PDF（投遞添付）
    resume_assets.rirekisho_html()   # 履歴書 HTML（志望動機を JD 特化する基底）
"""

from __future__ import annotations

from pathlib import Path

from tools import app_config

ROOT = Path(__file__).parent.parent

DEFAULTS = {
    "shokumu_html": "resume/jp/output/shokumu-3page-0724-quiet.html",
    "shokumu_pdf": "resume/jp/output/shokumu-3page-0724-quiet.pdf",
    "rirekisho_html": "resume/jp/output/rirekisho-0725-quiet.html",
    "rirekisho_pdf": "resume/jp/output/rirekisho-0725-quiet.pdf",
}


def path_for(key: str) -> Path:
    """config/resume.yaml の値（無ければ既定値）を絶対パスで返す。"""
    if key not in DEFAULTS:
        raise KeyError(f"unknown resume asset key: {key}")
    raw = str(app_config.get("resume", key, DEFAULTS[key]))
    p = Path(raw)
    return p if p.is_absolute() else ROOT / p


def shokumu_html() -> Path:
    return path_for("shokumu_html")


def shokumu_pdf() -> Path:
    return path_for("shokumu_pdf")


def rirekisho_html() -> Path:
    return path_for("rirekisho_html")


def rirekisho_pdf() -> Path:
    return path_for("rirekisho_pdf")


def missing() -> list[str]:
    """存在しないアセットのキー一覧（診断・起動時チェック用）。"""
    return [k for k in DEFAULTS if not path_for(k).exists()]


def attachment_names() -> dict[str, str]:
    """日本の慣例に沿った添付ファイル名（氏名はローカルの data.yaml から）。"""
    from tools.deid import load_resume_contact
    name = (load_resume_contact().get("name_ja") or "").replace(" ", "")
    prefix = f"{name}_" if name else ""
    return {"shokumu": f"{prefix}職務経歴書.pdf", "rirekisho": f"{prefix}履歴書.pdf"}


if __name__ == "__main__":  # 診断: python3 -m tools.resume_assets
    for key in DEFAULTS:
        p = path_for(key)
        print(f"{'✓' if p.exists() else '✗'} {key}: {p}")
    print(f"添付名: {attachment_names()}")
