from __future__ import annotations

from datetime import datetime, timedelta

try:
    from croniter import croniter as _croniter  # type: ignore
except ModuleNotFoundError:  # Офлайн/минимальное окружение.
    _croniter = None


def _parse_atom(atom: str, minimum: int, maximum: int) -> set[int]:
    values: set[int] = set()
    base, slash, step_raw = atom.partition("/")
    step = int(step_raw) if slash else 1
    if step <= 0:
        raise ValueError("cron step must be positive")
    if base == "*":
        start, end = minimum, maximum
    elif "-" in base:
        left, right = base.split("-", 1)
        start, end = int(left), int(right)
    else:
        start = end = int(base)
    if start < minimum or end > maximum or start > end:
        raise ValueError("cron value out of range")
    values.update(range(start, end + 1, step))
    return values


def _parse_field(field: str, minimum: int, maximum: int, *, dow: bool = False) -> set[int]:
    if not field:
        raise ValueError("empty cron field")
    result: set[int] = set()
    for part in field.split(","):
        result.update(_parse_atom(part, minimum, maximum))
    if dow and 7 in result:
        result.remove(7)
        result.add(0)
    return result


def cron_is_valid(expression: str) -> bool:
    if _croniter is not None:
        return bool(_croniter.is_valid(expression))
    try:
        fields = expression.split()
        if len(fields) != 5:
            return False
        _parse_field(fields[0], 0, 59)
        _parse_field(fields[1], 0, 23)
        _parse_field(fields[2], 1, 31)
        _parse_field(fields[3], 1, 12)
        _parse_field(fields[4], 0, 7, dow=True)
        return True
    except (TypeError, ValueError):
        return False


def cron_matches(expression: str, moment: datetime) -> bool:
    if _croniter is not None:
        return bool(_croniter.match(expression, moment))
    fields = expression.split()
    if len(fields) != 5:
        return False
    minute = _parse_field(fields[0], 0, 59)
    hour = _parse_field(fields[1], 0, 23)
    dom = _parse_field(fields[2], 1, 31)
    month = _parse_field(fields[3], 1, 12)
    dow = _parse_field(fields[4], 0, 7, dow=True)
    # cron Sunday=0, Python Monday=0
    py_dow = (moment.weekday() + 1) % 7
    return (
        moment.minute in minute
        and moment.hour in hour
        and moment.day in dom
        and moment.month in month
        and py_dow in dow
    )


def cron_previous(expression: str, before_or_at: datetime) -> datetime:
    if _croniter is not None:
        return _croniter(expression, before_or_at).get_prev(datetime)
    current = before_or_at.replace(second=0, microsecond=0) - timedelta(minutes=1)
    limit = current - timedelta(days=370)
    while current >= limit:
        if cron_matches(expression, current):
            return current
        current -= timedelta(minutes=1)
    raise ValueError("Не удалось найти предыдущий cron slot в пределах 370 дней")


def cron_next(expression: str, after: datetime) -> datetime:
    """Возвращает следующий cron slot после указанного момента."""
    if _croniter is not None:
        return _croniter(expression, after).get_next(datetime)
    current = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit = current + timedelta(days=370)
    while current <= limit:
        if cron_matches(expression, current):
            return current
        current += timedelta(minutes=1)
    raise ValueError("Не удалось найти следующий cron slot в пределах 370 дней")
