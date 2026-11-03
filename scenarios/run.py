"""Всё подряд: разведка → расшифровка → сценарии. Этот файл запускается по расписанию.
    python run.py"""
import config  # первым: переключает на окружение .venv
import os
import traceback

os.chdir(os.path.dirname(os.path.abspath(__file__)))

import collect  # noqa: E402
import transcribe  # noqa: E402
import write  # noqa: E402
from lib import tg  # noqa: E402
from lib.common import log  # noqa: E402

if __name__ == "__main__":
    log("=== Запуск системы ===")
    for name, step in (("Разведка", collect.main), ("Расшифровка", transcribe.main), ("Сценарии", write.main)):
        try:
            step()
        except Exception as e:
            log(f"{name}: упало – {e}\n{traceback.format_exc()}")
            tg.notify(f"Шаг «{name}» упал: {str(e)[:300]}\nПодробности – в папке logs.")
    log("=== Готово ===")
