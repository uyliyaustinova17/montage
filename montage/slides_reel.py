"""Видео автора в окне презентации: слайды меняются через затемнение по тексту речи.

    python3 tools/slides_reel.py <проект>              – собрать final.mp4
    python3 tools/slides_reel.py <проект> --check 12,95  – контрольные кадры на этих секундах готового ролика

Берёт projects/<имя>/slides_plan.json:
  slides   – папка с кадрами слайдов 1920x1080 (slide01.png …)
  crop     – [x, y, w, h] кроп 3:4 из source.mp4 (лицо целиком, без водяного знака)
  window   – [x, y, w, h] окно под видео на слайде (в пикселях слайда), radius – скругление
  fade     – сколько секунд уход в темноту и столько же выход из неё (смена слайда = 2 × fade)
  lead     – на сколько секунд раньше темнота наступает до первого слова новой темы
  scenes   – [{"slide": N, "at": секунда ИСХОДНИКА, где начинается тема}], первый слайд at = 0
Время сцен берётся из исходника и пересчитывается в время ролика через keep.json.
Картинка и звук собираются из source.mp4 за один проход (cut.mp4 не нужен): python3 tools/cut.py делает только cut.wav."""
import argparse
import json
import math
import subprocess

from PIL import Image, ImageDraw

from common import project, run

FPS = 30


def reel_time(keep, t):
    """Секунда исходника → секунда готового ролика (куски склеены встык, как в cut.py)."""
    acc = 0.0
    for a, b in keep:
        if t < a:
            return acc
        if t <= b:
            return acc + (t - a)
        acc += b - a
    return acc


def frame(x):
    return round(x * FPS) / FPS


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--check")
    ap.add_argument("--enc", choices=["x264", "qsv"], default="x264", help="кодирование: x264 (по умолчанию) или qsv (видеокарта Intel)")
    ap.add_argument("--preset", default="veryfast", help="пресет x264")
    ap.add_argument("--q", type=int, default=18, help="качество: crf для x264, global_quality для qsv (меньше = лучше)")
    ap.add_argument("--no-hw", dest="hw", action="store_false", help="не декодировать через QSV")
    a = ap.parse_args()
    p = project(a.name)
    plan = json.loads((p / "slides_plan.json").read_text(encoding="utf-8"))
    keep = json.loads((p / "keep.json").read_text(encoding="utf-8"))
    # длина ролика – сумма кадров кусков keep.json (они выровнены по кадрам), как у cut.wav
    total = sum(round(b * FPS) - round(a_ * FPS) for a_, b in keep) / FPS
    k = 1.0
    fade, lead = plan["fade"], plan["lead"]
    wx, wy, ww, wh = plan["window"]
    cx, cy, cw, ch = plan["crop"]

    sc = plan["scenes"]
    # центр затемнения перед каждым слайдом, кроме первого
    centers = [0.0] + [frame(max(reel_time(keep, s["at"]) * k - lead, 0)) for s in sc[1:]] + [total]
    for i in range(1, len(centers)):
        if centers[i] - centers[i - 1] < 2 * fade:
            raise SystemExit(f"слайд {sc[i - 1]['slide']} показывается меньше {2 * fade} с – проверь at в slides_plan.json")

    mask = Image.new("L", (ww * 2, wh * 2), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, ww * 2 - 1, wh * 2 - 1], radius=plan["radius"] * 2, fill=255)
    mask.resize((ww, wh), Image.LANCZOS).save(p / "slides_mask.png")

    if a.check:
        for t in [float(x) for x in a.check.split(",")]:
            run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t}", "-i", "final.mp4", "-frames:v", "1", "-vf", "scale=960:-2",
                 str(p / "check" / f"final_{t:g}.jpg")], cwd=p)
            print(p / "check" / f"final_{t:g}.jpg")
        return



    # картинка и звук – из source.mp4 за один проход: trim по кадрам, как в cut.py; кроп и сжатие до окна делаются ДО склейки
    src = p / "source.mp4"
    vcodec = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name",
                             "-of", "csv=p=0", str(src)], capture_output=True, text=True, check=True).stdout.strip()
    dec = ["-hwaccel", "qsv", "-c:v", f"{vcodec}_qsv"] if a.hw and vcodec in ("hevc", "h264") else []
    inputs, chains, labels = [], [], []
    for i, s in enumerate(sc):
        dur = frame(centers[i + 1] - centers[i])
        inputs += ["-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.4f}", "-i", str(p / plan["slides"] / f"slide{s['slide']:02d}.png")]
        f = [f"[{i}:v]format=yuv420p"]
        if i > 0:
            f.append(f"fade=t=in:st=0:d={fade}")
        if i < len(sc) - 1:
            f.append(f"fade=t=out:st={dur - fade:.4f}:d={fade}")
        chains.append(",".join(f) + f"[s{i}]")
        labels.append(f"[s{i}]")
    n = len(sc)
    inputs += [*dec, "-i", str(src), "-loop", "1", "-framerate", str(FPS), "-i", "slides_mask.png"]
    fc = ";".join(chains)
    fc += f";{''.join(labels)}concat=n={n}:v=1:a=0[bg]"
    fc += f";[{n}:v]{"hwdownload,format=nv12," if dec else ""}fps={FPS},crop={cw}:{ch}:{cx}:{cy},scale={ww}:{wh}:flags=lanczos,format=rgba[sv]"
    for j, (ka, kb) in enumerate(keep):
        fa, fb = round(ka * FPS), round(kb * FPS)
        fc += (f";[sv]trim=start_frame={fa}:end_frame={fb},setpts=PTS-STARTPTS[v{j}]"
               f";[{n}:a]atrim={ka}:{kb},asetpts=PTS-STARTPTS,afade=t=in:d=0.02,afade=t=out:st={kb - ka - 0.02:.4f}:d=0.02[a{j}]")
    fc += ";" + "".join(f"[v{j}][a{j}]" for j in range(len(keep))) + f"concat=n={len(keep)}:v=1:a=1[vc][ac]"
    fc += f";[{n + 1}:v]format=gray[m];[vc][m]alphamerge[vm]"
    fc += f";[bg][vm]overlay={wx}:{wy}:shortest=0:format=auto,format=yuv420p[out]"
    if a.enc == "qsv":
        venc = ["-c:v", "h264_qsv", "-preset", "slow", "-global_quality", str(a.q), "-look_ahead", "1"]
    else:
        venc = ["-c:v", "libx264", "-preset", a.preset, "-crf", str(a.q)]
    (p / "slides_filter.txt").write_text(fc, encoding="utf-8")   # граф длинный: в командную строку не помещается\n    run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex_script", "slides_filter.txt", "-map", "[out]", "-map", "[ac]",
         "-t", f"{total:.4f}", "-r", str(FPS), *venc, "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "final.mp4"], cwd=p)
    print("Центры смены слайдов (с ролика):", ", ".join(f"{c:.2f}" for c in centers[1:-1]))
    print(f"Готово: {p / 'final.mp4'}, {math.floor(total // 60)}:{total % 60:05.2f}")


if __name__ == "__main__":
    main()
