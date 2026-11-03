"""Кадры для проверки глазами: лист кадров с временем под каждым.

    python3 tools/frames.py <видео> 1.5,8,12.2          – кадры на этих секундах
    python3 tools/frames.py <видео> --every 2            – кадр каждые 2 с
    python3 tools/frames.py <видео> --grid                – один кадр с сеткой координат (для кропа лица)
    python3 tools/frames.py <видео> --crop x,y,w,h 2,9,20 – что попадёт в кроп на этих секундах (проверка лица)

Лист сохраняется рядом с видео в check/, путь печатается – открыть картинку и посмотреть."""
import argparse
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from common import duration


def font(size):
    for f in ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Helvetica.ttc",
              "C:/Windows/Fonts/arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if Path(f).exists():
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def grab(video, t, crop=None, width=540):
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "f.png"
        vf = (f"crop={crop[2]}:{crop[3]}:{crop[0]}:{crop[1]}," if crop else "") + f"scale={width}:-2"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1", "-vf", vf, str(out)],
                       check=True)
        return Image.open(out).convert("RGB")


def sheet(imgs, labels, cols, path):
    w, h = imgs[0].size
    rows = -(-len(imgs) // cols)
    S = Image.new("RGB", (cols * w, rows * (h + 44)), (20, 20, 20))
    d, f = ImageDraw.Draw(S), font(28)
    for k, (im, lb) in enumerate(zip(imgs, labels)):
        x, y = (k % cols) * w, (k // cols) * (h + 44)
        S.paste(im, (x, y))
        d.text((x + 10, y + h + 6), lb, fill=(255, 255, 255), font=f)
    S.save(path, quality=88)
    return path


def grid(video, path, t=1.0):
    im = grab(video, t, width=1080)
    d, f = ImageDraw.Draw(im), font(24)
    W, H = im.size
    for x in range(0, W, 100):
        d.line([(x, 0), (x, H)], fill=(253, 228, 69) if x % 500 == 0 else (120, 120, 120), width=1)
        d.text((x + 3, 3), str(x), fill=(253, 228, 69), font=f)
    for y in range(0, H, 100):
        d.line([(0, y), (W, y)], fill=(253, 228, 69) if y % 500 == 0 else (120, 120, 120), width=1)
        d.text((3, y + 3), str(y), fill=(253, 228, 69), font=f)
    im.save(path, quality=90)
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("times", nargs="?")
    ap.add_argument("--every", type=float)
    ap.add_argument("--grid", action="store_true")
    ap.add_argument("--crop")
    ap.add_argument("--cols", type=int, default=4)
    a = ap.parse_args()
    v = Path(a.video).resolve()
    out = v.parent / "check"
    out.mkdir(exist_ok=True)
    if a.grid:
        print(grid(v, out / f"{v.stem}_grid.jpg", float(a.times or 1)))
        return
    if a.every:
        d = duration(v)
        ts = [round(i * a.every, 2) for i in range(int(d / a.every) + 1)]
    else:
        ts = [float(t) for t in (a.times or "1").split(",")]
    crop = [int(c) for c in a.crop.split(",")] if a.crop else None
    imgs = [grab(v, t, crop, 360 if not crop else 540) for t in ts]
    name = f"{v.stem}_{'crop_' if crop else ''}{ts[0]:g}-{ts[-1]:g}.jpg"
    print(sheet(imgs, [f"{t:.2f} с" for t in ts], min(a.cols, len(imgs)), out / name))


if __name__ == "__main__":
    main()
