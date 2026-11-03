"""Шаг 0. Паспорт блога: анкета → knowledge/passport.md (кто ты, зритель, боли, темы) и knowledge/style.md (как ты говоришь).
Блогеры из анкеты попадают в bloggers.txt. Ссылки на свои рилсы из анкеты расшифровываются – по ним виден стиль.
    python passport.py"""
import config  # первым: переключает на окружение .venv
import re
import sys

from lib import db, llm, speech
from lib.common import log

PROMPT = """Ниже анкета автора блога и расшифровки его собственных роликов (если есть).
Сделай два документа в markdown, по-русски.

1. "passport" – паспорт блога. Разделы:
   ## Кто автор (одно предложение, как сказал бы другу; что продаёт и за сколько; подписчики)
   ## Зритель (кто он; боли его словами – списком, дословно из анкеты; что мешает; чего хочет)
   ## Темы блога (5–6 подтем)
   ## Куда ведём (кодовое слово и что человек получает; или «пока только подписка»)
   ## Не говорим никогда
   ## Факты автора (только из анкеты: цифры, истории, результаты)
   ## Ниша для поиска блогеров (3–5 строк: о чём ниша, кто похожие авторы, ключевые слова по-русски и по-английски)
   Ничего не додумывай. Пустой пункт анкеты – пиши [уточнить].

2. "style" – как автор говорит. Разделы:
   ## Обращение (ты / вы)
   ## Длина фраз и ритм
   ## Слова и обороты автора (с цитатами)
   ## Как начинает и заканчивает мысль
   ## Чего в речи нет
   ## Образцы (3 дословные цитаты из расшифровок или текстов автора)
   Опирайся только на образцы речи. Если образцов нет – так и напиши и дай общие правила: просто, коротко, на «ты».

Ответь только JSON-объектом: {{"passport": "markdown", "style": "markdown"}}

<анкета>
{anketa}
</анкета>

<расшифровки своих роликов>
{own}
</расшифровки своих роликов>"""


def find_anketa():
    for p in [config.ROOT / "anketa.md", *sorted(config.ROOT.glob("*АНКЕТА*.md")), *sorted(config.ROOT.glob("*nketa*.md"))]:
        if p.exists():
            return p
    return None


def section(text, num):
    m = re.search(rf"^## {num}\..*?$(.*?)(?=^## \d+\.|\Z)", text, re.S | re.M)
    return m[1] if m else ""


def main():
    a = find_anketa()
    if not a:
        sys.exit("Нет анкеты: заполни anketa.md в папке scenarios")
    text = a.read_text(encoding="utf-8")
    # блогеры из раздела 7
    names = re.findall(r"@?([A-Za-z0-9._]{3,30})", section(text, 7).replace("instagram.com/", "@"))
    names = [n for n in names if not n.lower().startswith(("http", "www"))]
    for n in names:
        db.add_to_list(n)
    if names:
        log(f"В bloggers.txt: {', '.join('@' + n for n in names)}")
    # свои рилсы из раздела 8 – расшифровать
    own = []
    for code in re.findall(r"instagram\.com/(?:reel|reels|p)/([A-Za-z0-9_-]+)", section(text, 8))[:5]:
        try:
            t, *_ = speech.transcribe(code)
            own.append(t)
            log(f"Свой ролик {code}: расшифрован")
        except Exception as e:
            log(f"Свой ролик {code}: не расшифровался – {str(e)[:150]}")
    res = llm.ask_json(PROMPT.format(anketa=text, own="\n\n---\n\n".join(own) or "нет"), model=config.WRITE_MODEL)
    (config.KNOWLEDGE / "passport.md").write_text(res["passport"].strip() + "\n", encoding="utf-8")
    (config.KNOWLEDGE / "style.md").write_text(res["style"].strip() + "\n", encoding="utf-8")
    log("Готово: knowledge/passport.md и knowledge/style.md. Открой и поправь, если что-то не так.")


if __name__ == "__main__":
    main()
