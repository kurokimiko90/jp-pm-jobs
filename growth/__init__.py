"""growth — 產品技能增長自動流程。

求人 JD を入力に、「この仕事をどう遂行して成長を作るか」の実戦手冊を生成する。

  python3 -m growth <job_id>                      # 全段生成
  python3 -m growth <job_id> --stage playbook     # 単段のみ
  python3 -m growth <job_id> --force              # キャッシュ無視で再生成
  python3 -m growth <job_id> --no-llm             # prompt だけ落として中身は書かない
  python3 -m growth --list-stages

産出: output/growth/<job_id>_<company>/01..08*.md + README.md
"""

from .pipeline import run, pack_dir  # noqa: F401

__all__ = ["run", "pack_dir"]
