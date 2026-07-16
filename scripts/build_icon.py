from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
PNG_PATH = ASSETS / "DART-QoE.png"
ICO_PATH = ASSETS / "DART-QoE.ico"
SIZE = 1024


def font(size: int) -> ImageFont.FreeTypeFont:
    candidates = (
        Path(r"C:\Windows\Fonts\segoeuib.ttf"),
        Path(r"C:\Windows\Fonts\seguisb.ttf"),
        Path(r"C:\Windows\Fonts\arialbd.ttf"),
    )
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    raise FileNotFoundError("A bold Windows font was not found.")


def build_icon() -> Image.Image:
    canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    gradient = Image.new("RGBA", (SIZE, SIZE))
    pixels = gradient.load()
    top = (17, 42, 70)
    bottom = (28, 72, 111)
    for y in range(SIZE):
        ratio = y / (SIZE - 1)
        color = tuple(round(top[index] * (1 - ratio) + bottom[index] * ratio) for index in range(3)) + (255,)
        for x in range(SIZE):
            pixels[x, y] = color

    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle((36, 36, 988, 988), radius=220, fill=255)
    canvas.alpha_composite(Image.composite(gradient, Image.new("RGBA", (SIZE, SIZE)), mask))
    draw = ImageDraw.Draw(canvas)

    # Compact monogram remains readable in Explorer's 32 px icon view.
    monogram_font = font(360)
    draw.text((132, 158), "D", font=monogram_font, fill=(255, 255, 255, 255), stroke_width=3)
    draw.text((500, 158), "Q", font=monogram_font, fill=(66, 199, 219, 255), stroke_width=3)

    # QoE-style performance line: uneven conversion followed by a clear rise.
    points = [(190, 760), (385, 660), (565, 715), (810, 505)]
    draw.line(points, fill=(255, 255, 255, 255), width=48, joint="curve")
    for x, y in points[:-1]:
        draw.ellipse((x - 30, y - 30, x + 30, y + 30), fill=(66, 199, 219, 255), outline=(255, 255, 255, 255), width=12)
    end_x, end_y = points[-1]
    draw.polygon(
        [(end_x + 82, end_y - 72), (end_x + 40, end_y + 52), (end_x - 55, end_y - 40)],
        fill=(66, 199, 219, 255),
    )
    return canvas


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    image = build_icon()
    image.save(PNG_PATH, optimize=True)
    image.save(ICO_PATH, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(PNG_PATH)
    print(ICO_PATH)


if __name__ == "__main__":
    main()
