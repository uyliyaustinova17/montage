"""Слайды презентации (PDF) → кадры 1920x1080 и окно под видео.

    python3 tools/pdf_slides.py <проект> "<путь к презентации.pdf>"

Рисует projects/<имя>/slides_hd/slide01.png … и печатает окно под видео автора [x, y, w, h] для slides_plan.json.
Окно ищется по надписи «ВИДЕО» на слайде: берётся цвет заливки рядом с надписью и считаются границы этой заливки.
Высота окна подгоняется под 3:4 от ширины – кроп 3:4 ложится в окно без искажений."""
import sys

import numpy as np
import pymupdf
from PIL import Image

from common import project

W = 1920
BORDER = 2          # пунктирная рамка вокруг заливки (заливка уже захватывает её часть), px кадра 1920


def main():
    p = project(sys.argv[1])
    pdf = pymupdf.open(sys.argv[2])
    out = p / "slides_hd"
    out.mkdir(exist_ok=True)
    for i, pg in enumerate(pdf, 1):
        z = W / pg.rect.width
        pg.get_pixmap(matrix=pymupdf.Matrix(z, z)).save(out / f"slide{i:02d}.png")
    print(f"Слайдов: {len(pdf)}, кадры: {out}")

    pg, z = pdf[0], W / pdf[0].rect.width
    label = [b for b in pg.get_text("blocks") if b[4].strip().startswith("ВИДЕО")]
    if not label:
        sys.exit("На первом слайде нет надписи «ВИДЕО» – окно задай в slides_plan.json вручную")
    x0, y0, x1, y1 = [v * z for v in label[0][:4]]
    sx, sy = int((x0 + x1) / 2), int(y0 - 200)          # точка заливки выше надписи
    im = np.array(Image.open(out / "slide01.png").convert("RGB")).astype(int)
    m = np.abs(im - im[sy, sx]).sum(2) < 14
    xl, xr = sx, sx
    while m[sy, xl - 1]:
        xl -= 1
    while m[sy, xr + 1]:
        xr += 1
    cx = xl + 40                                          # столбец у края заливки – там нет надписи
    yt, yb = sy, sy
    while m[yt - 1, cx]:
        yt -= 1
    while m[yb + 1, cx]:
        yb += 1
    w = xr - xl + 1 + 2 * BORDER
    h = round(w * 4 / 3)
    cy = (yt + yb) / 2
    win = [xl - BORDER, round(cy - h / 2), w, h]
    print(f"Окно под видео (заливка x {xl}–{xr}, y {yt}–{yb}): \"window\": {win}")
    print(f"Пропорции заливки {(xr - xl + 1) / (yb - yt + 1):.3f} (3:4 = 0.750)")


if __name__ == "__main__":
    main()
