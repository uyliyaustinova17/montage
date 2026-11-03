"""Шаг 2. Расшифровка залётов: свежие и самые сильные первыми, не больше 20 за запуск.
    python transcribe.py"""
import config  # первым: переключает на окружение .venv
import random
import time

from lib import db, speech
from lib.common import log, now


def main(limit=config.TRANSCRIBE_PER_RUN, ids=None):
    con = db.connect()
    if ids:
        rows = con.execute(f"SELECT id, blogger, multiplier FROM reels WHERE id IN ({','.join('?' * len(ids))})",
                           ids).fetchall()
    else:
        rows = con.execute("SELECT id, blogger, multiplier FROM reels WHERE state='outlier' "
                           "ORDER BY posted_at >= date('now', ?) DESC, multiplier DESC LIMIT ?",
                           (f"-{config.FRESH_DAYS} days", limit)).fetchall()
    if not rows:
        log("Расшифровывать нечего: новых залётов нет")
        return 0
    ok = 0
    for i, r in enumerate(rows):
        if i:
            time.sleep(random.uniform(2, 5))
        try:
            text, lang, caption, engine = speech.transcribe(r["id"])
        except Exception as e:
            # не скачался даже со входом – больше не пробуем
            closed = "isn't available" in str(e) or "not available" in str(e) or "login" in str(e).lower()
            con.execute("UPDATE reels SET error=?, updated=?, state=CASE WHEN ? THEN 'skipped' ELSE state END WHERE id=?",
                        (str(e)[:300], now(), int(closed), r["id"]))
            con.commit()
            log(f"{r['id']}: не расшифровался – {str(e)[:200]}")
            continue
        is_ad = any(m in caption.lower() for m in config.AD_MARKERS)
        if len(text) < 80:  # речи почти нет, ролик держится на картинке и подписи
            text = f"[речи почти нет] {text}\nПодпись к ролику: {caption}".strip()
        state = "skipped" if is_ad else "transcribed"
        con.execute("UPDATE reels SET transcript=?, lang=?, caption=?, engine=?, error=?, state=?, updated=? WHERE id=?",
                    (text, lang, caption, engine, "реклама" if is_ad else None, state, now(), r["id"]))
        con.commit()
        ok += 1
        log(f"{r['id']} @{r['blogger']} ×{r['multiplier'] or '?'}: {engine}, {len(text)} знаков{' (реклама, пропуск)' if is_ad else ''}")
    log(f"Расшифровано: {ok} из {len(rows)}")
    return ok


if __name__ == "__main__":
    main()
