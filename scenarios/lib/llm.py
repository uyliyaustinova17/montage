"""Вызов Claude через Claude Code (подписка ученика, отдельный ключ не нужен). Ответ – JSON."""
import json
import re
import shutil
import subprocess
from datetime import datetime

import config
from lib.common import IS_WIN


class LLMError(Exception):
    pass


def _claude():
    exe = shutil.which("claude")
    if not exe:
        raise LLMError("не найдена программа claude – установи Claude Code")
    return exe


def command(args):
    """Команда запуска claude с аргументами. На Windows claude бывает .cmd – его запускает только cmd,
    а путь с пробелом (C:\\Users\\Ира Иванова\\...) cmd понимает только в двойных кавычках с /s."""
    exe = _claude()
    if IS_WIN and exe.lower().endswith((".cmd", ".bat")):
        return f'cmd /d /s /c ""{exe}" {subprocess.list2cmdline(args)}"'
    return [exe, *args]


def ask_json(prompt, model, timeout=900):
    try:
        return _ask(prompt, model, timeout)
    except LLMError as e:
        if not str(e).startswith("не JSON"):
            raise
        return _ask(prompt + "\n\nВажно: ответ – строго валидный JSON, кавычки внутри строк экранированы.", model, timeout)


def _ask(prompt, model, timeout):
    try:
        r = subprocess.run(command(["-p", "--model", model, "--output-format", "json", "--tools", ""]),
                           input=prompt, capture_output=True, text=True, encoding="utf-8", timeout=timeout,
                           cwd=config.ROOT)
    except subprocess.TimeoutExpired:
        raise LLMError(f"Claude не ответил за {timeout} с")
    try:
        out = json.loads(r.stdout)
    except ValueError:
        out = None
    if r.returncode != 0 or out is None or out.get("is_error"):
        why = str((out or {}).get("result") or r.stderr or r.stdout).strip()[:300]
        if any(w in why.lower() for w in ("login", "api key", "authenticat", "not logged")):
            why = "Claude Code не вошёл в аккаунт: открой терминал, набери claude и войди. (" + why + ")"
        raise LLMError(why)
    text = (out.get("result") or "").strip()
    tries = [text, re.sub(r"^```(?:json)?\s*|\s*```$", "", text)]
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    if starts:
        tries.append(text[min(starts):max(text.rfind("}"), text.rfind("]")) + 1])
    for t in tries:
        try:
            return json.loads(t)
        except ValueError:
            pass
    with open(config.LOGS / "llm_bad.txt", "a", encoding="utf-8") as f:
        f.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M}\n{text}\n")
    raise LLMError("не JSON: " + text[:300])
