"""HeyGen через окно Chrome: аудио → видео с твоим аватаром.
    python heygen.py --login         – открыть HeyGen и войти; окно закроется само после входа
    python heygen.py --avatars       – снимок страницы «Мои аватары» в logs/heygen_avatars.png (узнать имя аватара)
    python heygen.py --check         – заполнить форму тестовым аудио и остановиться перед «Сгенерировать» (ничего не тратит)
    python heygen.py <аудио>         – сделать видео и скачать в папку video/
    python heygen.py <аудио> --show  – то же, но с видимым окном Chrome

Путь в HeyGen (проверен 22.10.2026): «Одна сцена» → плитка аватара → твой аватар → «загрузить/записать» →
«Загрузить аудио» → «Добавить аудио» → модель → вертикаль → 1080p → «Сгенерировать».
Готовое видео появляется первой карточкой под формой, оттуда и скачивается.
Нужен платный тариф HeyGen (Creator или выше): на бесплатном скачивание в 1080p закрыто."""
import config  # первым: переключает на окружение .venv
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from patchright.sync_api import sync_playwright

from lib.common import IS_WIN, log

AVATAR = os.getenv("HEYGEN_AVATAR", "").strip()              # имя аватара в HeyGen, как на карточке
MODEL = os.getenv("HEYGEN_MODEL", "Аватар III").strip()      # «Аватар III» – липсинк по твоему голосу
ORIENTATION = os.getenv("HEYGEN_ORIENTATION", "Вертикальная").strip()
RESOLUTION = os.getenv("HEYGEN_RESOLUTION", "1080p").strip()
PROFILE = config.ROOT / "heygen_profile"   # отдельный Chrome-профиль: вход в HeyGen сохраняется
VIDEO_DIR = config.ROOT / "video"
CREATE_URL = "https://app.heygen.com/home/photo-to-video"
AVATARS_URL = "https://app.heygen.com/avatar/my-avatars"
LOGIN_WAIT_MIN = 15
MENU = "[role=menuitem]:visible, [role=option]:visible, [role=menuitemradio]:visible, [role=menuitemcheckbox]:visible"


class HeyGenError(Exception):
    pass


def launch(pw, headless=True):
    PROFILE.mkdir(parents=True, exist_ok=True)
    ua = None
    if headless:  # без окна Chrome подписывается как HeadlessChrome – подменяем на обычный
        os_part = "Windows NT 10.0; Win64; x64" if IS_WIN else "Macintosh; Intel Mac OS X 10_15_7"
        ua = f"Mozilla/5.0 ({os_part}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
    ctx = pw.chromium.launch_persistent_context(
        str(PROFILE), channel="chrome", headless=headless, locale="ru-RU",   # русский интерфейс HeyGen
        viewport={"width": 1300, "height": 900}, user_agent=ua, accept_downloads=True,
        args=["--disable-blink-features=AutomationControlled"])
    ctx.set_default_timeout(30000)
    return ctx


def seconds(audio):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(audio)],
                         capture_output=True, text=True).stdout
    return float(out.strip() or 0)


def shot(page, name):
    path = config.LOGS / f"heygen_{name}.png"
    try:
        page.screenshot(path=str(path))
    except Exception:
        pass
    return path


def open_page(page, url):
    page.goto(url, wait_until="domcontentloaded", timeout=90000)
    time.sleep(8)
    if any(s in page.url for s in ("/login", "/signup", "/auth")):
        raise HeyGenError("HeyGen просит войти: python heygen.py --login")


def login(pw):
    ctx = launch(pw, headless=False)
    try:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://app.heygen.com/login", wait_until="domcontentloaded", timeout=90000)
        log(f"Войди в HeyGen в открывшемся окне Chrome. Жду до {LOGIN_WAIT_MIN} минут.")
        deadline = time.time() + LOGIN_WAIT_MIN * 60
        while time.time() < deadline:
            time.sleep(3)
            # вход мог идти во всплывающей вкладке (Google) – смотрим все вкладки
            if any("app.heygen.com" in p.url and not any(s in p.url for s in ("/login", "/signup", "/auth"))
                   for p in ctx.pages):
                time.sleep(8)  # дать дописать куки
                log("Готово: вход в HeyGen сохранён.")
                return True
        log("Вход не дождался. Запусти heygen.py --login ещё раз.")
        return False
    finally:
        ctx.close()


def pick_avatar(page):
    if not AVATAR:
        raise HeyGenError("Не задан аватар: впиши HEYGEN_AVATAR=<имя аватара> в .env")
    page.get_by_test_id("presenter-avatar-tile").click()
    time.sleep(4)
    dialog = page.locator("[role=dialog]:visible")
    # окно открывается в папке последнего аватара – если нужного там нет, выходим в «Мои аватары»
    if not dialog.get_by_text(AVATAR, exact=True).count():
        root = dialog.get_by_text(re.compile(r"^(Мои аватары|My avatars)$"))
        if root.count():
            root.first.click(force=True)
            time.sleep(3)
    # первый клик открывает папку аватара, второй выбирает образ; окно закрывается, когда выбран
    for _ in range(3):
        name = dialog.get_by_text(AVATAR, exact=True)
        if not name.count():
            break
        name.last.click(force=True)
        time.sleep(3)
        if not dialog.count():
            return
    raise HeyGenError(f"Не нашёл аватар «{AVATAR}». Проверь имя: python heygen.py --avatars "
                      f"(снимок окна: {shot(page, 'error')})")


def choose(page, opener, option, what):
    """Открыть меню (модель, ориентация, качество) и выбрать пункт по началу текста."""
    opener.click()
    time.sleep(2)
    items = page.locator(MENU).filter(has_text=re.compile("^" + re.escape(option)))
    if not items.count():
        page.keyboard.press("Escape")
        raise HeyGenError(f"В HeyGen нет пункта «{option}» ({what}). Снимок: {shot(page, 'error')}")
    items.first.click()
    time.sleep(2)


def prepare(page, audio):
    """Заполнить форму до кнопки «Сгенерировать». Ничего не создаёт и не тратит."""
    open_page(page, CREATE_URL)
    page.get_by_test_id("presenter-avatar-tile").wait_for(timeout=60000)
    pick_avatar(page)
    page.get_by_text(re.compile(r"загрузить/записать|upload/record", re.I)).first.click()
    page.get_by_text(re.compile(r"^(Загрузить аудио|Upload audio)$", re.I)).first.click()
    page.locator("input[type=file]").last.set_input_files(str(audio))
    page.get_by_role("button", name=re.compile(r"^(Добавить аудио|Add audio)$", re.I)).click(timeout=120000)
    time.sleep(3)
    box = page.get_by_test_id("presenter-prompt-box")
    if Path(audio).name not in box.inner_text():
        raise HeyGenError(f"Аудио не прикрепилось. Снимок: {shot(page, 'error')}")
    model = box.locator("button[aria-haspopup=menu]").first
    if model.inner_text().strip() != MODEL:
        choose(page, model, MODEL, "модель")
    menus = box.locator("div[aria-haspopup=menu]")   # без текста – ориентация, с «720p/1080p» – качество
    choose(page, menus.first, ORIENTATION, "ориентация")
    quality = menus.filter(has_text=re.compile(r"\d{3,4}p"))
    if quality.first.inner_text().strip() != RESOLUTION:
        choose(page, quality.first, RESOLUTION, "качество")
    if quality.first.inner_text().strip() != RESOLUTION:
        raise HeyGenError(f"Не выбралось {RESOLUTION} – нужен платный тариф HeyGen. Снимок: {shot(page, 'error')}")


def first_card(page):
    return page.locator("[data-atlas-id=avatar-shot]").first


def download_first(page, out, expect_sec=None):
    """Первая карточка под формой → кнопка скачивания → «Скачать» (в «Расширенных настройках» уже 1080p)."""
    card = first_card(page)
    card.scroll_into_view_if_needed()
    card.locator("button").nth(3).click()   # кнопки карточки: 👍 👎 копировать скачать … открыть
    time.sleep(3)
    dialog = page.locator("[role=dialog]:visible")
    if expect_sec:  # сверяем длину: скачиваем именно это видео, а не прошлое
        m = re.search(r"(?:(\d+)m\s*)?(\d+)s", dialog.inner_text())
        if m and abs(int(m.group(1) or 0) * 60 + int(m.group(2)) - expect_sec) > 3:
            raise HeyGenError(f"Первое видео в HeyGen другой длины, чем аудио. Снимок: {shot(page, 'error')}")
    # качество при скачивании выбирается ещё раз (по умолчанию 720П) – в «Расширенных настройках»
    res = re.compile("^" + RESOLUTION[:-1] + "[pPпП]$")
    dialog.get_by_role("button", name=re.compile(r"Расширенные настройки|Advanced settings")).click()
    time.sleep(1)
    combo = dialog.get_by_role("combobox")
    if not res.match(combo.first.inner_text().strip()):
        combo.first.click()
        time.sleep(1)
        page.get_by_text(res).last.click()   # пункты списка – простые div без роли
        time.sleep(1)
    if not res.match(combo.first.inner_text().strip()):
        raise HeyGenError(f"Не выбралось {RESOLUTION} при скачивании. Снимок: {shot(page, 'error')}")
    with page.expect_download(timeout=600000) as dl:
        dialog.get_by_role("button", name=re.compile(r"^(Скачать|Download)$")).click()
    out.parent.mkdir(parents=True, exist_ok=True)
    dl.value.save_as(str(out))
    return out


def generate(page, audio, out):
    """Сгенерировать и скачать. Рендер минутного аудио обычно 5–10 минут; ждём до 10 минут на минуту, минимум час."""
    sec = seconds(audio)
    prepare(page, audio)
    page.get_by_test_id("presenter-generate-btn").click()
    log("HeyGen: видео в работе")
    time.sleep(20)
    deadline = time.time() + max(60, sec / 60 * 10) * 60
    while time.time() < deadline:
        open_page(page, CREATE_URL)
        card = first_card(page)
        # пока рендерится – на карточке «39%», готовое – длина «01:43»; сверяем её с длиной аудио
        text = card.inner_text() if card.count() else ""
        m = re.search(r"\b(\d{1,2}):(\d{2})\b", text)
        if m and not re.search(r"\d+\s*%", text) and abs(int(m.group(1)) * 60 + int(m.group(2)) - sec) <= 3:
            return download_first(page, out, round(sec))
        time.sleep(30)
    raise HeyGenError(f"Видео не готово за отведённое время. Снимок: {shot(page, 'error')}")


def test_audio():
    path = config.DATA / "heygen_test.mp3"
    if not path.exists():
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=220:duration=3",
                        "-ac", "1", str(path)], check=True)
    return path


def make(audio, headless=True):
    """Для других скриптов: аудио → путь к готовому видео."""
    audio = Path(audio).resolve()
    with sync_playwright() as pw:
        ctx = launch(pw, headless=headless)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            return generate(page, audio, VIDEO_DIR / (audio.stem + ".mp4"))
        finally:
            ctx.close()


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    try:
        with sync_playwright() as pw:
            if "--login" in sys.argv:
                sys.exit(0 if login(pw) else 1)
            if "--avatars" in sys.argv or "--check" in sys.argv:
                ctx = launch(pw, headless="--show" not in sys.argv)
                try:
                    page = ctx.pages[0] if ctx.pages else ctx.new_page()
                    if "--avatars" in sys.argv:
                        open_page(page, AVATARS_URL)
                        print("Снимок «Мои аватары»:", shot(page, "avatars"))
                    else:
                        prepare(page, test_audio())
                        print("✅ HeyGen готов: вход есть, аватар, аудио, модель, вертикаль и", RESOLUTION, "выбираются.")
                        print("Снимок формы:", shot(page, "check"), "– кнопка «Сгенерировать» не нажата.")
                finally:
                    ctx.close()
                sys.exit(0)
        if not args:
            sys.exit(print(__doc__))
        print(make(args[0], headless="--show" not in sys.argv))
    except HeyGenError as e:
        sys.exit(f"❌ {e}")
