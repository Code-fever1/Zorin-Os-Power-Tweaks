#!/usr/bin/env python3
"""Stop auto-cpufreq from pulsing turbo around a 20% load line.

The daemon turns boost on at 20% and off under 20%, and it rewrites
no_turbo on every pass. This adds a hold band and skips unchanged writes.
"""
from pathlib import Path
import sys

TARGET = Path(
    "/opt/auto-cpufreq/current/venv/lib/python3.12/site-packages/auto_cpufreq/core.py"
)

OLD_SET = '''def set_turbo(value:bool):
    print("Setting turbo boost:", "on" if value else "off")
    turbo(value)
'''

NEW_SET = '''def set_turbo(value:bool):
    if turbo() == bool(value):
        return
    print("Setting turbo boost:", "on" if value else "off")
    turbo(value)

_turbo_hold = None

def set_turbo_auto(cpuload, temp=None):
    """Boost on only while the CPU stays busy. Hold the last choice in between."""
    global _turbo_hold
    if _turbo_hold is None:
        _turbo_hold = bool(turbo())
    if temp is not None and temp >= 70 and cpuload < 25:
        _turbo_hold = False
    elif cpuload >= 40:
        _turbo_hold = True
    elif cpuload < 15:
        _turbo_hold = False
    set_turbo(_turbo_hold)
'''

REPLACEMENTS = (
    (
        '''        if cpuload >= 20: set_turbo(True) # high cpu usage trigger
        else: # set turbo state based on average of all core temperatures
            from auto_cpufreq.modules.system_info import SystemInfo

            print(f"Optimal total CPU usage: {cpuload}%, high average core temp: {SystemInfo.avg_temp()}°C")
            set_turbo(False)
''',
        '''        from auto_cpufreq.modules.system_info import SystemInfo

        print(f"Optimal total CPU usage: {cpuload}%, high average core temp: {SystemInfo.avg_temp()}°C")
        set_turbo_auto(cpuload, SystemInfo.avg_temp())
''',
    ),
    (
        '''            if cpuload >= 20: set_turbo(True) # high cpu usage trigger
            elif SystemInfo.avg_temp() >= 70: # set turbo state based on average of all core temperatures
                print(f"Optimal total CPU usage: {cpuload}%, high average core temp: {SystemInfo.avg_temp()}°C")
                set_turbo(False)
            else: set_turbo(True)
''',
        '''            print(f"Optimal total CPU usage: {cpuload}%, high average core temp: {SystemInfo.avg_temp()}°C")
            set_turbo_auto(cpuload, SystemInfo.avg_temp())
''',
    ),
    (
        '''            if cpuload >= 20: set_turbo(True) # high cpu usage trigger
            elif SystemInfo.avg_temp() >= 65: # set turbo state based on average of all core temperatures
                print(f"Optimal total CPU usage: {cpuload}%, high average core temp: {SystemInfo.avg_temp()}°C")
                set_turbo(False)
            else: set_turbo(True)
''',
        '''            print(f"Optimal total CPU usage: {cpuload}%, high average core temp: {SystemInfo.avg_temp()}°C")
            set_turbo_auto(cpuload, SystemInfo.avg_temp())
''',
    ),
    (
        '''            if cpuload >= 20: set_turbo(True) # high cpu usage trigger
            else: # set turbo state based on average of all core temperatures
                print(f"Optimal total CPU usage: {cpuload}%, high average core temp: {SystemInfo.avg_temp()}°C")
                set_turbo(False)
''',
        '''            print(f"Optimal total CPU usage: {cpuload}%, high average core temp: {SystemInfo.avg_temp()}°C")
            set_turbo_auto(cpuload, SystemInfo.avg_temp())
''',
    ),
)


def main() -> int:
    if not TARGET.is_file():
        print(f"auto-cpufreq core not found: {TARGET}", file=sys.stderr)
        return 1
    text = TARGET.read_text()
    if "def set_turbo_auto(" in text:
        print("Turbo hold is already installed.")
        return 0
    if OLD_SET not in text:
        print("set_turbo() did not match. auto-cpufreq changed.", file=sys.stderr)
        return 1
    text = text.replace(OLD_SET, NEW_SET, 1)
    for old, new in REPLACEMENTS:
        count = text.count(old)
        if count != 1:
            print(f"Expected 1 match, found {count} for a turbo decision.", file=sys.stderr)
            return 1
        text = text.replace(old, new, 1)
    TARGET.write_text(text)
    print(f"Updated {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
