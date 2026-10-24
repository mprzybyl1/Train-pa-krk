"""Generuje ikony PWA do frontend/icons/ (wymaga Pillow): python scripts/make_icons.py"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parents[1] / "frontend" / "icons"
BG, FG, ACCENT = "#0b1020", "#eef2ff", "#7cb4ff"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def draw(size: int, safe: float) -> Image.Image:
    """safe: część płótna na treść (maskable wymaga marginesu ~20%)."""
    s = size * 4  # rysujemy większe i skalujemy = gładkie krawędzie
    img = Image.new("RGB", (s, s), BG)
    d = ImageDraw.Draw(img)
    box = s * safe
    off = (s - box) / 2
    big = ImageFont.truetype(FONT, int(box * 0.30))
    d.text((s / 2, off + box * 0.22), "PZ", font=big, fill=FG, anchor="mm")
    d.text((s / 2, off + box * 0.80), "KRK", font=big, fill=FG, anchor="mm")
    # dwie strzałki ⇄
    w = int(box * 0.05)
    y1, y2 = off + box * 0.45, off + box * 0.58
    x0, x1 = off + box * 0.18, off + box * 0.82
    head = box * 0.09
    d.line([(x0, y1), (x1, y1)], fill=ACCENT, width=w)
    d.polygon([(x1 + w, y1), (x1 - head, y1 - head), (x1 - head, y1 + head)], fill=ACCENT)
    d.line([(x0, y2), (x1, y2)], fill=ACCENT, width=w)
    d.polygon([(x0 - w, y2), (x0 + head, y2 - head), (x0 + head, y2 + head)], fill=ACCENT)
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    draw(192, 0.82).save(OUT / "icon-192.png")
    draw(512, 0.82).save(OUT / "icon-512.png")
    draw(512, 0.62).save(OUT / "icon-maskable-512.png")
    draw(180, 0.82).save(OUT / "apple-touch-icon.png")
    draw(32, 0.95).save(OUT / "favicon-32.png")
    print("Ikony zapisane w", OUT)


if __name__ == "__main__":
    main()
