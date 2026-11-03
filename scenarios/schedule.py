"""Ночной запуск run.py.
    python schedule.py on        – включить (каждый день в 03:00)
    python schedule.py on 05:30  – включить на своё время
    python schedule.py off       – выключить
    python schedule.py status    – проверить"""
import config  # первым: переключает на окружение .venv
import base64
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

from lib.common import IS_MAC, IS_WIN

NAME = "scenarios-nightly"
RUN = config.ROOT / "run.py"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"com.{NAME}.plist"


def on(at="03:00"):
    h, m = (int(x) for x in at.split(":"))
    if IS_MAC:
        # ночью у запуска нет твоего терминала: явно говорим, где лежат claude, ffmpeg и python
        dirs = [str(Path(p).parent) for p in (shutil.which("claude"), shutil.which("ffmpeg"), sys.executable) if p]
        path = ":".join(dict.fromkeys(dirs + ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin",
                                              str(Path.home() / ".local/bin")]))
        PLIST.parent.mkdir(parents=True, exist_ok=True)
        log = str(config.LOGS / "schedule.log")
        PLIST.write_bytes(plistlib.dumps({
            "Label": f"com.{NAME}", "ProgramArguments": [sys.executable, str(RUN)], "WorkingDirectory": str(config.ROOT),
            "StartCalendarInterval": {"Hour": h, "Minute": m}, "StandardOutPath": log, "StandardErrorPath": log,
            "EnvironmentVariables": {"PATH": path}}))
        subprocess.run(["launchctl", "unload", str(PLIST)], capture_output=True)
        subprocess.run(["launchctl", "load", str(PLIST)], check=True)
    elif IS_WIN:
        q = lambda v: str(v).replace("'", "''")  # апостроф в пути для PowerShell удваивается
        ps = ("$ProgressPreference = 'SilentlyContinue';"
              f"$a = New-ScheduledTaskAction -Execute '{q(sys.executable)}' -Argument '\"{q(RUN)}\"' -WorkingDirectory '{q(config.ROOT)}';"
              f"$t = New-ScheduledTaskTrigger -Daily -At '{at}';"
              "$s = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 3);"
              f"Register-ScheduledTask -TaskName '{NAME}' -Action $a -Trigger $t -Settings $s -Force | Out-Null")
        # закодированная команда: кавычки и кириллица в пути (C:\Users\Ира\...) не ломают PowerShell
        enc = base64.b64encode(ps.encode("utf-16-le")).decode()
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", enc],
                           capture_output=True)
        if r.returncode != 0:
            sys.exit("Не получилось включить: " + r.stderr.decode("utf-8", "replace")[-500:])
    else:
        sys.exit("Поддерживаются macOS и Windows")
    print(f"Включено: каждый день в {at}. Компьютер в это время должен быть включён (на Windows разбудится сам).")


def off():
    if IS_MAC:
        subprocess.run(["launchctl", "unload", str(PLIST)], capture_output=True)
        PLIST.unlink(missing_ok=True)
    elif IS_WIN:
        subprocess.run(["schtasks", "/Delete", "/TN", NAME, "/F"], capture_output=True)
    print("Выключено.")


def status():
    if IS_MAC:
        r = subprocess.run(["launchctl", "list"], capture_output=True, text=True)
        print("Включено" if f"com.{NAME}" in r.stdout else "Выключено")
    elif IS_WIN:
        r = subprocess.run(["schtasks", "/Query", "/TN", NAME], capture_output=True)  # без text: вывод в кодировке консоли
        print("Включено" if r.returncode == 0 else "Выключено")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    {"on": lambda: on(*sys.argv[2:3]), "off": off, "status": status}.get(cmd, status)()
