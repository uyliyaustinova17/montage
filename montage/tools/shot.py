"""Скриншот настоящей страницы для наглядности (UI-монтаж показывает только реальные экраны).

    python3 tools/shot.py <проект> <имя> <ссылка> [--full] [--mobile]

→ projects/<проект>/assets/<имя>.png в двойном разрешении. Окна cookie и «войдите» скрипт пытается закрыть;
если страница всё равно закрыта логином или баннером – не подделывать интерфейс, а попросить у автора свой скриншот."""
import argparse
import sys

from common import project


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("name")
    ap.add_argument("url")
    ap.add_argument("--full", action="store_true", help="вся страница, а не первый экран")
    ap.add_argument("--mobile", action="store_true", help="как на телефоне: 430×932")
    a = ap.parse_args()
    p = project(a.project)
    out = p / "assets" / f"{a.name}.png"
    out.parent.mkdir(exist_ok=True)
    from patchright.sync_api import sync_playwright
    vw, vh = (430, 932) if a.mobile else (1440, 900)
    with sync_playwright() as pw:
        try:
            b = pw.chromium.launch(channel="chrome", headless=True)
        except Exception:
            b = pw.chromium.launch(headless=True)
        pg = b.new_page(viewport={"width": vw, "height": vh}, device_scale_factor=2, locale="ru-RU")
        pg.goto(a.url, wait_until="domcontentloaded", timeout=60000)
        pg.wait_for_timeout(4000)
        for txt in ("Принять", "Accept all", "Accept", "Reject", "Разрешить все", "Allow all", "Согласен", "OK", "Понятно",
                    "Закрыть"):
            try:
                pg.get_by_role("button", name=txt, exact=False).first.click(timeout=800)
                pg.wait_for_timeout(500)
                break
            except Exception:
                pass
        # что не закрылось кнопкой – убрать со страницы: окна cookie, согласия, затемнение под ними
        pg.evaluate("""() => {
          const bad = /cookie|consent|gdpr|onetrust|didomi|cmp-|privacy-banner/i;
          document.querySelectorAll('body *').forEach(e => {
            const id = (e.id || '') + ' ' + (typeof e.className === 'string' ? e.className : '');
            const st = getComputedStyle(e);
            const txt = (e.innerText || '').slice(0, 400).toLowerCase();
            if (bad.test(id) || ((st.position === 'fixed' || e.getAttribute('role') === 'dialog' || e.tagName === 'DIALOG')
                && /cookie|куки|consent|согласи/.test(txt))) e.remove();
          });
          document.querySelectorAll('[class*=backdrop],[class*=overlay]').forEach(e => {
            if (getComputedStyle(e).position === 'fixed' && !(e.innerText || '').trim()) e.remove(); });
          document.documentElement.style.overflow = 'auto'; document.body.style.overflow = 'auto';
        }""")
        pg.wait_for_timeout(300)
        pg.screenshot(path=str(out), full_page=a.full)
        b.close()
    print(out)
    print("Открой картинку и проверь: нет ли окна входа, cookie-баннера или пустого экрана.")


if __name__ == "__main__":
    sys.exit(main())
