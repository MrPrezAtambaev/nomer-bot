"""Карточка номера: что с ним сейчас, когда выйдет без аукциона, какие моменты ловить."""
import datetime as dt
import html

import forecast
import srs

DISCOUNT = 0.8      # цена без аукциона = 80% стартовой
HALF_DAYS = 185     # −50% примерно через 185 дней после последних торгов
BASE = srs.BASE


def money(n):
    return '{:,}'.format(n).replace(',', ' ') if n is not None else '—'


def dm(d, now, time=False):
    s = d.strftime('%d.%m')
    if d.year != now.year:
        s += d.strftime('.%Y')
    if time and isinstance(d, dt.datetime):
        s += d.strftime(' %H:%M')
    return s


def plural(n, forms):
    n10, n100 = n % 10, n % 100
    f = forms[0] if n10 == 1 and n100 != 11 else forms[1] if 2 <= n10 <= 4 and not 12 <= n100 <= 14 else forms[2]
    return '%d %s' % (n, f)


TRY = ('попытка', 'попытки', 'попыток')


def pcmd(region, sym):
    """Номер командой: нажал в сообщении — получил карточку."""
    return '/%s' % sym if region == '01' else '/%s_%s' % (region, sym)


def scmd(region, key):
    """Список командой: key — три буквы (серия) или три цифры (все номера с ними)."""
    return '/%s' % key if region == '01' else '/%s_%s' % (region, key)


def title(p):
    return '%s KG %s %s' % (p['region'], p['sym'][:3], p['sym'][3:])


def link(p):
    return srs.plate_url(p['region'], p['sym'])


def build(p, now):
    """p — разобранная страница номера (ok=True). -> карточка для подписи, виджета и картинки."""
    c = {'title': title(p), 'region': p['region'], 'sym': p['sym'], 'place': srs.REGIONS.get(p['region'], ''),
         'url': link(p), 'tone': 'hint', 'head': '', 'sub': [], 'rows': [], 'tl': [], 'hot': [],
         'rel': None, 'np': None, 'auction_id': None, 'state': p['status'], 'warn': None}
    auctions = [dict(a, dt=srs.ts(a['at'])) for a in p['auctions']]
    failed = sorted(a['dt'] for a in auctions if a['st'] == 'failed')
    planned = sorted((a for a in auctions if a['st'] == 'planned'), key=lambda a: a['dt'])
    live = [a for a in auctions if a['st'] == 'other' and abs((a['dt'] - now).days) <= 5]
    st = p['status']

    if p['type'] == 'auction' and st in ('in_auction', 'waiting'):
        fc = forecast.plan(failed, [a['dt'] for a in planned], now)
        np_ = round(p['price'] * DISCOUNT) if p['price'] else None
        c['np'] = np_
        if st == 'in_auction':
            c['tone'] = 'link'
            c['head'] = 'На аукционе · старт %s сом' % money(p['price'])
            if planned:
                a = planned[0]
                c['auction_id'] = a['id']
                c['sub'].append('Торги %s · участников %d, подтверждено %d'
                                % (dm(a['dt'], now, True), a['total'] or 0, a['verif'] or 0))
                if (a['verif'] or 0) >= 2:
                    c['warn'] = 'Торги состоятся: %d подтверждённых участника — номер, скорее всего, купят.' % a['verif']
            elif live:
                c['sub'].append('Торги идут или ждут оплаты победителя.')
        else:
            c['tone'] = 'warn'
            c['head'] = 'Между торгами · старт %s сом' % money(p['price'])
        if fc:
            for d in fc['c']:
                c['tl'].append({'d': d, 'k': 'fail'})
            for d, real in fc['att']:
                c['tl'].append({'d': d, 'k': 'plan' if real else 'fc'})
            if fc['c']:
                cc = fc['c']
                if len(cc) <= 2:
                    for d in cc:
                        c['rows'].append((dm(d, now), 'торги не состоялись', 'hint'))
                else:
                    c['rows'].append(('%s – %s' % (dm(cc[0], now), dm(cc[-1], now)),
                                      plural(len(cc), TRY) + ' — торги не состоялись', 'hint'))
            att = fc['att']
            shown = att if len(att) <= 4 else att[:2] + [None] + att[-1:]
            for x in shown:
                if x is None:
                    c['rows'].append(('…', 'ещё ' + plural(len(att) - 3, TRY) + ' по прогнозу', 'hint'))
                elif x[1]:
                    c['rows'].append((dm(x[0], now, True), 'торги назначены', 'text'))
                else:
                    c['rows'].append((dm(x[0], now), 'торги по прогнозу', 'hint'))
            if fc['off']:
                c['warn'] = c['warn'] or 'Этот номер торгуется дольше обычных трёх месяцев — дату выхода предсказать нельзя.'
            elif fc['rel']:
                rel = fc['rel']
                c['rel'] = rel
                relt = dt.datetime.combine(rel, dt.time(0, 0))
                c['tl'].append({'d': relt, 'k': 'rel'})
                c['rows'].append((dm(rel, now) + ' 00:00', 'без аукциона ≈ %s сом' % money(np_), 'good'))
                if st == 'waiting' and not fc['att']:
                    c['head'] = 'Торги закончились — выходит без аукциона'
                    if relt <= now:
                        c['sub'].append('По правилу уже должен выйти — проверяю страницу.')
                    else:
                        c['sub'].append('Выйдет %s в 00:00 за ≈ %s сом (−20%%).' % (dm(rel, now), money(np_)))
                    c['tone'] = 'good'
                else:
                    c['sub'].append('Без аукциона: %s в 00:00 за ≈ %s сом (−20%%), если не купят на торгах.'
                                    % (dm(rel, now), money(np_)))
                if relt > now - dt.timedelta(hours=6):
                    c['hot'].append(relt)
            if st == 'waiting' and fc['att'] and not fc['off']:
                c['sub'].insert(0, 'Новые торги назначат в понедельник %s.' % dm(forecast.next_mon(now.date()), now))
        elif st == 'waiting':
            c['sub'].append('Истории торгов нет — дату выхода пока не посчитать.')

    elif p['type'] == 'auction' and st == 'sold':
        c['tone'] = 'bad'
        a = next((a for a in auctions if a['st'] == 'sold'), None)
        c['head'] = 'Продан на аукционе · %s сом' % money(p['sold'])
        c['sub'].append('Старт %s сом%s.' % (money(p['price']), (', торги ' + dm(a['dt'], now)) if a else ''))

    elif p['type'] == 'auction' and st == 'free':
        # статус «Бош», но тип аукционный: в свободной продаже номера нет, торги по нему ещё не начинались
        c['state'] = 'idle'
        c['head'] = 'Торги не начались · старт %s сом' % money(p['price'])
        c['sub'].append('Номер аукционный: без торгов его не купить, а торги по нему ещё не назначены.')
        c['sub'].append('Участие в аукционе — через личный кабинет портала.')

    elif st == 'free':
        c['tone'] = 'good'
        cur = p['price2'] or p['price']
        c['head'] = 'Свободен, без аукциона · %s сом' % money(cur)
        if p['price2'] and p['price']:
            c['sub'].append('Было %s сом, скидка %d%%.' % (money(p['price']), round(100 - 100 * p['price2'] / p['price'])))
            if failed and round(p['price2'] / p['price'], 2) == DISCOUNT:
                half = failed[-1] + dt.timedelta(days=HALF_DAYS)
                if half > now:
                    c['sub'].append('Скидка 50%% (%s сом) ожидается ≈ %s.' % (money(round(p['price'] * 0.5)), dm(half, now)))
        c['sub'].append('Бронь — в личном кабинете портала, оплата в течение 24 часов.')
        unpaid = [b for b in p['bookings'] if not b['paid']]
        if unpaid:
            c['sub'].append('Бронировали %d раз и не оплатили, последний — %s.' % (len(unpaid), dm(srs.ts(unpaid[0]['at']), now)))

    elif st == 'booked':
        c['tone'] = 'warn'
        cur = p['price2'] or p['price']
        b = max(p['bookings'], key=lambda b: srs.ts(b['at'])) if p['bookings'] else None
        c['head'] = 'Забронирован · %s сом' % money(cur)
        if b:
            due = srs.ts(b['due'])
            c['rows'].append((dm(srs.ts(b['at']), now, True), 'бронь', 'text'))
            c['rows'].append((dm(due, now, True), 'срок оплаты', 'warn'))
            c['sub'].append('Не оплатят до %s — номер снова освободится.' % dm(due, now, True))
            if due > now - dt.timedelta(minutes=20):
                c['hot'].append(due)

    elif st == 'sold':
        c['tone'] = 'bad'
        b = next((b for b in p['bookings'] if b['paid']), None)
        c['head'] = 'Продан · %s сом' % money(p['sold'] or p['price2'] or p['price'])
        if b:
            c['sub'].append('Оплачен %s.' % dm(srs.ts(b['at']), now))

    else:
        c['head'] = 'На портале не продаётся'
        c['sub'].append('Обычная комбинация или номер ещё не выставлен.')
    return c


EMOJI = {'good': '🟢', 'warn': '🟠', 'bad': '🔴', 'link': '🔨', 'hint': '⚪'}


def caption(c, extra=None):
    """Подпись под карточкой: полный текст, читается и без виджета."""
    e = html.escape
    out = ['<b>%s</b> · %s' % (e(c['title']), e(c['place'])), '%s %s' % (EMOJI[c['tone']], e(c['head']))]
    out += [e(x) for x in c['sub']]
    if c['warn']:
        out.append('⚠️ ' + e(c['warn']))
    if c['rows']:
        out.append('')
        mark = {'good': '🟢', 'warn': '🟠', 'hint': '▫️', 'text': '🔹'}
        out += ['%s <b>%s</b> — %s' % (mark.get(t, '▫️'), e(a), e(b)) for a, b, t in c['rows']]
    out += ['', 'Вся серия: %s · все с цифрами: %s' % (scmd(c['region'], c['sym'][3:]), scmd(c['region'], c['sym'][:3]))]
    if extra:
        out += ['', extra]
    return '\n'.join(out)


def snapshot(p, c):
    """Что сравниваем между проверками, чтобы заметить перемену."""
    planned = sorted(a['at'] for a in p['auctions'] if a['st'] == 'planned')
    v2 = any((a['verif'] or 0) >= 2 for a in p['auctions'] if a['st'] == 'planned')
    return {'type': p['type'], 'status': p['status'], 'price': p['price2'] or p['price'], 'sold': p['sold'],
            'book': [[b['at'], b['due'], b['paid']] for b in p['bookings'][:3]],
            'plan': planned, 'v2': v2, 'rel': c['rel'].isoformat() if c['rel'] else None,
            'nfail': sum(1 for a in p['auctions'] if a['st'] == 'failed')}


def changes(old, new, p, c, now):
    """Сообщения о том, что изменилось у номера между двумя снимками. -> [(важно?, текст)]."""
    out = []
    if not old:
        return out
    cur = money(new['price'])
    if old['status'] != new['status'] or old['type'] != new['type']:
        s = new['status']
        if s == 'free' and new['type'] == 'auction':     # аукционный без торгов — это не свободная продажа
            out.append((False, '▫️ <b>%s</b>: торги по номеру сейчас не назначены. Без аукциона он не продаётся.' % c['title']))
        elif s == 'free' and old['type'] == 'auction':
            out.append((True, '🟢 <b>%s вышел без аукциона</b> — свободен, %s сом. Бронируй сейчас.' % (c['title'], cur)))
        elif s == 'free':
            out.append((True, '🟢 <b>%s снова свободен</b> — %s сом. Бронируй сейчас.' % (c['title'], cur)))
        elif s == 'booked':
            b = p['bookings'][0] if p['bookings'] else None
            tail = (' Срок оплаты до %s — не оплатят, освободится, я напишу.' % dm(srs.ts(b['due']), now, True)) if b else ''
            lead = 'вышел без аукциона и сразу забронирован' if old['type'] == 'auction' else 'забронирован'
            out.append((True, '🟠 <b>%s %s</b>.%s' % (c['title'], lead, tail)))
        elif s == 'sold':
            out.append((True, '🔴 <b>%s продан</b> за %s сом. Слежение снято.' % (c['title'], money(new['sold'] or new['price']))))
        elif s == 'waiting' and old['status'] == 'in_auction':
            if c['rel'] and not any(x['k'] in ('fc', 'plan') for x in c['tl']):
                out.append((True, '⏳ <b>%s</b>: торги не состоялись, и это были последние. Без аукциона выйдет %s в 00:00 за ≈ %s сом — напомню.'
                            % (c['title'], dm(c['rel'], now), money(c['np']))))
            else:
                out.append((False, '▫️ <b>%s</b>: торги не состоялись. Новые назначат в понедельник.' % c['title']))
        elif s == 'in_auction':
            a = new['plan'][0] if new['plan'] else None
            out.append((False, '📅 <b>%s</b>: назначены торги%s.%s' % (
                c['title'], (' ' + dm(srs.ts(a), now, True)) if a else '',
                (' Без аукциона по прогнозу — %s в 00:00.' % dm(c['rel'], now)) if c['rel'] else '')))
        else:
            out.append((False, '▫️ <b>%s</b>: статус изменился — %s.' % (c['title'], c['head'])))
    else:
        if new['plan'] != old['plan'] and new['plan']:
            out.append((False, '📅 <b>%s</b>: торги %s.%s' % (
                c['title'], dm(srs.ts(new['plan'][0]), now, True),
                (' Без аукциона по прогнозу — %s в 00:00.' % dm(c['rel'], now)) if c['rel'] else '')))
        if new['status'] == 'booked' and new['book'][:1] != old['book'][:1] and new['book']:
            out.append((True, '🟠 <b>%s</b>: новая бронь, срок оплаты до %s.' % (c['title'], dm(srs.ts(new['book'][0][1]), now, True))))
        if new['status'] == 'free' and new['price'] != old['price']:
            out.append((True, '💸 <b>%s</b>: цена изменилась — %s сом (было %s).' % (c['title'], cur, money(old['price']))))
    if new['v2'] and not old.get('v2'):
        out.append((True, '⚠️ <b>%s</b>: на торги записались 2+ подтверждённых участника — номер, скорее всего, купят на аукционе.' % c['title']))
    return out
