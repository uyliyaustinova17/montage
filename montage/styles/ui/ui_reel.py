"""UI-монтаж: вертикальный рилс 1080×1920.

Сверху (y 0–1160) — живой интерфейс того, о чём речь; посередине (y 1186) — субтитры по 3 слова, текущее слово жёлтое;
внизу — лицо карточкой 920×620 (face: "card") или на всю ширину 1080×640 (face: "hero", интро). Правила – STYLE.md.

    python3 ui_reel.py plan.json                 – рендер
    python3 ui_reel.py plan.json --frames 1,5,9  – контрольные кадры в check/

plan.json: source, transcript (words: text/start/end, время исходника), keep или keep_file, card_crop [x,y,w,h], hero_crop [x,y,w,h],
fix {слово: замена}, out, jobs, scenes. Время "at" — секунды склейки или слово (первое вхождение от начала сцены).
Картинки: путь от папки плана или "src:12.5" — кадр склейки на 12,5 с (кроп "src:12.5:card|wide|tall").

Сцены (type): editor | phone | app | code | takes | grid | cards | prompt | cta | media | chart | flip.
Общие поля: from, face (card|hero|none), label {text, kind: dot|dotl|head}, word {text, at, out} — жёлтое слово поверх.
"""
import base64
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

# запуск из любого python: переключиться на окружение .venv папки montage (его создаёт install.py)
_VENV = Path(__file__).resolve().parents[2] / ".venv"
_VPY = _VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
if _VPY.exists() and Path(sys.prefix).resolve() != _VENV.resolve():
    sys.exit(subprocess.run([str(_VPY), *sys.argv]).returncode)

from patchright.sync_api import sync_playwright  # noqa: E402

KIT = Path(__file__).resolve().parent
W, H, FPS = 1080, 1920, 30
CARD = (80, 1268, 920, 620)
HERO = (0, 1280, 1080, 640)


def data_uri(path):
    p = Path(path)
    mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode()


def load_words(plan, root):
    d = json.load(open(root / plan["transcript"]))
    if "keep" not in plan:
        plan["keep"] = json.load(open(root / plan["keep_file"]))
    fix = plan.get("fix", {})
    words, off = [], 0.0
    for a, b in plan["keep"]:
        for w in d["words"]:
            if a <= w["start"] < b:
                t = w.get("text", w.get("word", "")).strip()
                words.append({"t": fix.get(t, t), "o": t, "s": round(w["start"] - a + off, 3), "e": round(min(w["end"], b) - a + off, 3)})
        off += b - a
    return words, off


def norm(s):
    return re.sub(r"[^\wё-]", "", s.lower())


def resolve(at, words, t0):
    if isinstance(at, (int, float)):
        return float(at)
    key, n = (at.split("#") + ["1"])[:2]
    key, n = norm(key), int(n)
    for w in words:
        if w["s"] >= t0 - 0.05 and (norm(w["t"]).startswith(key) or norm(w["o"]).startswith(key)):
            n -= 1
            if not n:
                return w["s"]
    raise ValueError(f"слово «{at}» не найдено после {t0:.2f}")


def to_src(plan, t):
    c = 0.0
    for a, b in plan["keep"]:
        if t <= c + b - a:
            return a + t - c
        c += b - a
    return plan["keep"][-1][1]


def grab(plan, root, spec):
    """'src:12.5[:card|wide|tall]' → кадр склейки в ui_assets/ (кэш)"""
    _, t, *crop = spec.split(":")
    crop = crop[0] if crop else "wide"
    out = root / "ui_assets" / f"src_{float(t):08.2f}_{crop}.jpg"
    if not out.exists():
        out.parent.mkdir(exist_ok=True)
        x, y, w, h = plan.get(f"{crop}_crop", plan.get("card_crop") if crop == "card" else [0, 0, -1, -1])
        vf = f"crop={w}:{h}:{x}:{y}," if w > 0 else ""
        if crop == "tall":   # 9:16 из горизонтального исходника
            vf = "crop=ih*9/16:ih,"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{to_src(plan, float(t)):.3f}", "-i", str(root / plan["source"]),
                        "-frames:v", "1", "-vf", vf + "scale=720:-2", "-q:v", "3", str(out)], check=True)
    return out


def prepare(plan, root, words, total):
    imgs, sizes = {}, {}

    def img(spec):
        if spec and spec not in imgs:
            p = grab(plan, root, spec) if spec.startswith("src:") else root / spec
            imgs[spec] = data_uri(p)
            from PIL import Image
            sizes[spec] = Image.open(p).size
        return spec

    def walk(o, t0):
        """все поля *at / out в сцене → секунды; все img/imgs → data uri"""
        if isinstance(o, dict):
            for k, v in list(o.items()):
                if k in ("from", "to"):
                    continue
                if (k == "at" or k.endswith("_at") or k == "out") and isinstance(v, (str, int, float)) and not isinstance(v, bool):
                    o[k] = resolve(v, words, t0)
                elif k in ("img", "avatar", "preview") and isinstance(v, str):
                    img(v)
                elif k in ("imgs", "thumbs", "seq", "video"):
                    for s in v:
                        img(s if isinstance(s, str) else s.get("img"))
                    walk(v, t0)
                else:
                    walk(v, t0)
        elif isinstance(o, list):
            for v in o:
                walk(v, t0)

    scenes = plan["scenes"]
    for sc in scenes:
        sc["from"] = resolve(sc["from"], words, 0)
    for i, sc in enumerate(scenes):
        sc["to"] = scenes[i + 1]["from"] if i + 1 < len(scenes) else total
        sc.setdefault("face", "card")
        walk(sc, sc["from"])
    return {"scenes": scenes, "imgs": imgs, "sizes": sizes, "words": words, "total": total, "card": CARD, "hero": HERO}


PAGE = r"""<!doctype html><html><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&family=PT+Serif:ital@0;1&display=swap" rel="stylesheet">
<style>
:root{--bg:#0A0A08;--p1:#151513;--p2:#1C1C1A;--ln:#2A2A27;--y:#FDE445;--t:#F3EFE6;--g:#8C8A84;--r:#E5484D}
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:1080px;height:1920px;overflow:hidden;background:var(--bg);font-family:Inter,sans-serif;color:var(--t)}
.scene{position:absolute;left:0;top:0;width:1080px;height:1160px;overflow:hidden;background:var(--bg)}
.ab{position:absolute}
.lab{position:absolute;left:24px;top:24px;font:700 40px Inter;letter-spacing:-.01em;display:flex;align-items:center;gap:12px;z-index:5}
.lab i{width:12px;height:12px;border-radius:50%;background:var(--y)}
.lab.head{font:800 46px Inter;color:var(--y);text-transform:uppercase;letter-spacing:-.01em}
.panel{position:absolute;background:var(--p1);border:2px solid var(--ln);border-radius:18px;overflow:hidden}
.panel img,.cover{width:100%;height:100%;object-fit:cover;display:block}
.word{position:absolute;font:800 150px Inter;color:var(--y);letter-spacing:-.03em;z-index:6;text-transform:uppercase}
#subs{position:absolute;left:0;width:1080px;top:1186px;text-align:center;font:700 44px Inter;letter-spacing:-.005em;white-space:nowrap}
#subs b{color:var(--y);font-weight:700}
.cursor{position:absolute;width:38px;height:48px;z-index:9;transform-origin:4px 4px}
/* editor */
.tl{position:absolute;left:26px;top:726px;width:1028px;height:368px;background:var(--p1);border:2px solid var(--ln);border-radius:16px}
.tl .ruler{position:absolute;left:150px;right:14px;top:10px;height:22px;font:500 15px 'JetBrains Mono';color:var(--g);display:flex;justify-content:space-between}
.tl .trk{position:absolute;left:150px;right:14px;height:62px;background:var(--p2);border-radius:8px;overflow:hidden;display:flex;gap:4px;padding:4px}
.tl .tn{position:absolute;left:18px;font:600 22px Inter;color:var(--t)}
.tl .th{height:100%;flex:1;border-radius:5px;object-fit:cover;min-width:0}
.tl .tb{height:100%;flex:1;border-radius:5px;background:var(--y);min-width:0}
.tl .ph{position:absolute;top:6px;bottom:6px;width:3px;background:var(--y)}
.tl .ph:before{content:'';position:absolute;left:-7px;top:-4px;border:8.5px solid transparent;border-top:11px solid var(--y)}
svg.links{position:absolute;left:0;top:0;width:1080px;height:1160px;pointer-events:none}
/* phone */
.phone{position:absolute;border-radius:34px;overflow:hidden;border:3px solid #34332F;background:#000}
.rec{position:absolute;width:64px;height:64px;border-radius:50%;border:4px solid #fff;display:flex;align-items:center;justify-content:center}
.rec:after{content:'';width:30px;height:30px;border-radius:8px;background:var(--y)}
.focus{position:absolute;border:2px solid var(--y);border-radius:6px}
/* app */
.win{position:absolute;background:var(--p1);border:2px solid var(--ln);border-radius:18px;overflow:hidden}
.chip{position:absolute;background:var(--p2);border:2px solid var(--ln);border-radius:14px;overflow:hidden;width:250px}
.chip img{width:100%;height:150px;object-fit:cover;display:block}
.chip div{font:500 19px 'JetBrains Mono';color:var(--t);padding:8px 12px;display:flex;justify-content:space-between}
.chip div span{color:var(--y)}
.wave{position:absolute;display:flex;align-items:center;gap:5px}
.wave i{width:6px;border-radius:3px;background:var(--y)}
/* code */
.code{position:absolute;left:16px;top:290px;width:1048px;background:var(--p1);border:2px solid var(--ln);border-radius:12px;overflow:hidden}
.code .tabs{height:40px;border-bottom:2px solid var(--ln);display:flex;gap:30px;padding:0 20px;align-items:center;font:500 18px 'JetBrains Mono';color:var(--g)}
.code .tabs b{color:var(--t);font-weight:500}
.code pre{padding:16px 0;font:400 30px/1.7 'JetBrains Mono',monospace;color:var(--t);position:relative}
.code .l{display:flex;padding-left:16px;white-space:pre}
.code .l em{width:52px;color:#5F5D57;font-style:normal;text-align:right;margin-right:24px;flex:none}
.code .hl{position:absolute;border:2px solid var(--y);border-radius:6px}
.code .y{color:var(--y)}
/* takes */
.take{position:absolute;border-radius:16px;overflow:hidden;border:3px solid transparent;background:#000}
.take img{width:100%;height:100%;object-fit:cover;display:block}
.take .tg{position:absolute;left:14px;bottom:12px;font:600 22px Inter;color:#fff;text-shadow:0 1px 4px #000}
.take .ok{position:absolute;right:14px;bottom:10px;font:800 28px Inter;color:var(--y)}
.take .red{position:absolute;inset:0;background:rgba(229,72,77,.42)}
.strip{position:absolute;left:30px;width:1020px;height:200px;background:var(--p1);border-radius:12px;overflow:hidden;border:2px solid var(--ln)}
.strip .fr{position:absolute;top:28px;bottom:0;display:flex}
.strip .fr img{height:100%;width:170px;object-fit:cover;opacity:.75}
.strip .wv{position:absolute;left:0;right:0;bottom:12px;height:90px;display:flex;align-items:center;gap:3px;padding:0 8px}
.strip .wv i{flex:1;background:#fff;border-radius:2px;opacity:.9}
.strip .ru{position:absolute;left:10px;right:10px;top:4px;font:500 15px 'JetBrains Mono';color:var(--g);display:flex;justify-content:space-between}
.pz{position:absolute;top:28px;bottom:0;border:3px solid var(--y);background:rgba(253,228,69,.18);border-radius:6px}
/* grid */
.gi{position:absolute;border-radius:10px;overflow:hidden;border:2.5px solid transparent;background:#000}
.gi img{width:100%;height:100%;object-fit:cover}
.scan{position:absolute;left:0;width:1080px;height:3px;background:var(--y)}
/* cards */
.cd{position:absolute;background:var(--p1);border:2px solid var(--ln);border-radius:20px;overflow:hidden}
.cd .cl{position:absolute;left:22px;top:18px;font:700 22px Inter;text-transform:uppercase;letter-spacing:.02em;color:var(--t);z-index:2}
.cd .cc{position:absolute;left:22px;right:22px;top:62px;bottom:22px;border-radius:12px;overflow:hidden;display:flex;gap:16px;align-items:stretch;justify-content:center}
.cd .cc img{width:100%;height:100%;object-fit:cover}
.cd .sw{flex:1;border-radius:12px}
.cd .tx{font:400 54px 'PT Serif',serif;display:flex;align-items:center;justify-content:center;text-align:center;line-height:1.1;width:100%}
/* prompt */
.prompt{position:absolute;left:52px;top:70px;width:976px;font:500 40px/1.5 Inter;color:var(--t)}
.prompt b{color:var(--y);font-weight:700;padding:0 4px;margin:0 -4px;border-radius:4px}
/* pops – элементы поверх любой сцены */
.pop{position:absolute;z-index:7;white-space:nowrap}
.pop.box{border:4px solid var(--y);border-radius:12px}
.pop.pill{background:var(--p2);border:2px solid var(--ln);border-radius:999px;padding:14px 30px;font:600 34px Inter;color:var(--t)}
.pop.badge{background:var(--y);color:#111;border-radius:999px;padding:14px 30px;font:800 34px Inter}
.pop.check,.pop.x{width:72px;height:72px;border-radius:50%;display:flex;align-items:center;justify-content:center;font:800 40px Inter}
.pop.check{background:var(--y);color:#111}.pop.x{background:var(--r);color:#fff}
/* chart */
.chart{position:absolute;background:var(--p1);border:2px solid var(--ln);border-radius:18px;overflow:hidden}
.mk{position:absolute;z-index:3;transform:translate(-50%,-50%)}
.mk i{display:block;width:30px;height:30px;border-radius:50%;background:var(--y);border:5px solid var(--bg);margin:0 auto}
.mk.swipe i{background:var(--r)}
.mk span{position:absolute;left:50%;bottom:44px;transform:translateX(-50%);background:var(--p2);border:2px solid var(--ln);border-radius:999px;padding:8px 20px;font:700 28px Inter;white-space:nowrap}
.mk.swipe span{color:#fff;border-color:var(--r)}
.mk.share span{color:var(--y)}
.vstrip{position:absolute;left:0;right:0;bottom:0;height:150px;display:flex;border-top:2px solid var(--ln)}
.vstrip img{flex:1;min-width:0;object-fit:cover;opacity:.8}
.vph{position:absolute;bottom:0;height:150px;width:4px;background:var(--y)}
/* flip */
.fcap{position:absolute;font:700 30px Inter;color:var(--t);text-transform:uppercase;letter-spacing:.02em}
.gck{position:absolute;right:8px;top:8px;width:46px;height:46px;border-radius:50%;background:var(--y);color:#111;font:800 28px Inter;display:flex;align-items:center;justify-content:center;opacity:0}
/* cta */
.cmt{position:absolute;left:72px;top:580px;width:936px;height:104px;background:var(--p2);border:2px solid var(--ln);border-radius:52px;display:flex;align-items:center;padding:0 16px 0 36px;font:500 38px Inter;color:var(--t)}
.cmt .ph{color:#6E6C66}.cmt .car{display:inline-block;width:3px;height:42px;background:var(--y);margin-left:4px;vertical-align:-8px}
.cmt .snd{margin-left:auto;width:72px;height:72px;border-radius:50%;background:#2E2D2A;color:#111;display:flex;align-items:center;justify-content:center;font:800 34px Inter}
.ctat{position:absolute;left:72px;top:70px;font:400 88px/1.02 'PT Serif',serif;color:var(--t)}
.ctat em{color:var(--y);font-style:normal}
.prof{position:absolute;left:72px;top:290px;width:936px;height:256px;background:#0F1216;border:2px solid #20252B;border-radius:18px;display:flex;gap:40px;align-items:center;padding:0 40px}
.prof img{width:180px;height:180px;border-radius:50%;object-fit:cover;flex:none}
.prof h4{font:700 34px Inter;margin-bottom:14px}
.prof p{font:500 22px/1.55 Inter;color:#C9C6BE}
.prof p.lk{color:#7FA3FF}
.ctas{position:absolute;left:72px;top:724px;font:400 38px Inter;color:var(--t)}
.btn{position:absolute;left:72px;top:812px;width:936px;height:114px;background:var(--y);border-radius:14px;display:flex;align-items:center;justify-content:space-between;padding:0 36px;font:700 42px Inter;color:#111}
.cth{position:absolute;left:72px;top:960px;font:400 28px Inter;color:var(--g)}
/* media */
.cap{position:absolute;left:0;width:1080px;text-align:center;font:400 52px 'PT Serif',serif;color:#fff;text-shadow:0 2px 10px #000}
.cap em{font-style:italic}
</style></head><body><div id="stage"></div><div id="subs"></div>
<script>
const D = __DATA__;
const cl = x => Math.min(Math.max(x, 0), 1);
const ease = x => 1 - Math.pow(1 - cl(x), 3);
const io = x => { x = cl(x); return x < .5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2; };
const back = x => { x = cl(x); const c = 1.6; return 1 + (c + 1) * Math.pow(x - 1, 3) + c * Math.pow(x - 1, 2); };
const md = s => (s || '').replace(/\*(.+?)\*/g, '<em>$1</em>').replace(/\{(.+?)\}/g, '<b>$1</b>');
const I = s => D.imgs[s] || '';
const stage = document.getElementById('stage'), subs = document.getElementById('subs');
const CUR = `<svg class="cursor" viewBox="0 0 38 48"><path d="M3 3 L3 38 L12 30 L18 44 L24 41 L18 28 L30 28 Z" fill="#fff" stroke="#000" stroke-width="2.5" stroke-linejoin="round"/></svg>`;
// появление: fade + сдвиг; dir: up|right|left|down|pop
function show(e, t, at, dir = 'up', dur = .35, dist = 34, out) {
  if (!e) return;
  const on = t >= at && (out === undefined || t < out);
  e.style.visibility = on ? 'visible' : 'hidden'; if (!on) return;
  const k = ease((t - at) / dur);
  let tr = '';
  if (dir === 'up') tr = `translateY(${(1 - k) * dist}px)`;
  if (dir === 'down') tr = `translateY(${-(1 - k) * dist}px)`;
  if (dir === 'right') tr = `translateX(${(1 - k) * dist * 4}px)`;
  if (dir === 'left') tr = `translateX(${-(1 - k) * dist * 4}px)`;
  if (dir === 'pop') tr = `scale(${0.85 + 0.15 * back((t - at) / dur)})`;
  e.style.opacity = k; e.style.transform = (e.dataset.base || '') + ' ' + tr;
}
const $ = id => document.getElementById(id);
function at(v, sc, d = 0) { return v === undefined ? sc.from + d : v; }

const B = {};   // постройка сцен
B.editor = (sc, si) => {
  let h = `<svg class="links" id="lk${si}"></svg>`;
  const pv = sc.preview_box || [354, 80, 372, 622];
  h += `<div class="panel" style="left:${pv[0]}px;top:${pv[1]}px;width:${pv[2]}px;height:${pv[3]}px" id="pv${si}">` +
    (sc.previews || []).map((p, i) => `<img class="ab cover" id="pvi${si}_${i}" src="${I(p.img)}" style="left:0;top:0">`).join('') + '</div>';
  (sc.panels || []).forEach((p, i) => h += `<div class="panel" id="pn${si}_${i}" style="left:${p.x}px;top:${p.y}px;width:${p.w}px;height:${p.h}px"><img src="${I(p.img)}"></div>`);
  const tr = sc.tracks || ['Видео', 'Визуал', 'Текст', 'Движение'];
  h += `<div class="tl"><div class="ruler">${['00:00', '04:00', '08:00', '12:00', '16:00', '20:00', '24:00', '28:00', '32:00'].map(x => `<span>${x}</span>`).join('')}</div>`;
  tr.forEach((n, i) => {
    const y = 44 + i * 78;
    h += `<div class="tn" style="top:${y + 18}px">${n}</div><div class="trk" style="top:${y}px" id="tk${si}_${i}">`;
    if (i === 0) h += (sc.thumbs || []).map(s => `<img class="th" src="${I(s)}">`).join('');
    if (i === 1) h += (sc.visual || []).map((v, k) => `<img class="th" id="vs${si}_${k}" src="${I(v.img)}" style="flex:${v.w || 1}">`).join('');
    if (i === 2) h += Array.from({ length: sc.blocks || 12 }, (_, k) => `<div class="tb" id="tb${si}_${k}"></div>`).join('');
    if (i === 3) h += `<svg width="870" height="54" viewBox="0 0 870 54"><path id="mv${si}" d="M0 44 C60 44 70 12 130 12 S200 44 260 44 S330 12 390 12 S460 44 520 44 S590 12 650 12 S720 44 780 44 S840 20 870 20" fill="none" stroke="#FDE445" stroke-width="3" stroke-dasharray="1200" stroke-dashoffset="1200"/></svg>`;
    h += '</div>';
  });
  h += `<div class="ph" id="ph${si}"></div></div>` + CUR.replace('class="cursor"', `class="cursor" id="cu${si}"`);
  return h;
};
B.phone = (sc, si) => {
  const f = sc.box || [100, 140, 488, 866];
  let h = `<div class="phone" id="ph${si}" style="left:${f[0]}px;top:${f[1]}px;width:${f[2]}px;height:${f[3]}px"><img class="cover" src="${I(sc.img)}">` +
    `<div class="rec" style="left:${f[2] / 2 - 32}px;top:${f[3] - 100}px"></div>` +
    (sc.focus ? `<div class="focus" id="fo${si}" style="left:${sc.focus[0]}px;top:${sc.focus[1]}px;width:${sc.focus[2]}px;height:${sc.focus[3]}px"></div>` : '') + '</div>';
  (sc.side || []).forEach((s, i) => h += `<div class="panel" id="sd${si}_${i}" data-base="rotate(${s.rot ?? (i % 2 ? 4 : -5)}deg)" style="left:${s.x ?? 640}px;top:${s.y ?? 170 + i * 260}px;width:${s.w ?? 260}px;height:${s.h ?? 250}px;border-radius:16px"><img src="${I(s.img)}"></div>`);
  return h;
};
B.app = (sc, si) => {
  const f = sc.box || [30, 110, 1020, 760];
  // view [x,y,w] – какую область скриншота (в его пикселях) вписать по ширине окна; view_to – куда медленно наехать
  let h = `<div class="win" id="wn${si}" style="left:${f[0]}px;top:${f[1]}px;width:${f[2]}px;height:${f[3]}px">` +
    (sc.view ? `<img id="wi${si}" class="ab" src="${I(sc.img)}">` : `<img class="cover" style="object-fit:${sc.fit || 'cover'};object-position:top" src="${I(sc.img)}">`) + '</div>';
  (sc.chips || []).forEach((c, i) => h += `<div class="chip" id="ch${si}_${i}" style="left:${c.x}px;top:${c.y}px"><img src="${I(c.img)}"><div>${c.name || 'presenter.mp4'}<span>✓</span></div></div>`);
  if (sc.wave) h += `<div class="wave" id="wv${si}" style="left:${sc.wave.x ?? 40}px;top:${sc.wave.y ?? 930}px">` +
    Array.from({ length: sc.wave.n || 60 }, (_, k) => `<i style="height:${8 + Math.abs(Math.sin(k * 1.7) * Math.cos(k * .6)) * 70}px"></i>`).join('') + '</div>';
  return h + CUR.replace('class="cursor"', `class="cursor" id="cu${si}"`);
};
B.code = (sc, si) => {
  let h = '';
  if (sc.attach) h += `<div class="chip" id="at${si}" style="left:${sc.attach.x ?? 415}px;top:${sc.attach.y ?? 20}px"><img src="${I(sc.attach.img)}"><div>${sc.attach.name || 'presenter.mp4'}<span>✓</span></div></div><div class="ab" id="atl${si}" style="left:${(sc.attach.x ?? 415) + 124}px;top:${(sc.attach.y ?? 20) + 196}px;width:2px;height:${(sc.y ?? 290) - (sc.attach.y ?? 20) - 196}px;background:#5F5D57"></div>`;
  h += `<div class="code" id="cb${si}" style="top:${sc.y ?? 290}px"><div class="tabs"><b>Code</b><span>${sc.tab || 'Catalog'}</span></div><pre>` +
    sc.lines.map((l, i) => `<div class="l" id="cl${si}_${i}"><em>${i + 1}</em><span class="tx">${md(l).replace(/<b>/g, '<span class="y">').replace(/<\/b>/g, '</span>')}</span></div>`).join('') +
    `<div class="hl" id="hl${si}"></div></pre></div>`;
  return h;
};
B.takes = (sc, si) => {
  let h = `<div class="lab head" id="hd${si}"></div>`;
  const n = sc.imgs.length, w = sc.card_w || 300, gap = (1080 - n * w) / (n + 1);
  sc.imgs.forEach((s, i) => h += `<div class="take" id="tk${si}_${i}" style="left:${gap + i * (w + gap)}px;top:120px;width:${w}px;height:${w * 1.85}px"><img src="${I(s)}"><div class="red" id="rd${si}_${i}" style="opacity:0"></div><div class="tg">${(sc.names || [])[i] || 'Дубль ' + (i + 1)}</div><div class="ok" id="ok${si}_${i}" style="opacity:0">✓</div></div>`);
  if (sc.strip) {
    h += `<div class="strip" id="sp${si}" style="top:${sc.strip.y ?? 840}px"><div class="ru"><span>01:12</span><span>01:18</span><span>01:24</span><span>01:30</span><span>01:36</span></div><div class="fr">` +
      (sc.strip.imgs || sc.imgs).concat(sc.strip.imgs || sc.imgs).slice(0, 6).map(s => `<img src="${I(s)}">`).join('') + '</div><div class="wv">' +
      Array.from({ length: 150 }, (_, k) => `<i style="height:${(k % 37 < 5 ? 6 : 20 + Math.abs(Math.sin(k * 1.3) * Math.sin(k * .37)) * 70)}%"></i>`).join('') + '</div>' +
      (sc.strip.pauses || []).map((p, k) => `<div class="pz" id="pz${si}_${k}" style="left:${p[0]}px;width:${p[1]}px"></div>`).join('') + '</div>';
  }
  return h;
};
B.grid = (sc, si) => {
  const cols = sc.cols || 6, gw = 1080 - 40, cw = (gw - (cols - 1) * 14) / cols, ch = cw * 1.78;
  let h = '';
  sc.imgs.forEach((s, i) => { const c = i % cols, r = Math.floor(i / cols);
    h += `<div class="gi" id="gi${si}_${i}" style="left:${20 + c * (cw + 14)}px;top:${(sc.y ?? 20) + r * (ch + 14)}px;width:${cw}px;height:${ch}px"><img src="${I(s)}"><i class="gck" id="gk${si}_${i}">✓</i></div>`; });
  return h + `<div class="scan" id="sn${si}"></div>`;
};
B.cards = (sc, si) => {
  let h = '';
  sc.items.forEach((c, i) => {
    const x = 24 + (i % 2) * 528, y = (sc.y ?? 110) + Math.floor(i / 2) * 520;
    let inner = '';
    if (c.img) inner = `<img src="${I(c.img)}">`;
    if (c.swatches) inner = c.swatches.map(s => `<div class="sw" style="background:${s}"></div>`).join('');
    if (c.text) inner = `<div class="tx">${md(c.text)}</div>`;
    h += `<div class="cd" id="cd${si}_${i}" style="left:${x}px;top:${y}px;width:504px;height:496px"><div class="cl">${c.label}</div><div class="cc">${inner}</div></div>`;
  });
  return h;
};
// chart: кривая удержания поверх ленты кадров, метки по словам (без цифр – только форма и подписи)
B.chart = (sc, si) => {
  const f = sc.box || [24, 110, 1032, 760], cw = f[2], chh = f[3] - 150;
  const P = sc.points || [[0, .95], [.1, .8], [.22, .7], [.38, .64], [.52, .6], [.62, .57], [.7, .4], [.84, .34], [1, .3]];
  const xy = P.map(([x, y]) => [x * (cw - 60) + 30, (1 - y) * (chh - 60) + 40]);
  let d = `M${xy[0][0]} ${xy[0][1]}`; for (let i = 1; i < xy.length; i++) { const [x0, y0] = xy[i - 1], [x1, y1] = xy[i], mx = (x0 + x1) / 2; d += ` C${mx} ${y0} ${mx} ${y1} ${x1} ${y1}`; }
  sc._xy = xy;
  let h = `<div class="chart" id="cr${si}" style="left:${f[0]}px;top:${f[1]}px;width:${cw}px;height:${f[3]}px"><svg width="${cw}" height="${chh}" style="position:absolute;left:0;top:0">` +
    [1, 2, 3].map(k => `<line x1="0" x2="${cw}" y1="${k * chh / 4}" y2="${k * chh / 4}" stroke="#262624" stroke-width="2"/>`).join('') +
    `<path id="ca${si}" d="${d} L${xy[xy.length - 1][0]} ${chh} L${xy[0][0]} ${chh} Z" fill="rgba(253,228,69,.10)"/>` +
    `<path id="cp${si}" d="${d}" fill="none" stroke="#FDE445" stroke-width="7" stroke-linecap="round"/></svg>` +
    `<div class="vstrip">${(sc.thumbs || []).map(s => `<img src="${I(s)}">`).join('')}</div><div class="vph" id="vp${si}"></div>` +
    (sc.marks || []).map((m, i) => `<div class="mk ${m.kind || ''}" id="mk${si}_${i}"><span>${m.label}</span><i></i></div>`).join('') + '</div>';
  return h;
};
// flip: слева «играющее» видео (кадры склейки), справа покадровая анимация (например, мозг), между ними жёлтая стрелка
B.flip = (sc, si) => {
  const L = sc.left_box || [24, 170, 420, 746], Rb = sc.right_box || [490, 300, 566, 415];
  return `<div class="panel" id="fl${si}" style="left:${L[0]}px;top:${L[1]}px;width:${L[2]}px;height:${L[3]}px">` +
    (sc.video || []).map((s, i) => `<img class="ab cover" id="fv${si}_${i}" src="${I(s)}" style="left:0;top:0;opacity:0">`).join('') + '</div>' +
    `<div class="panel" id="fr${si}" style="left:${Rb[0]}px;top:${Rb[1]}px;width:${Rb[2]}px;height:${Rb[3]}px;background:#111">` +
    (sc.seq || []).map((s, i) => `<img class="ab" id="fs${si}_${i}" src="${I(s)}" style="left:0;top:0;width:100%;height:100%;object-fit:contain;opacity:0">`).join('') + '</div>' +
    `<svg class="links"><path id="fa${si}" d="M${L[0] + L[2] + 8} ${L[1] + L[3] / 2} C${Rb[0] - 10} ${L[1] + L[3] / 2} ${Rb[0] - 40} ${Rb[1] + Rb[3] / 2} ${Rb[0] - 8} ${Rb[1] + Rb[3] / 2}" fill="none" stroke="#FDE445" stroke-width="4" stroke-dasharray="400" stroke-dashoffset="400"/></svg>` +
    (sc.cap_left ? `<div class="fcap" id="fcl${si}" style="left:${L[0]}px;top:${L[1] + L[3] + 18}px">${sc.cap_left}</div>` : '') +
    (sc.cap_right ? `<div class="fcap" id="fcr${si}" style="left:${Rb[0]}px;top:${Rb[1] + Rb[3] + 18}px">${sc.cap_right}</div>` : '');
};
B.prompt = (sc, si) => `<div class="prompt" id="pr${si}">${md(sc.text)}</div>`;
B.cta = (sc, si) => `<div class="ctat" id="ct${si}">${md(sc.title)}</div>` +
  `<div class="prof" id="pf${si}"><img src="${I(sc.avatar)}"><div><h4>${sc.name}</h4>${(sc.bio || []).map((b, i) => `<p class="${i === sc.bio.length - 1 && sc.link ? 'lk' : ''}">${b}</p>`).join('')}</div></div>` +
  (sc.comment ? `<div class="cmt" id="cm${si}"><span id="cmt${si}"></span><div class="snd" id="snd${si}">↑</div></div>` : '') +
  `<div class="ctas" id="cs${si}">${sc.sub || ''}</div><div class="btn" id="bt${si}"><span>${sc.button || 'Подписаться'}</span><span>↗</span></div><div class="cth" id="hn${si}">${sc.handle || ''}</div>`;
B.media = (sc, si) => {
  const f = sc.box || [240, 30, 600, 1066];
  return `<div class="panel" id="md${si}" style="left:${f[0]}px;top:${f[1]}px;width:${f[2]}px;height:${f[3]}px;border:0;border-radius:6px"><img src="${I(sc.img)}"></div>` +
    (sc.caption ? `<div class="cap" id="mc${si}" style="top:${sc.cap_y ?? 560}px">${md(sc.caption)}</div>` : '');
};

D.scenes.forEach((sc, si) => {
  const el = document.createElement('div');
  el.className = 'scene'; el.id = 'sc' + si; el.style.zIndex = si;
  let h = (B[sc.type] || (() => ''))(sc, si);
  if (sc.label) h += `<div class="lab ${sc.label.kind === 'head' ? 'head' : ''}" id="lb${si}">${sc.label.kind === 'dotl' ? '<i></i>' : ''}${sc.label.text}${sc.label.kind === 'dot' ? '<i></i>' : ''}</div>`;
  (sc.pops || []).forEach((p, i) => h += `<div class="pop ${p.kind || 'pill'}" id="po${si}_${i}" style="left:${p.x}px;top:${p.y}px;${p.w ? 'width:' + p.w + 'px;' : ''}${p.h ? 'height:' + p.h + 'px;' : ''}">${p.text || (p.kind === 'check' ? '✓' : p.kind === 'x' ? '✕' : '')}</div>`);
  if (sc.word) h += `<div class="word" id="wd${si}" style="left:${sc.word.x ?? 560}px;top:${sc.word.y ?? 380}px">${sc.word.text}</div>`;
  el.innerHTML = h; stage.appendChild(el);
});
// связи editor: кривые от боковых панелей к превью
D.scenes.forEach((sc, si) => {
  if (sc.type !== 'editor') return;
  const pv = sc.preview_box || [354, 80, 372, 622];
  $('lk' + si).innerHTML = (sc.panels || []).map((p, i) => {
    const left = p.x < pv[0], x1 = left ? p.x + p.w : p.x, y1 = p.y + p.h / 2, x2 = left ? pv[0] : pv[0] + pv[2], y2 = pv[1] + pv[3] * (0.3 + 0.4 * (i % 2));
    const mx = (x1 + x2) / 2;
    return `<path id="ln${si}_${i}" d="M${x1} ${y1} C${mx} ${y1} ${mx} ${y2} ${x2} ${y2}" fill="none" stroke="#FDE445" stroke-width="3" stroke-dasharray="600" stroke-dashoffset="600"/>`;
  }).join('');
});

function cursor(e, t, path) {   // path: [{x,y,at,click}]
  if (!e || !path || !path.length) { if (e) e.style.display = 'none'; return; }
  e.style.display = t >= path[0].at ? 'block' : 'none';
  let x = path[0].x, y = path[0].y, s = 1;
  for (let i = 1; i < path.length; i++) {
    const a = path[i - 1], b = path[i], k = io((t - a.at) / Math.max(b.at - a.at, .01));
    if (t >= a.at) { x = a.x + (b.x - a.x) * k; y = a.y + (b.y - a.y) * k; }
  }
  path.forEach(p => { if (p.click && t >= p.at && t < p.at + .25) s = 0.8; });
  e.style.left = x + 'px'; e.style.top = y + 'px'; e.style.transform = `scale(${s})`;
}

const U = {};   // анимации сцен
U.editor = (sc, si, t, lt, dur) => {
  (sc.previews || []).forEach((p, i) => { const e = $(`pvi${si}_${i}`); e.style.opacity = t >= at(p.at, sc) ? ease((t - at(p.at, sc)) / .25) : 0; });
  (sc.panels || []).forEach((p, i) => {
    show($(`pn${si}_${i}`), t, at(p.at, sc, .05 * i), p.x < 354 ? 'left' : 'right', .4, 12);
    const ln = $(`ln${si}_${i}`); if (ln) ln.setAttribute('stroke-dashoffset', 600 * (1 - ease((t - at(p.link_at, sc, .4 + .12 * i)) / .5)));
  });
  (sc.visual || []).forEach((v, k) => show($(`vs${si}_${k}`), t, at(v.at, sc, .3 + .25 * k), 'pop', .25));
  const n = sc.blocks || 12, ta = at(sc.text_at, sc, .5), step = sc.text_step || .15;
  for (let k = 0; k < n; k++) show($(`tb${si}_${k}`), t, ta + k * step, 'pop', .2);
  const mv = $('mv' + si); if (mv) mv.setAttribute('stroke-dashoffset', 1200 * (1 - cl((t - at(sc.motion_at, sc, 1)) / 1.5)));
  const p0 = sc.play_from ?? 0, p1 = sc.play_to ?? 1;
  $('ph' + si).style.left = (150 + (p0 + (p1 - p0) * cl(lt / dur)) * 862) + 'px';
  cursor($('cu' + si), t, sc.cursor);
};
U.phone = (sc, si, t, lt, dur) => {
  const ph = $('ph' + si); ph.style.transform = `scale(${1 + .03 * cl(lt / dur)})`;
  (sc.side || []).forEach((s, i) => show($(`sd${si}_${i}`), t, at(s.at, sc, .3 + .2 * i), 'right', .4, 30));
  const fo = $('fo' + si); if (fo) show(fo, t, at(sc.focus_at, sc, .2), 'pop', .3);
};
U.app = (sc, si, t, lt, dur) => {
  const w = $('wn' + si), f = sc.box || [30, 110, 1020, 760];
  if (sc.view) { const v0 = sc.view, v1 = sc.view_to || v0, k = io(lt / dur), v = v0.map((a, i) => a + (v1[i] - a) * k);
    const sz = D.sizes[sc.img], sk = f[2] / v[2], im = $('wi' + si);
    im.style.width = sz[0] * sk + 'px'; im.style.left = -v[0] * sk + 'px'; im.style.top = -v[1] * sk + 'px'; }
  else w.style.transform = `scale(${1 + .025 * cl(lt / dur)})`;
  (sc.chips || []).forEach((c, i) => {
    const e = $(`ch${si}_${i}`), a = at(c.at, sc, .6);
    if (!c.from_xy) return show(e, t, a, c.dir || 'up', .35, 40, c.out);
    e.style.visibility = t >= a - .3 ? 'visible' : 'hidden'; const k = io((t - a) / (c.dur || .7));
    e.style.left = (c.from_xy[0] + (c.x - c.from_xy[0]) * k) + 'px'; e.style.top = (c.from_xy[1] + (c.y - c.from_xy[1]) * k) + 'px';
    e.style.transform = `rotate(${(1 - k) * -6}deg) scale(${1.05 - .05 * k})`; e.style.opacity = ease((t - a + .3) / .3);
  });
  if (sc.wave) { const e = $('wv' + si); show(e, t, at(sc.wave.at, sc, .4), 'up', .3);
    [...e.children].forEach((b, k) => b.style.opacity = t > at(sc.wave.at, sc, .4) + k * .015 ? 1 : .15); }
  cursor($('cu' + si), t, sc.cursor);
};
U.code = (sc, si, t) => {
  show($('cb' + si), t, sc.from, 'up', .35, 30);
  if (sc.cps && sc.type_at !== undefined) {   // печать по символам, строка за строкой
    let n = Math.floor((t - sc.type_at) * sc.cps);
    sc.lines.forEach((l, i) => { const e = $(`cl${si}_${i}`), tx = e.querySelector('.tx'), plain = l.replace(/[*{}]/g, '');
      if (!e.dataset.full) e.dataset.full = tx.innerHTML;
      e.style.opacity = n >= 0 ? 1 : 0;
      if (n >= plain.length) { if (tx.innerHTML !== e.dataset.full) tx.innerHTML = e.dataset.full; }
      else tx.innerHTML = n > 0 ? plain.slice(0, n) + '<span style="display:inline-block;width:14px;height:30px;background:#FDE445;vertical-align:-5px"></span>' : '';
      n -= plain.length + 3; });
  } else
  sc.lines.forEach((l, i) => { const e = $(`cl${si}_${i}`); e.style.opacity = sc.type_at !== undefined ? (t >= sc.type_at + i * (sc.type_step || .08) ? 1 : 0) : 1; });
  if (sc.attach) { show($('at' + si), t, at(sc.attach.at, sc, .2), 'down', .35, 50); show($('atl' + si), t, at(sc.attach.at, sc, .2) + .3, 'up', .2, 0); }
  const hl = $('hl' + si); let cur = null;
  (sc.hl || []).forEach(h => { if (t >= h.at) cur = h; });
  hl.style.display = cur ? 'block' : 'none';
  if (cur) { const r = $(`cl${si}_${cur.line - 1}`).querySelector('.tx');
    hl.style.top = (r.offsetTop + 2) + 'px'; hl.style.height = (r.offsetHeight - 4) + 'px';
    hl.style.left = (r.offsetLeft - 10) + 'px'; hl.style.width = (r.offsetWidth + 20) + 'px';
    hl.style.opacity = ease((t - cur.at) / .2); }
};
U.takes = (sc, si, t) => {
  let txt = ''; (sc.heads || []).forEach(h => { if (t >= at(h.at, sc)) txt = h.text; });
  const hd = $('hd' + si); if (hd.textContent !== txt) hd.textContent = txt;
  const n = sc.imgs.length, w = sc.card_w || 300, gap = (1080 - n * w) / (n + 1);
  sc.imgs.forEach((s, i) => {
    const e = $(`tk${si}_${i}`); show(e, t, at((sc.in_at || [])[i], sc, .1 * i), 'up', .35, 40);
    const bad = (sc.bad || []).includes(i), good = sc.good === i;
    const ba = at(sc.bad_at, sc, 99), ga = at(sc.good_at, sc, 99);
    $(`rd${si}_${i}`).style.opacity = bad && t >= ba ? ease((t - ba) / .25) : 0;
    e.style.borderColor = bad && t >= ba && t < ga ? '#E5484D' : (good && t >= ga ? '#FDE445' : 'transparent');
    if (t >= ga) {
      const k = io((t - ga) / .45);
      if (bad || !good) { e.style.opacity = 1 - k; e.style.transform = `translateY(${-k * 60}px) rotate(${(i - 1) * 8 * k}deg)`; }
      if (good) { const x0 = gap + i * (w + gap), x1 = (1080 - w) / 2; e.style.left = (x0 + (x1 - x0) * k) + 'px'; e.style.transform = `scale(${1 + .12 * k})`; e.style.opacity = 1;
        $(`ok${si}_${i}`).style.opacity = ease((t - ga - .3) / .2); }
    }
  });
  if (sc.strip) {
    show($('sp' + si), t, at(sc.strip.at, sc, .3), 'up', .35, 30);
    (sc.strip.pauses || []).forEach((p, k) => { const e = $(`pz${si}_${k}`), a = at(sc.strip.pause_at, sc, .6) + k * .15, c = at(sc.strip.cut_at, sc, 99);
      e.style.display = t >= a && t < c + .3 ? 'block' : 'none'; e.style.opacity = t < c ? ease((t - a) / .15) : 1 - ease((t - c) / .3); });
  }
};
U.grid = (sc, si, t, lt, dur) => {
  sc.imgs.forEach((s, i) => { const e = $(`gi${si}_${i}`); show(e, t, sc.from + (sc.step_in ?? .02) * i, 'pop', .25);
    e.style.borderColor = t >= at(sc.mark_at, sc, .6) + i * (sc.mark_step ?? .04) ? '#FDE445' : 'transparent';
    if (sc.checks_at !== undefined) { const k = (t - sc.checks_at - i * (sc.check_step ?? .1)) / .2; $(`gk${si}_${i}`).style.opacity = ease(k); $(`gk${si}_${i}`).style.transform = `scale(${.6 + .4 * back(k)})`; } });
  const sn = $('sn' + si), sa = at(sc.scan_at, sc, .4); sn.style.display = t >= sa ? 'block' : 'none'; sn.style.top = (20 + 1100 * cl((t - sa) / (sc.scan_dur || 1.6))) + 'px';
};
U.cards = (sc, si, t) => sc.items.forEach((c, i) => show($(`cd${si}_${i}`), t, at(c.at, sc, .15 + .2 * i), 'up', .35, 40));
U.prompt = (sc, si, t) => {
  show($('pr' + si), t, sc.from, 'up', .35, 24);
  const ma = at(sc.mark_at, sc, 99);
  $('pr' + si).querySelectorAll('b').forEach(b => { const k = ease((t - ma) / .3); b.style.background = `rgba(253,228,69,${k})`; b.style.color = k > .5 ? '#111' : '#FDE445'; });
};
U.cta = (sc, si, t) => {
  [['ct', 0], ['pf', .15], ['cm', .25], ['cs', .3], ['bt', .4], ['hn', .5]].forEach(([p, d]) => show($(p + si), t, sc.from + d, 'up', .35, 30));
  if (sc.comment) {   // слово печатается в поле комментария, потом кнопка отправки желтеет
    const c = sc.comment, a = at(c.at, sc, .6), n = Math.max(0, Math.floor((t - a) * (c.cps || 9))), done = n >= c.text.length;
    $('cmt' + si).innerHTML = t < a ? `<span class="ph">${c.placeholder || 'Добавьте комментарий…'}</span>` : c.text.slice(0, n) + (done && Math.floor(t * 2) % 2 ? '' : '<span class="car"></span>');
    $('snd' + si).style.background = done ? '#FDE445' : '#2E2D2A';
  }
};
U.chart = (sc, si, t, lt, dur) => {
  show($('cr' + si), t, sc.from, 'up', .35, 30);
  const cp = $('cp' + si), L = cp.getTotalLength(), k = io((t - at(sc.draw_at, sc, .3)) / (sc.draw_dur || 2.2));
  cp.style.strokeDasharray = L; cp.style.strokeDashoffset = L * (1 - k); $('ca' + si).style.opacity = k;
  const f = sc.box || [24, 110, 1032, 760];
  $('vp' + si).style.left = (k * (f[2] - 4)) + 'px';
  (sc.marks || []).forEach((m, i) => { const e = $(`mk${si}_${i}`), pt = cp.getPointAtLength(L * m.x);
    let best = pt; for (let q = 0; q <= 200; q++) { const c = cp.getPointAtLength(L * q / 200); if (Math.abs(c.x - (m.x * (f[2] - 60) + 30)) < Math.abs(best.x - (m.x * (f[2] - 60) + 30))) best = c; }
    e.style.left = best.x + 'px'; e.style.top = best.y + 'px'; show(e, t, at(m.at, sc, 1), 'pop', .3, 0);
    e.style.transform = 'translate(-50%,-50%) ' + e.style.transform; });
};
U.flip = (sc, si, t, lt) => {
  show($('fl' + si), t, sc.from, 'left', .4, 12); show($('fr' + si), t, at(sc.seq_at, sc, .3), 'right', .4, 12);
  const v = sc.video || [], q = sc.seq || [];
  v.forEach((s, i) => $(`fv${si}_${i}`).style.opacity = i === Math.floor(lt * (sc.video_fps || 3)) % v.length ? 1 : 0);
  const sa = at(sc.seq_at, sc, .3);
  q.forEach((s, i) => $(`fs${si}_${i}`).style.opacity = t >= sa && i === Math.floor((t - sa) * (sc.seq_fps || 8)) % q.length ? 1 : 0);
  $('fa' + si).setAttribute('stroke-dashoffset', 400 * (1 - ease((t - sa) / .5)));
  show($('fcl' + si), t, sc.from + .2, 'up', .3, 16); show($('fcr' + si), t, sa + .2, 'up', .3, 16);
};
U.media = (sc, si, t, lt, dur) => { const e = $('md' + si); e.style.transform = `scale(${1 + .04 * cl(lt / dur)})`; show($('mc' + si), t, at(sc.cap_at, sc, .2), 'up', .3, 20); };

function render(t) {
  D.scenes.forEach((sc, si) => {
    const el = $('sc' + si), on = t >= sc.from && t < sc.to + .2;   // +0,2 с — под растворку следующей
    el.style.display = on ? 'block' : 'none';
    if (!on) return;
    el.style.opacity = si ? ease((t - sc.from) / .2) : 1;
    const lt = t - sc.from, dur = Math.max(sc.to - sc.from, .5);
    (U[sc.type] || (() => 0))(sc, si, t, lt, dur);
    if (sc.label) show($('lb' + si), t, sc.from, 'up', .3, 14);
    if (sc.word) show($('wd' + si), t, at(sc.word.at, sc), 'pop', .25, 0, sc.word.out);
    (sc.pops || []).forEach((p, i) => show($(`po${si}_${i}`), t, at(p.at, sc, .2), p.kind === 'box' || p.kind === 'check' || p.kind === 'x' ? 'pop' : 'up', .3, 20, p.out));
  });
  // субтитры: окна по 3 слова, разрыв после точки/вопроса
  let ci = -1;
  for (let i = 0; i < D.words.length; i++) if (D.words[i].s <= t + .02) ci = i;
  if (ci < 0 || t > D.words[ci].e + .8) { subs.innerHTML = ''; return; }
  const chunks = []; let c = [];
  if (!D._ch) { D.words.forEach((w, i) => { c.push(i); if (c.length === 3 || /[.!?…]$/.test(w.t)) { chunks.push(c); c = []; } }); if (c.length) chunks.push(c); D._ch = chunks; }
  const ch = D._ch.find(k => k.includes(ci));
  subs.innerHTML = ch.map(i => i === ci ? `<b>${D.words[i].t}</b>` : D.words[i].t).join(' ');
}
window.render = render;
</script></body></html>"""


def build_page(plan_path):
    root = plan_path.parent
    plan = json.load(open(plan_path))
    words, total = load_words(plan, root)
    data = prepare(plan, root, words, total)
    plan["_scenes"] = data["scenes"]
    page = root / "ui_page.html"
    page.write_text(PAGE.replace("__DATA__", json.dumps(data, ensure_ascii=False)), encoding="utf-8")
    return root, plan, total, page


def open_page(pw, page):
    b = pw.chromium.launch(channel="chrome", headless=True, args=["--allow-file-access-from-files"])
    pg = b.new_page(viewport={"width": W, "height": H})
    pg.goto(page.as_uri(), wait_until="networkidle"); pg.evaluate("document.fonts.ready"); pg.wait_for_timeout(500)
    return b, pg


def render_chunk(args):
    page, out, i0, i1 = args
    with sync_playwright() as pw:
        b, pg = open_page(pw, Path(page))
        ff = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "image2pipe", "-framerate", str(FPS), "-c:v", "mjpeg", "-i", "-",
                               "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "17", "-preset", "fast", str(out)], stdin=subprocess.PIPE)
        for i in range(i0, i1):
            pg.evaluate(f"render({i / FPS})", isolated_context=False)
            ff.stdin.write(pg.screenshot(type="jpeg", quality=92))
        ff.stdin.close(); ff.wait(); b.close()
    return out


def mask(path, w, h, r):
    from PIL import Image, ImageDraw
    m = Image.new("L", (w, h), 0); ImageDraw.Draw(m).rounded_rectangle([0, 0, w - 1, h - 1], r, fill=255); m.save(path)


def face_preview(plan, root, out, t):
    """Контрольный кадр как в готовом ролике: лицо из исходника кладётся в карточку или на всю ширину."""
    from PIL import Image
    sc = next((s for s in plan["_scenes"] if s["from"] <= t < s["to"]), plan["_scenes"][-1])
    if sc["face"] not in ("card", "hero"):
        return
    x, y, w, h = plan["card_crop"] if sc["face"] == "card" else plan.get("hero_crop", plan["card_crop"])
    X, Y, W_, H_ = CARD if sc["face"] == "card" else HERO
    tmp = root / "check" / "_face.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{to_src(plan, t):.3f}", "-i", str(root / plan["source"]),
                    "-frames:v", "1", "-vf", f"crop={w}:{h}:{x}:{y},scale={W_}:{H_}", str(tmp)], check=True)
    frame, face = Image.open(out).convert("RGB"), Image.open(tmp).convert("RGB")
    if sc["face"] == "card":
        mk = root / "ui_card_mask.png"
        mask(mk, W_, H_, 30)
        frame.paste(face, (X, Y), Image.open(mk).convert("L"))
    else:
        frame.paste(face, (X, Y))
    frame.save(out, quality=85)
    tmp.unlink()


def main():
    plan_path = Path(sys.argv[1]).resolve()
    root, plan, total, page = build_page(plan_path)
    if "--frames" in sys.argv:
        (root / "check").mkdir(exist_ok=True)
        with sync_playwright() as pw:
            b, pg = open_page(pw, page)
            for t in [float(x) for x in sys.argv[sys.argv.index("--frames") + 1].split(",")]:
                pg.evaluate(f"render({t})", isolated_context=False)
                out = root / "check" / f"ui_{t:07.2f}.jpg"
                pg.screenshot(path=str(out), type="jpeg", quality=85)
                face_preview(plan, root, out, t)
            b.close()
        return
    n = int(total * FPS)
    jobs = int(plan.get("jobs", 6))
    step = -(-n // jobs)
    chunks = [(str(page), str(root / f"ui_chunk_{k}.mp4"), k * step, min(n, (k + 1) * step)) for k in range(jobs)]
    with ProcessPoolExecutor(jobs) as ex:
        outs = list(ex.map(render_chunk, chunks))
    (root / "ui_chunks.txt").write_text("".join(f"file '{o}'\n" for o in outs))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(root / "ui_chunks.txt"), "-c", "copy",
                    str(root / "ui_video_only.mp4")], check=True)
    # лицо из склейки keep (trim/concat – синхронно со звуком): карточкой или на всю ширину снизу
    K = plan["keep"]
    parts = "".join(f"[0:v]trim={a}:{b},setpts=PTS-STARTPTS[v{i}];[0:a]atrim={a}:{b},asetpts=PTS-STARTPTS,"
                    f"afade=t=in:d=0.02,afade=t=out:st={b - a - 0.02}:d=0.02[a{i}];" for i, (a, b) in enumerate(K))
    cat = "".join(f"[v{i}][a{i}]" for i in range(len(K))) + f"concat=n={len(K)}:v=1:a=1[cv][ca];"
    win = lambda kind: "+".join(f"between(t,{sc['from']:.3f},{sc['to'] - 0.001:.3f})" for sc in plan["_scenes"] if sc["face"] == kind) or "0"
    cx, cy, cw, ch = plan["card_crop"]
    hx, hy, hw, hh = plan.get("hero_crop", plan["card_crop"])
    mask(root / "ui_card_mask.png", CARD[2], CARD[3], 30)
    fc = (parts + cat + f"[cv]fps={FPS},split[c1][c2];"
          f"[c1]crop={cw}:{ch}:{cx}:{cy},scale={CARD[2]}:{CARD[3]},setsar=1,format=rgba[cc];[2:v]format=gray[mk];[cc][mk]alphamerge[card];"
          f"[c2]crop={hw}:{hh}:{hx}:{hy},scale={HERO[2]}:{HERO[3]},setsar=1[hero];"
          f"[1:v]setsar=1[g];[g][card]overlay={CARD[0]}:{CARD[1]}:enable='{win('card')}'[g2];"
          f"[g2][hero]overlay={HERO[0]}:{HERO[1]}:enable='{win('hero')}',format=yuv420p[v];[ca]loudnorm=I=-14:TP=-1.5[a]")
    out = root / plan.get("out", "ui_final.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(root / plan["source"]), "-i", str(root / "ui_video_only.mp4"),
                    "-loop", "1", "-i", str(root / "ui_card_mask.png"), "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
                    "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                    "-movflags", "+faststart", "-t", f"{total:.3f}", str(out)], check=True)
    print(out)


if __name__ == "__main__":
    main()
