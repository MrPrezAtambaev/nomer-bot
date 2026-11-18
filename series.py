"""Списки номеров, сгруппированно: серия целиком (одни буквы) и все номера с одними цифрами."""
import collections
import datetime as dt
import html
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import card
import forecast
import srs

# Цифры, которые продаются через аукцион (прайс-лист портала). Бот сам перечитывает прайс раз в неделю.
AUCTION_DIGITS = ('001 777 888 999 111 222 333 444 555 666 007 100 200 300 400 500 600 700 800 900 '
                  '002 003 004 005 006 008 009 010 020 030 040 050 060 070 080 090 460 470 570').split()
RECENT = 8      # за сколько дней смотрим несостоявшиеся торги: дольше номер между торгами не ждёт
NEAR = 14       # в списке по цифрам торги на ближайшие столько дней идут по дням, дальние — пачкой за месяц
PER_ROW = 30    # номеров в строке: в сообщении Telegram нажимаются только первые 100 команд


def fetch_auction_digits():
    """Аукционные цифры из прайс-листа портала (первая таблица). -> список или None."""
    page = srs._open(urllib.request.build_opener(), srs.BASE + '/prices.xhtml')
    tabs = re.findall(r'<table.*?</table>', page, re.S)
    if not tabs:
        return None
    cells = [html.unescape(re.sub(r'<[^>]+>', ' ', c)).strip() for c in re.findall(r'<td.*?</td>', tabs[0], re.S)]
    digs = [d for c in cells if re.fullmatch(r'(\d{3}[,\s]*)+', c) for d in re.findall(r'\d{3}', c)]
    return digs if 20 <= len(digs) <= 80 else None


def _pages(region, syms):
    def one(sym):
        try:
            return srs.fetch_plate(region, sym, timeout=15, tries=2)
        except Exception:
            return None

    with ThreadPoolExecutor(4) as ex:
        return list(ex.map(one, syms))


def _blank(free):
    return {'msg': None, 'free': dict(free), 'auc': [], 'wait': [], 'idle': [], 'booked': [],
            'sold': 0, 'none': 0, 'err': 0}


def _sort(pages, now, out, part):
    """Разложить страницы номеров по группам. part — чем номер отличается от соседей по списку: цифры или буквы."""
    for p in pages:
        if p is None or not p['ok']:
            out['err'] += 1
            continue
        c = card.build(p, now)
        d, st = part(p['sym']), p['status']
        if p['type'] == 'auction' and st == 'in_auction':
            nxt = min((srs.ts(a['at']) for a in p['auctions'] if a['st'] == 'planned'), default=None)
            v2 = any((a['verif'] or 0) >= 2 for a in p['auctions'] if a['st'] == 'planned')
            out['auc'].append({'d': d, 'start': p['price'], 'at': nxt, 'rel': c['rel'], 'v2': v2})
        elif p['type'] == 'auction' and st == 'waiting':
            last = bool(c['rel']) and not any(x['k'] in ('fc', 'plan') for x in c['tl'])
            out['wait'].append({'d': d, 'start': p['price'], 'np': c['np'], 'rel': c['rel'], 'last': last})
        elif p['type'] == 'auction' and st == 'free':
            # статус «Бош», но номер аукционный: торги по нему ещё не начинались, без аукциона его не купить
            out['idle'].append({'d': d, 'start': p['price']})
        elif st == 'booked':
            b = max(p['bookings'], key=lambda b: srs.ts(b['at'])) if p['bookings'] else None
            out['booked'].append({'d': d, 'price': p['price2'] or p['price'], 'due': srs.ts(b['due']) if b else None})
        elif st == 'free':
            out['free'][p['sym']] = p['price2'] or p['price']
        elif st == 'sold':
            out['sold'] += 1
        else:
            out['none'] += 1


def collect(region, series, now, digits=None):
    """Собрать серию: список свободных + страницы аукционных цифр. -> dict с группами или {'msg': ...}."""
    digits = digits or AUCTION_DIGITS
    probe = srs.fetch_plate(region, digits[0] + series, timeout=15, tries=2)
    if not probe['ok']:
        return {'msg': probe['msg']}
    free = srs.fetch_free(region, '', series)
    todo = [d + series for d in digits[1:] if d + series not in free]
    out = _blank(free)
    _sort(([probe] if digits[0] + series not in free else []) + _pages(region, todo), now, out, lambda sym: sym[:3])
    return out


def collect_digits(region, digits, now):
    """Собрать все номера с одними цифрами. -> dict с теми же группами, что у серии.

    Перебрать 17 тысяч серий нельзя, поэтому источник — списки портала: свободные, забронированные и назначенные
    торги. Страницы открываем только тем, чьи торги сорвались за последние дни: это номера между торгами
    и те, что вот-вот выйдут без аукциона. Аукционных номеров, по которым торгов ещё не было, в списках нет.
    """
    with ThreadPoolExecutor(3) as ex:
        f_free = ex.submit(srs.fetch_free, region, digits)
        f_book = ex.submit(srs.fetch_free, region, digits, '', 'booked')
        f_fail = ex.submit(srs.fetch_auctions, region, digits, '', srs.AUC_FAILED, now - dt.timedelta(days=RECENT))
        plan = {a['sym']: a for a in srs.fetch_auctions(region, digits)}
        free, booked, failed = f_free.result(), f_book.result(), f_fail.result()
    out = _blank(free)
    todo = sorted(set(booked) | {a['sym'] for a in failed} - set(plan) - set(free))
    pages = _pages(region, todo)
    _sort(pages, now, out, lambda sym: sym[3:])
    out['sold'] = out['none'] = 0       # это случайная выборка за неделю, а не счёт по всем номерам
    read = {p['sym'] for p in pages if p and p['ok']}
    out['booked'] += [{'d': sym[3:], 'price': price, 'due': None} for sym, price in booked.items() if sym not in read]
    have = {x['d'] for x in out['auc']}
    out['auc'] += [{'d': sym[3:], 'start': a['start'], 'at': a['at'], 'rel': None, 'v2': a['verif'] >= 2}
                   for sym, a in plan.items() if sym[3:] not in have]
    return out


def _by(items, key):
    g = collections.defaultdict(list)
    for x in items:
        g[key(x)].append(x)
    return g


def _rows(price, cmds):
    return ['  <b>%s</b>: %s' % (card.money(price), ' '.join(cmds[i:i + PER_ROW])) for i in range(0, len(cmds), PER_ROW)]


def fmt(region, key, data, now):
    """Текст списка для Telegram (HTML): key — три буквы серии или три цифры. Номера — командами: нажал и получил карточку."""
    by_digits = key.isdigit()
    cmd = (lambda x: card.pcmd(region, key + x)) if by_digits else (lambda x: card.pcmd(region, x + key))
    part = (lambda sym: sym[3:]) if by_digits else (lambda sym: sym[:3])
    row = lambda price, ds: _rows(price, [cmd(d) for d in sorted(ds)])
    desc = lambda v: -(v or 0)
    out = ['<b>%s %s</b> · %s %s' % ('Цифры' if by_digits else 'Серия', key, region, srs.REGIONS.get(region, ''))]
    if data.get('msg'):
        return out[0] + '\n' + html.escape(data['msg'])
    free, auc, wait, idle, booked = data['free'], data['auc'], data['wait'], data['idle'], data['booked']
    if free:
        out.append('\n🟢 <b>Свободны без аукциона — %d</b>, сом' % len(free))
        g = _by(free.items(), lambda kv: kv[1])
        for price in sorted(g, reverse=True):
            out += row(price, [part(sym) for sym, _ in g[price]])
    if auc:
        out.append('\n🔨 <b>На аукционе — %d</b>, старт в сомах' % len(auc))
        far = now.date() + dt.timedelta(days=NEAR) if by_digits else dt.date.max

        def group(x):       # -> (день, дальние торги пачкой?, прогноз выхода)
            day = x['at'].date() if x['at'] else dt.date.max
            if far < day < dt.date.max:
                return max(day.replace(day=1), far), True, dt.date.max
            return day, False, x['rel'] or dt.date.max

        g = _by(auc, group)
        for day, pack, rel in sorted(g):
            items = g[(day, pack, rel)]
            if pack:
                lo, hi = min(x['at'] for x in items), max(x['at'] for x in items)
                head = 'Торги %s' % card.dm(lo, now) + (' – %s' % card.dm(hi, now) if hi.date() != lo.date() else '')
            else:
                head = 'Торги %s' % card.dm(day, now) if day != dt.date.max else 'Торги идут'
            if rel != dt.date.max:
                head += ' → без аукциона ≈ %s' % card.dm(rel, now)
            out.append(head)
            gp = _by(items, lambda x: x['start'])
            for price in sorted(gp, key=desc):
                out += _rows(price, [cmd(x['d']) + (' ⚠' if x['v2'] else '')
                                     for x in sorted(gp[price], key=lambda x: x['d'])])
    if wait:
        out.append('\n⏳ <b>Между торгами — %d</b>' % len(wait))
        # у цифр таких номеров десятки, и прогноз у каждого свой — по датам делим только тех, кто уже выходит
        g = _by(wait, lambda x: (not x['last'], (x['rel'] if x['last'] or not by_digits else None) or dt.date.max))
        for notlast, rel in sorted(g):
            items = g[(notlast, rel)]
            if not notlast:
                out.append('Выйдут без аукциона %s в 00:00, цена в сомах' % card.dm(rel, now))
                gp = _by(items, lambda x: x['np'])
            else:
                head = 'Новые торги назначат в понедельник %s' % card.dm(forecast.next_mon(now.date()), now)
                if rel != dt.date.max:
                    head += ' → без аукциона ≈ %s' % card.dm(rel, now)
                out.append(head + ', старт в сомах')
                gp = _by(items, lambda x: x['start'])
            for price in sorted(gp, key=desc):
                out += row(price, [x['d'] for x in gp[price]])
    if idle:
        out.append('\n⚪ <b>Аукционные, торги ещё не начались — %d</b>, старт в сомах' % len(idle))
        gp = _by(idle, lambda x: x['start'])
        for price in sorted(gp, key=desc):
            out += row(price, [x['d'] for x in gp[price]])
    if booked:
        out.append('\n🟠 <b>Забронированы — %d</b>' % len(booked))
        out += ['  %s — %s сом, оплата до %s' % (cmd(x['d']), card.money(x['price']),
                                              card.dm(x['due'], now, True) if x['due'] else '?') for x in booked]
    if not (free or auc or wait or idle or booked):
        out.append('\nСвободных и аукционных номеров %s сейчас нет.' % ('с этими цифрами' if by_digits else 'в этой серии'))
    tail = []
    if data['sold']:
        tail.append('продано аукционных: %d' % data['sold'])
    if data['none']:
        tail.append('не выставлены: %d' % data['none'])
    if data['err']:
        tail.append('не открылись: %d' % data['err'])
    if tail:
        out.append('\n' + ' · '.join(tail).capitalize())
    notes = []
    if auc or idle or any(x['last'] is False for x in wait):
        notes.append('Без аукциона цена на 20% ниже старта.')
    if idle:
        notes.append('⚪ — номер продаётся только с торгов, а их по нему ещё не назначали: без аукциона его не купить.')
    if any(x['v2'] for x in auc):
        notes.append('⚠ — на торги записались 2+ подтверждённых участника.')
    if by_digits and (auc or wait):
        notes.append('Аукционных номеров, по которым торги ещё не начались, здесь нет: портал не отдаёт их списком — '
                     'смотри серию.')
    notes.append('Нажми на номер — пришлю карточку с графиком.')
    out.append('\n' + ' '.join(notes))
    return '\n'.join(out)
