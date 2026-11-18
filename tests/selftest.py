#!/usr/bin/env python3
"""Сухой прогон сценариев бота: Telegram и портал подменены, сеть не нужна. Запуск: python3 tests/selftest.py"""
import base64
import copy
import datetime as dt
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
TMP = tempfile.mkdtemp(prefix='nomer-bot-test-')
os.environ['DATA_DIR'] = TMP
os.environ.setdefault('BOT_TOKEN', 'test')

import bot       # noqa: E402
import forecast  # noqa: E402
import series    # noqa: E402
import srs       # noqa: E402

sent = []


def fake_tg(method, payload=None, files=None, timeout=60):
    p = payload or {}
    sent.append((method, p.get('text') or p.get('caption') or ''))
    if method == 'sendWidget':
        return {'ok': False, 'description': 'Bad Request: INPUT_MEDIA_WIDGET_NOT_ALLOWED'}
    return {'ok': True, 'result': {'message_id': 9}}


bot.tg = fake_tg
bot.log = lambda *a: None
NOW = [dt.datetime(2026, 12, 13, 20, 0)]
bot.now = lambda: NOW[0]
CID = 1


def texts():
    out = [t for m, t in sent if m in ('sendMessage', 'sendPhoto', 'editMessageText')]
    sent.clear()
    return out


def auc(at, st, verif=0, total=0):
    return {'at': at, 'verif': verif, 'total': total, 'st_raw': '', 'st': st, 'id': 555 if st == 'planned' else None}


def plate(**kw):
    d = {'ok': True, 'region': '01', 'sym': '001HAI', 'digits': '001', 'letters': 'HAI', 'type': 'auction',
         'status': 'waiting', 'status_raw': '', 'price': 62000, 'price2': None, 'sold': None, 'bookings': [],
         'auctions': [auc('07.12.2026 12:00', 'failed'), auc('16.11.2026 12:15', 'failed'),
                      auc('26.10.2026 14:50', 'failed'), auc('05.10.2026 16:00', 'failed'),
                      auc('14.09.2026 15:20', 'failed')]}
    d.update(kw)
    return d


cur = [plate()]
calls = []
srs.fetch_plate = lambda region, sym, timeout=20, tries=3: (calls.append(('plate', region, sym)) or copy.deepcopy(cur[0]))
msg = lambda text: bot.on_message({'chat': {'id': CID}, 'text': text})
cb = lambda data: bot.on_callback({'id': 'q', 'message': {'chat': {'id': CID}, 'message_id': 5}, 'data': data})

# правило отсечки на четырёх реальных циклах: последние торги по правилу = фактически последние
for first, gaps in [(dt.datetime(2024, 9, 5), [14, 14, 14, 15, 14, 14, 13]), (dt.datetime(2025, 4, 28), [21] * 4),
                    (dt.datetime(2026, 2, 3), [16, 14, 14, 14, 14, 14]), (dt.datetime(2025, 9, 18), [18, 21, 21, 21, 21])]:
    ds = [first]
    for g in gaps:
        ds.append(ds[-1] + dt.timedelta(days=g))
    cut = forecast.cutoff(ds[0])
    assert next(d for d in ds if forecast.is_last(d, cut)) == ds[-1], first

# карточка: торги закончились, выход 14.12 в 00:00
msg('001 HAI')
t = texts()
assert 'выходит без аукциона' in t[-1] and '14.12' in t[-1] and '/HAI' in t[-1], t

# слежение, частота проверок, напоминания
cb('w|01|001HAI')
texts()
e = bot.S.d['watch']['01-001HAI']
assert e['hot'] == ['2026-12-14T00:00:00'], e['hot']
for when, want in [('2026-12-13 20:00', 600), ('2026-12-13 23:52', 30), ('2026-12-13 23:59:20', 2),
                   ('2026-12-14 00:00:01', 2), ('2026-12-14 00:05', 10), ('2026-12-14 00:30', 120)]:
    assert round(bot.delay_for(e, dt.datetime.fromisoformat(when))) == want, when
assert not bot.remind(e, dt.datetime(2026, 12, 13, 20, 0))
r = bot.remind(e, dt.datetime(2026, 12, 13, 21, 1))
assert len(r) == 1 and 'через 3 ч' in r[0][1], r
assert not bot.remind(e, dt.datetime(2026, 12, 13, 21, 2))
assert 'мин' in bot.remind(e, dt.datetime(2026, 12, 13, 23, 51))[0][1]

# полночь: вышел → забронирован → снова свободен → продан
NOW[0] = dt.datetime(2026, 12, 14, 0, 0, 2)
bot.check_watch('01-001HAI')
assert not texts()
cur[0] = plate(type='plain', status='free', price2=49600)
bot.check_watch('01-001HAI')
assert 'вышел без аукциона' in texts()[0]
book = {'sum': 49600, 'at': '14.12.2026 00:00', 'due': '15.12.2026 00:00', 'paid': False}
cur[0] = plate(type='plain', status='booked', price2=49600, bookings=[book])
bot.check_watch('01-001HAI')
assert 'забронирован' in texts()[0]
assert bot.S.d['watch']['01-001HAI']['hot'] == ['2026-12-15T00:00:00']
NOW[0] = dt.datetime(2026, 12, 15, 0, 0, 3)
cur[0] = plate(type='plain', status='free', price2=49600, bookings=[book])
bot.check_watch('01-001HAI')
assert 'снова свободен' in texts()[0]
cur[0] = plate(type='plain', status='sold', price2=49600, sold=49600, bookings=[dict(book, paid=True)])
bot.check_watch('01-001HAI')
assert 'продан' in texts()[0] and '01-001HAI' not in bot.S.d['watch']

# аукцион: торги не состоялись (не последние) → новые торги и 2 подтверждённых участника
NOW[0] = dt.datetime(2026, 11, 17, 19, 0)
lvs = dict(sym='777LVS', digits='777', letters='LVS')
cur[0] = plate(status='in_auction', auctions=[auc('18.11.2026 09:00', 'planned'), auc('27.10.2026 16:25', 'failed', 0, 2)], **lvs)
cb('w|01|777LVS')
assert '11.02.2027' in texts()[-1]
NOW[0] = dt.datetime(2026, 11, 18, 10, 0)
cur[0] = plate(status='waiting', auctions=[auc('18.11.2026 09:00', 'failed'), auc('27.10.2026 16:25', 'failed', 0, 2)], **lvs)
bot.check_watch('01-777LVS')
assert 'Новые назначат' in texts()[0]
NOW[0] = dt.datetime(2026, 11, 23, 10, 0)
cur[0] = plate(status='in_auction', auctions=[auc('09.12.2026 09:00', 'planned', 2, 3), auc('18.11.2026 09:00', 'failed'),
                                              auc('27.10.2026 16:25', 'failed', 0, 2)], **lvs)
bot.check_watch('01-777LVS')
t = texts()
assert len(t) == 2 and 'назначены торги' in t[0] and 'подтверждённых' in t[1], t

# маска: первый опрос молчит, новый номер — сообщение
free = [{'777ABC': 49600}]
srs.fetch_free = lambda r, d='', s='': (calls.append(('free', r, d, s)) or dict(free[0]))
msg('/sub 777')
assert 'Маска' in texts()[0]
bot.check_mask('01|777|')
assert not texts()
free[0] = {'777ABC': 49600, '777XYZ': 49600}
bot.check_mask('01|777|')
t = texts()
assert 'Свободны без аукциона' in t[0] and '/777XYZ' in t[0], t
assert bot.mask_delay(dt.datetime(2026, 11, 24, 0, 1)) == 40 and bot.mask_delay(dt.datetime(2026, 11, 23, 10, 0)) == 900

# аукционный номер со статусом «свободен» и без торгов — не свободная продажа: ни в карточке, ни в серии, ни в слежении
NOW[0] = dt.datetime(2026, 11, 18, 12, 0)
idle = dict(sym='222NAN', digits='222', letters='NAN', status='free', price=42000, auctions=[])
cur[0] = plate(**idle)
msg('222 NAN')
t = texts()[-1]
assert 'Торги не начались' in t and 'Свободен' not in t and '/NAN' in t and '/222' in t, t
cb('w|01|222NAN')
assert 'назначат торги' in texts()[-1]
cur[0] = plate(type='plain', price2=33600, **idle)
bot.check_watch('01-222NAN')
assert 'вышел без аукциона' in texts()[0]
cur[0] = plate(**idle)
bot.check_watch('01-222NAN')
t = texts()
assert len(t) == 1 and 'не назначены' in t[0] and 'свободен' not in t[0], t
cb('u|01|222NAN')
texts()
real_free, real_plate = srs.fetch_free, srs.fetch_plate
srs.fetch_free = lambda r, d='', s='', status='notSold': {'012NAN': 12000}
srs.fetch_plate = lambda region, sym, timeout=20, tries=3: (
    plate(sym=sym, status='in_auction', auctions=[auc('02.12.2026 12:55', 'planned', 2, 3)]) if sym[:3] == '001'
    else plate(**dict(idle, sym=sym, price=42000 if sym[:3] == '222' else 32000)))
text = series.fmt('01', 'NAN', series.collect('01', 'NAN', NOW[0], ['001', '222', '100']), NOW[0])
assert 'Свободны без аукциона — 1<' in text and 'торги ещё не начались — 2<' in text, text
assert text.index('/012NAN') < text.index('На аукционе — 1<') < text.index('/001NAN ⚠') < text.index('/222NAN') < text.index('/100NAN'), text

# все номера с одними цифрами: списки портала + страницы только тем, у кого торги сорвались на днях
planned = [{'sym': '777AAA', 'start': 62000, 'at': dt.datetime(2026, 11, 19, 9, 5), 'verif': 2, 'total': 3},
           {'sym': '777BBB', 'start': 50000, 'at': dt.datetime(2027, 3, 4, 10, 0), 'verif': 0, 'total': 0},
           {'sym': '777CCC', 'start': 62000, 'at': dt.datetime(2027, 3, 25, 10, 0), 'verif': 0, 'total': 0}]
failed = [dict(planned[0], sym=s, at=dt.datetime(2026, 11, 16, 12, 0)) for s in ('777AAA', '777WWW', '777ZZZ')]
srs.fetch_free = lambda r, d='', s='', status='notSold': {'777ZZZ': 49600} if status == 'notSold' else {'777KKK': 49600}
srs.fetch_auctions = lambda r, d='', s='', status=1, since=None: planned if status == srs.AUC_PLANNED else failed
calls.clear()
srs.fetch_plate = lambda region, sym, timeout=20, tries=3: (calls.append(sym) or (
    plate(sym=sym, type='plain', status='booked', price2=49600,
          bookings=[{'sum': 49600, 'at': '18.11.2026 00:01', 'due': '19.11.2026 00:01', 'paid': False}])
    if sym == '777KKK' else plate(sym=sym, auctions=[auc('16.11.2026 12:00', 'failed'), auc('26.10.2026 12:00', 'failed')])))
data = series.collect_digits('01', '777', NOW[0])
assert sorted(calls) == ['777KKK', '777WWW'], calls      # на торгах и свободные страниц не требуют
text = series.fmt('01', '777', data, NOW[0])
for part in ('<b>Цифры 777</b>', 'Свободны без аукциона — 1<', '/777ZZZ', 'На аукционе — 3<', 'Торги 19.11\n', '/777AAA ⚠',
             'Торги 04.03.2027 – 25.03.2027', 'Между торгами — 1<', '/777WWW', 'Забронированы — 1<', 'оплата до 19.11 00:01'):
    assert part in text, (part, text)
srs.fetch_free, srs.fetch_plate = real_free, real_plate

# длинный список режется на страницы: не больше 100 нажимаемых номеров в сообщении, заголовок группы повторяется
big = series.fmt('01', '123', dict(series._blank({'123%s' % (chr(65 + i // 26) + chr(65 + i % 26) + 'A'): 6750
                                                  for i in range(200)})), NOW[0])
pages = bot.paginate(big)
assert len(pages) == 3 and all(p.count('/123') <= 90 and p.startswith('<b>Цифры 123</b>') for p in pages), pages
assert pages[1].split('\n')[1].startswith('… 🟢') and sum(p.count('/123') for p in pages) == 200, pages[1]
assert [len(k) for k in bot.list_kb('01', '123', 1, 3)] == [2, 2] and [len(k) for k in bot.list_kb('01', '123', 0, 1)] == [2]

# разбор ввода и быстрый доступ через «/»
series.collect = lambda region, ser, now, digits=None: (calls.append(('series', region, ser)) or {'msg': 'stub'})
series.collect_digits = lambda region, digits, now: (calls.append(('digits', region, digits)) or series._blank({}))
for text, want in [('/777LVS', ('plate', '01', '777LVS')), ('/02_001ABC', ('plate', '02', '001ABC')),
                   ('01 KG 777 LVS', ('plate', '01', '777LVS')), ('/EEF', ('series', '01', 'EEF')),
                   ('/02_eeg', ('series', '02', 'EEG')), ('EEH', ('series', '01', 'EEH')), ('02 eek', ('series', '02', 'EEK')),
                   ('777', ('digits', '01', '777')), ('/070', ('digits', '01', '070')), ('/02_001', ('digits', '02', '001')),
                   ('2 555', ('digits', '02', '555')), ('03 KG 010', ('digits', '03', '010'))]:
    calls.clear()
    msg(text)
    assert calls and calls[0] == want, (text, calls)
assert 'с этими цифрами сейчас нет' in texts()[-1]
for text in ('привет мир', '01777', '7777', '77'):
    calls.clear()
    msg(text)
    assert not calls and 'Не вижу номера' in texts()[0], text

# регион по умолчанию: при первом /start спрашиваем один раз, дальше всё без региона идёт в выбранный
msg('/start')
t = texts()
assert len(t) == 2 and '01 г. Бишкек' in t[0] and 'выбери регион' in t[1], t
cb('g|02')
assert '02 г. Ош' in texts()[-1] and bot.region_of(CID) == '02'
for text, want in [('777', ('digits', '02', '777')), ('EEF', ('series', '02', 'EEF')), ('888 KYH', ('plate', '02', '888KYH')),
                   ('01 444', ('digits', '01', '444')), ('01 888 KYH', ('plate', '01', '888KYH')),
                   ('/333', ('digits', '01', '333')), ('/888KYH', ('plate', '01', '888KYH'))]:   # ссылка без региона — всегда 01
    calls.clear()
    msg(text)
    assert calls and calls[0] == want, (text, calls)
msg('/sub 555')
assert '02|555|' in bot.S.d['masks']
texts()
msg('/start')
t = texts()
assert len(t) == 1 and '02 г. Ош' in t[0], t      # второй раз не спрашиваем
msg('/region')
t = texts()[-1]
assert 'Выбери другой' in t and '02 г. Ош' in t, t
msg('/region 1')
assert '01 г. Бишкек' in texts()[-1] and bot.region_of(CID) == '01'
calls.clear()
msg('999')
assert calls[0] == ('digits', '01', '999'), calls
texts()

# листание: страница берётся из уже собранного списка и правит то же сообщение; подписка на цифры кнопкой
bot.LISTS[('01', '123')] = (0, pages)
calls.clear()
cb('p|01|123|2')
assert not calls and sent[-1][0] == 'editMessageText' and texts()[-1] == pages[2]
cb('p|01|123|99')
assert texts()[-1] == pages[2]
cb('m|01|777')
assert 'Маска' in texts()[-1] and '01|777|' in bot.S.d['masks']
assert bot.find_plates('888KYH и 111HAH') == [('01', '888KYH'), ('01', '111HAH')]
assert bot.parse_mask('02 *** HAH') == ('02', '', 'HAH') and bot.parse_mask('7777') is None
msg('/list')
assert '/777LVS' in texts()[0]

# учёт входящих: кто, сколько раз, что спрашивал; /stats отвечает только владельцу
ch = bot.S.d['chats'][str(CID)]
n0, cb0 = ch['n'], ch['cb']
bot.on_message({'chat': {'id': CID}, 'from': {'first_name': 'Тест', 'username': 'tester'}, 'text': '888 <KYH>'})
cb('c|01|888KYH')
texts()
assert (ch['n'], ch['cb'], ch['name'], ch['q'][-1][1]) == (n0 + 1, cb0 + 1, '?', '888 <KYH>') and len(ch['q']) <= bot.KEEP, ch
msg('/stats')
assert 'Номера' in texts()[0]                       # чужому — обычная справка
bot.ADMIN = CID
bot.on_message({'chat': {'id': CID}, 'from': {'first_name': 'Тест', 'username': 'tester'}, 'text': '/stats'})
t = texts()[0]
assert 'Кто писал боту' in t and 'Тест @tester' in t and 'сообщений %d' % (n0 + 3) in t and '888 &lt;KYH&gt;' in t, t

# перенос состояния через STATE_SEED
bot.S.save(force=True)
os.remove(os.path.join(TMP, 'state.json'))
os.environ['STATE_SEED'] = base64.b64encode(json.dumps({'watch': {'01-111HAH': {'chats': [5]}}, 'offset': 7}).encode()).decode()
st = bot.Store()
assert list(st.d['watch']) == ['01-111HAH'] and st.d['offset'] == 7

print('OK: все сценарии прошли')
