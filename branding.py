"""Render the app's code-native identity at all Windows icon sizes."""
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent


def make_icon(recording=False):
    factor = 4
    image = Image.new("RGBA", (256 * factor, 256 * factor))
    draw = ImageDraw.Draw(image)
    def box(coords):
        return tuple(int(x * factor) for x in coords)
    draw.rounded_rectangle(box((4, 4, 252, 252)), radius=58 * factor, fill="#191d2b")
    draw.rounded_rectangle(box((8, 8, 248, 248)), radius=54 * factor, outline="#39334f", width=2 * factor)
    # Four framing corners remain readable at 16px, and match the SVG master.
    for coords in (((92, 60), (64, 60), (64, 88)), ((164, 60), (192, 60), (192, 88)),
                   ((64, 168), (64, 196), (92, 196)), ((192, 168), (192, 196), (164, 196))):
        points = [(int(x * factor), int(y * factor)) for x, y in coords]
        draw.line(points, fill="#b99afa", width=12 * factor, joint="curve")
        for x, y in (points[0], points[-1]):
            draw.ellipse((x - 6*factor, y - 6*factor, x + 6*factor, y + 6*factor), fill="#b99afa")
    draw.ellipse(box((97, 97, 159, 159)), fill="#fa748d" if recording else "#e8b3eb")
    return image.resize((256, 256), Image.Resampling.LANCZOS)


def main():
    assets = ROOT / "assets"
    assets.mkdir(exist_ok=True)
    image = make_icon()
    image.save(ROOT / "icon.png")
    image.save(ROOT / "icon.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    make_icon(True).save(ROOT / "icon_recording.png")
    small = Image.new("RGB", (64, 64), "#191d2b")
    small.paste(image.resize((56, 56), Image.Resampling.LANCZOS), (4, 4), image.resize((56, 56), Image.Resampling.LANCZOS))
    small.save(assets / "installer-icon.bmp")


if __name__ == "__main__":
    main()
