"""Regenerate the F2 visual review set using only the public HTTP API.

Run: uv run python scripts/readability.py --url http://127.0.0.1:8000 --output <directory>
"""

import argparse
import io
import json
import random
from pathlib import Path

import httpx
from PIL import Image, ImageDraw

BACKGROUNDS = (
    "white",
    "black",
    "gray",
    "cream",
    "hard-edge",
    "gradient",
    "noise",
    "stripes",
    "transparent",
)
LONG = (
    "A long caption must stay readable over every difficult background and wrap without losing "
    "a single word while keeping a clear black outline around it."
)


def background(name: str) -> Image.Image:
    image = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    solids = {"white": "white", "black": "black", "gray": "#808080", "cream": "#fffdd0"}
    if name in solids:
        draw.rectangle((0, 0, 399, 399), fill=solids[name])
    elif name == "hard-edge":
        draw.rectangle((0, 0, 199, 399), fill="white")
        draw.rectangle((200, 0, 399, 399), fill="black")
    elif name == "gradient":
        for x in range(400):
            value = round(255 * (399 - x) / 399)
            draw.line((x, 0, x, 399), fill=(value, value, value, 255))
    elif name == "noise":
        rng = random.Random(2026)
        image.putdata([(value, value, value, 255) for value in rng.choices(range(256), k=160000)])
    elif name == "stripes":
        for x in range(0, 400, 20):
            draw.rectangle((x, 0, x + 19, 399), fill="red" if x % 40 == 0 else "blue")
    return image


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, object]] = []
    with httpx.Client(base_url=args.url, timeout=30) as client:
        for variant in ("short", "long"):
            sheet = Image.new("RGB", (1200, 1290), "#e5e7eb")
            for index, name in enumerate(BACKGROUNDS):
                source = background(name)
                payload = io.BytesIO()
                source.save(payload, "PNG")
                top = "TOP TEXT" if variant == "short" else LONG
                bottom = "BOTTOM TEXT" if variant == "short" else LONG
                response = client.post(
                    "/memes",
                    files={"image": ("background.png", payload.getvalue(), "image/png")},
                    data={"top_text": top, "bottom_text": bottom},
                )
                response.raise_for_status()
                assert response.headers["content-type"] == "image/png"
                meme = Image.open(io.BytesIO(response.content)).convert("RGBA")
                assert meme.size == (400, 400)
                for box in ((0, 0, 400, 100), (0, 300, 400, 400)):
                    colors = set(meme.crop(box).get_flattened_data())
                    assert (255, 255, 255, 255) in colors, (name, variant, "no white fill")
                    assert (0, 0, 0, 255) in colors, (name, variant, "no black outline")
                if name == "transparent":
                    assert meme.getpixel((200, 200)) == (0, 0, 0, 0)
                filename = f"{variant}-{name}.png"
                meme.save(args.output / filename)
                # Checkerboard makes preserved alpha visible on the contact sheet.
                tile = Image.new("RGBA", (400, 400), "white")
                tile_draw = ImageDraw.Draw(tile)
                for y in range(0, 400, 20):
                    for x in range(0, 400, 20):
                        if (x + y) % 40 == 0:
                            tile_draw.rectangle((x, y, x + 19, y + 19), fill="#cbd5e1")
                tile = Image.alpha_composite(tile, meme)
                x, y = (index % 3) * 400, (index // 3) * 430
                sheet.paste(tile.convert("RGB"), (x, y + 30))
                ImageDraw.Draw(sheet).text((x + 8, y + 8), f"{name} / {variant}", fill="black")
                results.append(
                    {
                        "background": name,
                        "variant": variant,
                        "status": response.status_code,
                        "top_chars": len(top),
                        "bottom_chars": len(bottom),
                        "fill_and_outline_both_bands": True,
                        "file": filename,
                    }
                )
            sheet.save(args.output / f"{variant}-contact-sheet.png")
    (args.output / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps({"passed": len(results), "output": str(args.output), "long_chars": len(LONG)}))


if __name__ == "__main__":
    main()
