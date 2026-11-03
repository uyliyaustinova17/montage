"""Разбор чужого ролика для копирования стиля: скачать, порезать на кадры, измерить ритм, цвета и звук.

    python3 tools/ref.py <ссылка или файл> --name <имя-стиля>
    python3 tools/ref.py --name <имя> --anim 3.2-4.4      – переход покадрово (30 кадров/с) – как именно анимируется

Выход в refs/<имя>/:
  original.mp4         – ролик;
  sheet_*.jpg          – листы кадров каждые 0,25 с с временем (смотреть ВСЕ);
  cuts/*.jpg           – кадр сразу после каждой смены сцены в полном размере;
  anim_*.jpg           – раскадровки переходов (по --anim);
  facts.md             – измеренное: размер, fps, длина, смены сцен и средняя длина кадра, громкость, цвета, текст речи.
Разбор и STYLE.md пишет Claude, глядя на листы, – по шаблону styles/_template/STYLE.md."""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from PIL import Image

from common import ROOT, duration, fmt, probe, run
from frames import sheet

REFS = ROOT / "refs"


def download(url, out):
    cookies = Path.home() / "scenarios" / "data" / "ig_cookies.txt"   # вход Instagram из системы сценариев
    cmd = [sys.executable, "-m", "yt_dlp", "-q", "--no-warnings", "-f", "bv*+ba/b", "--merge-output-format", "mp4",
           "-o", str(out), url]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode and cookies.exists():
        r = subprocess.run(cmd[:-1] + ["--cookies", str(cookies), url], capture_output=True, text=True)
    if r.returncode or not out.exists():
        sys.exit("Не скачалось: " + (r.stderr or r.stdout)[-300:] +
                 "\nСкачай ролик сам (например, через приложение или запись экрана) и передай путь к файлу.")


def scene_cuts(video, thr=0.3):
    r = subprocess.run(["ffmpeg", "-v", "info", "-i", str(video), "-vf", f"select='gt(scene,{thr})',showinfo", "-f", "null", "-"],
                       capture_output=True, text=True)
    return [float(m) for m in re.findall(r"pts_time:([\d.]+)", r.stderr)]


def palette(imgs, n=8):
    big = Image.new("RGB", (len(imgs) * 64, 114))
    for k, im in enumerate(imgs):
        big.paste(im.resize((64, 114)), (k * 64, 0))
    q = big.quantize(n, method=Image.Quantize.MEDIANCUT)
    pal, counts = q.getpalette()[:n * 3], sorted(q.getcolors(), reverse=True)
    return [("#%02X%02X%02X" % tuple(pal[i * 3:i * 3 + 3]), c / (big.width * big.height)) for c, i in counts]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src", nargs="?")
    ap.add_argument("--name", required=True)
    ap.add_argument("--anim")
    ap.add_argument("--step", type=float, default=0.25)
    a = ap.parse_args()
    d = REFS / a.name
    d.mkdir(parents=True, exist_ok=True)
    orig = d / "original.mp4"

    if a.anim:
        t0, t1 = [float(x) for x in a.anim.split("-")]
        tmp = d / "anim_tmp"
        tmp.mkdir(exist_ok=True)
        run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t0}", "-i", orig, "-t", f"{t1 - t0}", "-vf", "fps=30,scale=270:-2",
             str(tmp / "%03d.png")])
        fr = sorted(tmp.glob("*.png"))
        imgs = [Image.open(f).convert("RGB") for f in fr]
        out = sheet(imgs, [f"{t0 + k / 30:.2f}" for k in range(len(imgs))], 8, d / f"anim_{t0:g}-{t1:g}.jpg")
        for f in fr:
            f.unlink()
        tmp.rmdir()
        print(out)
        return

    if not orig.exists():
        if not a.src:
            sys.exit("Нужна ссылка или файл")
        if Path(a.src).expanduser().exists():
            run(["ffmpeg", "-v", "error", "-y", "-i", Path(a.src).expanduser(), "-c", "copy", orig])
        else:
            download(a.src, orig)
    j = probe(orig)
    v = next(s for s in j["streams"] if s["codec_type"] == "video")
    dur = duration(orig)

    # листы кадров
    fr = d / "frames"
    fr.mkdir(exist_ok=True)
    run(["ffmpeg", "-v", "error", "-y", "-i", orig, "-vf", f"fps={1 / a.step},scale=270:-2", str(fr / "%04d.jpg")])
    files = sorted(fr.glob("*.jpg"))
    for old in d.glob("sheet_*.jpg"):
        old.unlink()
    per = 24
    for s in range(0, len(files), per):
        part = files[s:s + per]
        sheet([Image.open(f).convert("RGB") for f in part], [f"{(s + k) * a.step:.2f} с" for k in range(len(part))], 8,
              d / f"sheet_{s // per}.jpg")

    # смены сцен
    cuts = scene_cuts(orig)
    cd = d / "cuts"
    cd.mkdir(exist_ok=True)
    for t in cuts:
        run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t + 0.05:.3f}", "-i", orig, "-frames:v", "1", str(cd / f"{t:07.2f}.jpg")])
    shots = [b - a_ for a_, b in zip([0] + cuts, cuts + [dur])]

    # громкость, цвета
    r = subprocess.run(["ffmpeg", "-v", "info", "-i", str(orig), "-af", "ebur128", "-f", "null", "-"], capture_output=True, text=True)
    lufs = re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)
    pal = palette([Image.open(f).convert("RGB") for f in files[::max(1, len(files) // 40)]])

    # речь (если есть ключ или локальный whisper)
    text = ""
    try:
        from speech import build, chunks, pick
        from common import audio
        x = audio(orig)
        segs, _, _ = chunks(x, 0.3, 7.0)
        ch = build(orig, segs, d / "cache", pick())
        text = "\n".join(f"{fmt(c['start'])}  {c['text']}" for c in ch)
        json.dump({"words": [w for c in ch for w in c["words"]]}, open(d / "words.json", "w"), ensure_ascii=False)
    except SystemExit as e:
        text = f"(речь не расшифрована: {e})"

    L = [f"# Факты о референсе «{a.name}»", "",
         f"- Размер {v['width']}×{v['height']}, {v['r_frame_rate']} кадров/с, длина {fmt(dur)}",
         f"- Смен сцен (порог 0,3): {len(cuts)}; средняя длина кадра {sum(shots) / len(shots):.2f} с; "
         f"смен в минуту {len(cuts) / dur * 60:.0f}",
         f"- Смены на: {', '.join(f'{t:.2f}' for t in cuts) or '—'}",
         f"- Громкость: {lufs[-1] if lufs else '?'} LUFS",
         "- Основные цвета кадра (доля): " + ", ".join(f"{c} {p:.0%}" for c, p in pal),
         f"- Листы кадров: {len(list(d.glob('sheet_*.jpg')))} шт. по {per} кадров, шаг {a.step} с; кадры после смен – cuts/",
         "", "## Речь", "", text, "",
         "Мелкие переходы и анимации смотреть покадрово: python3 tools/ref.py --name "
         f"{a.name} --anim <начало>-<конец>"]
    (d / "facts.md").write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L[:8]))
    print(f"Папка: {d}")


if __name__ == "__main__":
    main()
