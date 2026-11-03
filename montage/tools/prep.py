"""Шаг 1. Исходник → папка проекта projects/<имя>/source.mp4.

    python3 tools/prep.py <видео> [ещё видео …] [--name имя]

Несколько файлов склеиваются по порядку (хук, начинка, призыв). Видео приводится к одному виду:
30 кадров/с, поворот применён, звук 48 кГц. iPhone HDR (HLG) переводится в обычный цвет через LUT –
иначе картинка выходит блёклой. Цвет обычного (SDR) видео не меняется."""
import argparse
import datetime
import re
from pathlib import Path

import numpy as np

from common import PROJECTS, fmt, probe, run, save, duration

FPS = 30


def hlg_lut(path, n=33, gain=3.4):
    """HLG BT.2020 → SDR BT.709: обратная HLG, матрица цветов, мягкое сжатие светов, гамма 2.2."""
    a, b, c = 0.17883277, 0.28466892, 0.55991073
    g = np.linspace(0, 1, n)
    B, G, R = np.meshgrid(g, g, g, indexing="ij")  # R меняется быстрее всех – так требует .cube
    e = np.stack([R, G, B], -1)
    lin = np.where(e <= 0.5, e * e / 3.0, (np.exp((e - c) / a) + b) / 12.0)
    M = np.array([[1.6605, -0.5876, -0.0728], [-0.1246, 1.1329, -0.0083], [-0.0182, -0.1006, 1.1187]])
    lin = np.clip(lin @ M.T, 0, None) * gain
    tm = lin * (1 + lin / (gain * gain)) / (1 + lin)
    out = np.clip(tm, 0, 1) ** (1 / 2.2)
    with open(path, "w") as f:
        f.write(f"LUT_3D_SIZE {n}\n")
        for row in out.reshape(-1, 3):
            f.write("%.5f %.5f %.5f\n" % tuple(row))


def info(path):
    j = probe(path)
    v = next(s for s in j["streams"] if s["codec_type"] == "video")
    a = any(s["codec_type"] == "audio" for s in j["streams"])
    return v, a


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("videos", nargs="+")
    ap.add_argument("--name")
    ap.add_argument("--copy", action="store_true",
                    help="не перекодировать (SDR, один файл): source.mp4 = оригинал как есть, минуты вместо получаса")
    args = ap.parse_args()
    srcs = [Path(v).expanduser().resolve() for v in args.videos]
    for s in srcs:
        if not s.exists():
            raise SystemExit(f"Нет файла: {s}")
    name = args.name or f"{datetime.date.today():%Y%m%d}_{re.sub(r'[^0-9A-Za-zА-Яа-яЁё_-]+', '_', srcs[0].stem)[:30]}"
    p = PROJECTS / name
    p.mkdir(parents=True, exist_ok=True)
    parts = []
    for i, s in enumerate(srcs):
        v, has_audio = info(s)
        if not has_audio:
            raise SystemExit(f"В {s.name} нет звука – монтаж по речи невозможен")
        hdr = v.get("color_transfer") in ("arib-std-b67", "smpte2084")
        vf = []
        if hdr:
            lut = p / "hlg2sdr.cube"
            if not lut.exists():
                hlg_lut(lut)
            vf.append("scale=in_color_matrix=bt2020:in_range=tv:out_range=pc,format=gbrp16le,"
                      f"lut3d={lut.name},scale=out_color_matrix=bt709:out_range=tv")
        if args.copy:
            if hdr or len(srcs) > 1:
                raise SystemExit("--copy только для одного SDR-файла: HDR и склейку нужно перекодировать (без --copy)")
            out = p / "source.mp4"
            run(["ffmpeg", "-v", "error", "-y", "-i", s, "-map", "0:v:0", "-map", "0:a:0", "-c", "copy", "-tag:v", "hvc1",
                 "-movflags", "+faststart", out.name], cwd=p)
            parts.append(out)
            print(f"{s.name}: {v['width']}×{v['height']}, обычный цвет, скопирован без перекодирования "
                  f"({v.get('r_frame_rate')} к/с – 30 к/с делает рендер)")
            continue
        vf += [f"fps={FPS}", "scale=trunc(iw/2)*2:trunc(ih/2)*2", "format=yuv420p"]
        out = p / (f"part{i}.mp4" if len(srcs) > 1 else "source.mp4")
        # поворот с телефона ffmpeg применяет сам (autorotate), теги цвета ставим явно – обычный BT.709
        run(["ffmpeg", "-v", "error", "-y", "-i", s, "-map", "0:v:0", "-map", "0:a:0", "-vf", ",".join(vf),
             "-c:v", "libx264", "-preset", "medium", "-crf", "14",
             "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
             "-c:a", "aac", "-b:a", "256k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", out.name], cwd=p)
        parts.append(out)
        print(f"{s.name}: {v['width']}×{v['height']}, {'HDR → обычный цвет' if hdr else 'обычный цвет, не трогаю'}")
    if len(parts) > 1:
        (p / "parts.txt").write_text("".join(f"file '{x.name}'\n" for x in parts))
        run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", "parts.txt", "-c", "copy", "source.mp4"], cwd=p)
    v, _ = info(p / "source.mp4")
    save(p, "project.json", {"sources": [str(s) for s in srcs], "width": v["width"], "height": v["height"], "fps": FPS})
    print(f"Проект: {p}\nsource.mp4 {v['width']}×{v['height']}, {fmt(duration(p / 'source.mp4'))}")
    if v["width"] > v["height"]:
        print("Видео горизонтальное: стиль ui рассчитан на вертикальное 9:16, лицо возьмём кропом.")


if __name__ == "__main__":
    main()
