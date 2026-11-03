"""Вход в отдельный аккаунт Instagram. Открывает Chrome, ты входишь сам, окно закрывается после входа.
    python login.py"""
import config  # первым: переключает на окружение .venv
import time

from patchright.sync_api import sync_playwright

from collect import save_cookies
from lib.common import launch, logged_in, log

WAIT_MIN = 15

if __name__ == "__main__":
    with sync_playwright() as pw:
        ctx = launch(pw, headless=False)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            if logged_in(ctx):
                save_cookies(ctx)
                log("Вход уже есть. Ничего делать не нужно.")
            else:
                page.goto("https://www.instagram.com/accounts/login/")
                log(f"Войди в Instagram в открывшемся окне Chrome. Жду до {WAIT_MIN} минут.")
                deadline = time.time() + WAIT_MIN * 60
                while time.time() < deadline and not logged_in(ctx):
                    time.sleep(3)
                if logged_in(ctx):
                    time.sleep(8)  # дать Instagram дописать куки
                    save_cookies(ctx)
                    log("Готово: вход сохранён. Окно можно не трогать, оно закроется само.")
                else:
                    log("Вход не дождался. Запусти login.py ещё раз.")
        finally:
            ctx.close()
