Add a new module `shopkit/utils/durations.py` with `parse_duration(text: str) -> int` returning a
duration in whole minutes. Input is hours and/or minutes such as "2h", "45m", "1h30m" or
"1h 30m": hours come before minutes, each unit at most once, units are case-insensitive, and
surrounding whitespace is ignored. Anything else (empty text, other units, a missing number,
minutes before hours, negative numbers) raises `ValueError`.
