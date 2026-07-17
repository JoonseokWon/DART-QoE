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

    # Keep only the DQ monogram so it remains distinct in Explorer's 16–32 px views.
    monogram_font = font(470)
    d_box = draw.textbbox((0, 0), "D", font=monogram_font, stroke_width=3)
    q_box = draw.textbbox((0, 0), "Q", font=monogram_font, stroke_width=3)
    gap = 34
    total_width = (d_box[2] - d_box[0]) + gap + (q_box[2] - q_box[0])
    top = (SIZE - max(d_box[3] - d_box[1], q_box[3] - q_box[1])) // 2
    d_x = (SIZE - total_width) // 2 - d_box[0]
    d_y = top - d_box[1]
    q_x = d_x + (d_box[2] - d_box[0]) + gap - q_box[0]
    q_y = top - q_box[1]
    draw.text((d_x, d_y), "D", font=monogram_font, fill=(255, 255, 255, 255), stroke_width=3)
    draw.text((q_x, q_y), "Q", font=monogram_font, fill=(66, 199, 219, 255), stroke_width=3)
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
