from __future__ import annotations

import argparse
import json
import sys

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tts.service import generate_audio


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate local TTS audio for prep/apply packs.")
    parser.add_argument("kind", choices=["prep", "apply"])
    parser.add_argument("dirname", help="pack directory name under output/prep or output/apply")
    parser.add_argument("--preset", default="core")
    parser.add_argument("--section-id", action="append", dest="section_ids", default=None)
    parser.add_argument("--voice", default="")
    parser.add_argument("--lang", default="ja")
    parser.add_argument("--audio-format", default="")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    result = generate_audio(
        args.kind,
        args.dirname,
        preset=args.preset,
        section_ids=args.section_ids,
        voice=args.voice,
        lang=args.lang,
        audio_format=args.audio_format,
        force=args.force,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
