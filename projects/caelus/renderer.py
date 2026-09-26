from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Dict, Tuple

from PIL import Image, ImageDraw, ImageFont

from kaban.assets import project_assets_dir
from projects.caelus.domain import SIGN_NAMES, SIGN_ORDER

ROOT = Path(__file__).resolve().parents[2]
ASSETS_DIR = project_assets_dir("caelus")

CARD_SIZE = (1080, 1350)

# Координаты рассчитаны под текущий фон CAELUS.
LAYOUT = {
    "glyph_box": (72, 118, 118, 118),       # x, y, w, h
    "art_box": (205, 105, 670, 585),        # x, y, w, h
    "title_y": 757,
    "date_y": 829,
    "forecast_box": (125, 935, 830, 235),   # x, y, w, h
}

SIGN_NAMES_RU = SIGN_NAMES["ru"]


COLORS = {
    "ivory": (244, 231, 203, 255),
    "gold": (216, 174, 103, 255),
    "muted_gold": (196, 166, 118, 255),
}


def find_font(*candidates: str) -> str:
    """Ищет шрифт в типичных Windows/Linux/macOS путях."""
    candidates_paths = []
    win = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for name in candidates:
        candidates_paths.extend([
            win / name,
            Path("/usr/share/fonts/truetype/ebgaramond") / name,
            Path("/usr/share/fonts/truetype/dejavu") / name,
            Path("/usr/share/fonts/truetype/freefont") / name,
            Path("/Library/Fonts") / name,
            Path.home() / "Library/Fonts" / name,
        ])
    for p in candidates_paths:
        if p.exists():
            return str(p)
    # Последний безопасный fallback в контейнере/многих Linux.
    fallback = "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"
    if Path(fallback).exists():
        return fallback
    raise FileNotFoundError("Не удалось найти подходящий serif-шрифт.")


FONT_REGULAR = find_font(
    "georgia.ttf",
    "EBGaramond-Regular.ttf",
    "DejaVuSerif.ttf",
)
FONT_BOLD = find_font(
    "georgiab.ttf",
    "EBGaramond-SemiBold.ttf",
    "DejaVuSerif-Bold.ttf",
)


def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size=size)


def alpha_trim(img: Image.Image, padding: int = 4) -> Image.Image:
    """Убирает лишние прозрачные поля вокруг PNG."""
    img = img.convert("RGBA")
    alpha = img.getchannel("A")
    bbox = alpha.getbbox()
    if not bbox:
        return img
    l, t, r, b = bbox
    l = max(0, l - padding)
    t = max(0, t - padding)
    r = min(img.width, r + padding)
    b = min(img.height, b + padding)
    return img.crop((l, t, r, b))


def contain(img: Image.Image, max_w: int, max_h: int) -> Image.Image:
    """Масштабирует с сохранением пропорций."""
    img = alpha_trim(img)
    ratio = min(max_w / img.width, max_h / img.height)
    size = (max(1, round(img.width * ratio)), max(1, round(img.height * ratio)))
    return img.resize(size, Image.Resampling.LANCZOS)


def paste_center(canvas: Image.Image, layer: Image.Image, box: Tuple[int, int, int, int]) -> None:
    x, y, w, h = box
    layer = contain(layer, w, h)
    px = x + (w - layer.width) // 2
    py = y + (h - layer.height) // 2
    canvas.alpha_composite(layer, (px, py))


def draw_centered(draw: ImageDraw.ImageDraw, text: str, y: int, fnt: ImageFont.FreeTypeFont, fill) -> None:
    bbox = draw.textbbox((0, 0), text, font=fnt)
    x = (CARD_SIZE[0] - (bbox[2] - bbox[0])) // 2
    draw.text((x, y), text, font=fnt, fill=fill)


def wrap(draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        width = draw.textbbox((0, 0), candidate, font=fnt)[2]
        if width <= max_w:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def draw_forecast(draw: ImageDraw.ImageDraw, text: str) -> None:
    x, y, w, h = LAYOUT["forecast_box"]
    chosen = None
    for size in range(38, 25, -1):
        fnt = font(FONT_REGULAR, size)
        lines = wrap(draw, text, fnt, w)
        line_h = round(size * 1.38)
        total_h = len(lines) * line_h
        if len(lines) <= 5 and total_h <= h:
            chosen = (fnt, lines, line_h)
            break
    if chosen is None:
        fnt = font(FONT_REGULAR, 26)
        lines = wrap(draw, text, fnt, w)[:5]
        line_h = 36
    else:
        fnt, lines, line_h = chosen

    total_h = len(lines) * line_h
    cy = y + (h - total_h) // 2
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=fnt)
        line_w = bbox[2] - bbox[0]
        draw.text((x + (w - line_w) // 2, cy), line, font=fnt, fill=COLORS["ivory"])
        cy += line_h


def load_background() -> Image.Image:
    bg = Image.open(ASSETS_DIR / "background.png").convert("RGBA")
    # Исходник практически 4:5. Масштабируем точно под Instagram 1080x1350.
    return bg.resize(CARD_SIZE, Image.Resampling.LANCZOS)


def validate_assets() -> None:
    missing = []
    for sign in SIGN_ORDER:
        for prefix in ("art", "glyph"):
            p = ASSETS_DIR / f"{prefix}_{sign}.png"
            if not p.exists():
                missing.append(p.name)
    if not (ASSETS_DIR / "background.png").exists():
        missing.append("background.png")
    if missing:
        raise FileNotFoundError("Не хватает файлов: " + ", ".join(missing))


def render_card(sign: str, date: str, forecast: str, out_dir: Path, name: str | None = None) -> Path:
    canvas = load_background()

    art = Image.open(ASSETS_DIR / f"art_{sign}.png").convert("RGBA")
    glyph = Image.open(ASSETS_DIR / f"glyph_{sign}.png").convert("RGBA")

    paste_center(canvas, art, LAYOUT["art_box"])
    paste_center(canvas, glyph, LAYOUT["glyph_box"])

    draw = ImageDraw.Draw(canvas)
    title = name or SIGN_NAMES_RU[sign]

    draw_centered(draw, title, LAYOUT["title_y"], font(FONT_BOLD, 48), COLORS["ivory"])
    draw_centered(draw, date.upper(), LAYOUT["date_y"], font(FONT_REGULAR, 24), COLORS["muted_gold"])
    draw_forecast(draw, forecast)

    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"caelus_{sign}.png"
    canvas.convert("RGB").save(out, "PNG", compress_level=3)
    return out


def load_data(path: Path) -> Dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def main() -> None:
    parser = argparse.ArgumentParser(description="Генератор ежедневных карточек CAELUS")
    parser.add_argument("data", nargs="?", default="sample_forecasts_ru.json", help="JSON с датой и прогнозами")
    parser.add_argument("--out", default="output", help="Папка результата")
    parser.add_argument("--sign", choices=SIGN_ORDER, help="Пересобрать только одну карточку")
    args = parser.parse_args()

    validate_assets()
    data = load_data(ROOT / args.data if not Path(args.data).is_absolute() else Path(args.data))
    date = data["date"]
    signs = data["signs"]
    out_dir = ROOT / args.out if not Path(args.out).is_absolute() else Path(args.out)

    selected = [args.sign] if args.sign else SIGN_ORDER
    for sign in selected:
        item = signs[sign]
        path = render_card(sign, date, item.get("card", item.get("forecast", "")), out_dir, item.get("name"))
        print(f"[OK] {path.name}")


if __name__ == "__main__":
    main()
