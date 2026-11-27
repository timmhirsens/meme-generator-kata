import argparse
import os
from pathlib import Path

import uvicorn

from meme_generator.captions import CaptionError
from meme_generator.service import create_meme
from meme_generator.uploads import UploadError


def main() -> None:
    parser = argparse.ArgumentParser(description="Create memes locally or serve the web API.")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("serve", help="Start the HTTP server (default).")
    render = commands.add_parser("render", help="Read a local image and write its meme.")
    render.add_argument("input", type=Path)
    render.add_argument("output", type=Path)
    render.add_argument("--top-text", default="")
    render.add_argument("--bottom-text", default="")
    args = parser.parse_args()
    if args.command == "render":
        try:
            data, _ = create_meme(args.input.read_bytes(), args.top_text, args.bottom_text)
            args.output.write_bytes(data)
        except (UploadError, CaptionError, OSError) as exc:
            parser.exit(1, f"{exc}\n")
        return
    uvicorn.run(
        "meme_generator.app:app",
        host=os.environ.get("MEME_HOST", "127.0.0.1"),
        port=int(os.environ.get("MEME_PORT", "8000")),
        access_log=False,
    )


if __name__ == "__main__":
    main()
