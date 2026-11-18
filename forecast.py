"""Правило отсечки: когда аукционный номер выйдет без аукциона.

Перенесено из ~/.config/batele/nomera/forecast.py (калибровка 17.11.2026: 2500 из 2551 цикла).
Отсечка = первые торги цикла + 3 календарных месяца. Повторные торги назначают пачкой по
понедельникам: если понедельник после несостоявшихся торгов выпал на отсечку или позже, новых
не назначают. Без аукциона номер выходит в 00:00 на следующий день после последних торгов,
но не раньше отсечки.
"""
import calendar
import datetime as dt
import statistics as st

MONTHS = 3
STEP = 21
GAP = 45            # разрыв между торгами длиннее — уже другой цикл
DAY = dt.timedelta(days=1)


def _date(d):
    return d.date() if isinstance(d, dt.datetime) else d


def addm(d, m):
    y = d.year + (d.month - 1 + m) // 12
    mo = (d.month - 1 + m) % 12 + 1
    return dt.date(y, mo, min(d.day, calendar.monthrange(y, mo)[1]))


def next_mon(d):
    return d + dt.timedelta(days=(7 - d.weekday()) % 7 or 7)


def cutoff(first):
    return addm(_date(first), MONTHS)


def is_last(d, cut):
    """Торги в день d последние: в ближайший понедельник новые уже не назначат."""
    return next_mon(_date(d)) >= cut


def release(last, cut):
    """Дата, в 00:00 которой номер выходит без аукциона."""
    return max(_date(last) + DAY, cut)


def cycle(f):
    """Текущий цикл торгов: хвост истории без разрывов длиннее GAP дней."""
    c = [f[-1]]
    for d in reversed(f[:-1]):
        if (c[0].date() - d.date()).days > GAP:
            break
        c.insert(0, d)
    return c


def offrule(c, cut, more):
    """Номер живёт не по правилу: торги шли и после отсечки."""
    return any(is_last(d, cut) for d in (c if more else c[:-1]))


def step_of(c):
    """Шаг повтора: 2 или 3 недели."""
    gaps = [(b.date() - a.date()).days for a, b in zip(c, c[1:])]
    if not gaps:
        return STEP
    return 14 if st.median(gaps[-3:]) <= 17 else 21


def extend(c, dates):
    """c — прошедшие торги цикла, dates — назначенные. -> (попытки [(дата, назначена?)], последняя, отсечка)."""
    f0 = c[0] if c else dates[0]
    cut = cutoff(f0)
    step = step_of(c + dates[:1]) if c else STEP
    att = []
    last = None
    for d in dates:
        att.append((d, True))
        if is_last(d, cut):
            last = d
            break
    while last is None and len(att) <= 10:
        d = (att[-1][0] if att else c[-1]) + dt.timedelta(days=step)
        while d.weekday() >= 5:      # торги только в будни
            d += DAY
        att.append((d, False))
        if is_last(d, cut):
            last = d
    return att, last, cut


def plan(failed, planned, now):
    """Прогноз по одному номеру.

    failed  — даты несостоявшихся торгов (вся история), planned — назначенные торги.
    -> None (данных нет) или dict:
       c     прошедшие торги текущего цикла
       att   предстоящие попытки [(datetime, назначена ли на сайте)]
       last  последние торги по правилу (datetime) или None
       cut   отсечка (date), rel — дата выхода в 00:00 (date) или None
       off   True — номер торгуется не по правилу, прогноз ненадёжен
       stale True — торгов давно нет и новые не назначены
    """
    failed = sorted(failed)
    planned = sorted(planned)
    c = cycle(failed) if failed else []
    if planned:
        if c and (planned[0].date() - c[-1].date()).days > GAP:
            c = []
        off = bool(c) and offrule(c, cutoff(c[0]), True)
        att, last, cut = extend(c, planned)
        return {'c': c, 'att': att, 'last': last, 'cut': cut,
                'rel': release(last, cut) if last else None, 'off': off, 'stale': False}
    if not c:
        return None
    cut = cutoff(c[0])
    off = offrule(c, cut, False)
    if is_last(c[-1], cut):
        return {'c': c, 'att': [], 'last': c[-1], 'cut': cut, 'rel': release(c[-1], cut), 'off': off, 'stale': False}
    if (now.date() - c[-1].date()).days > GAP:
        return {'c': c, 'att': [], 'last': None, 'cut': cut, 'rel': None, 'off': True, 'stale': True}
    att, last, cut = extend(c, [])
    return {'c': c, 'att': att, 'last': last, 'cut': cut,
            'rel': release(last, cut) if last else None, 'off': off, 'stale': False}
