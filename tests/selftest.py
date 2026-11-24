#!/usr/bin/env python3
"""Сухой прогон сценариев бота: Telegram и портал подменены, сеть не нужна. Запуск: python3 tests/selftest.py"""
import base64
import copy
import datetime as dt
import json
import os
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
TMP = tempfile.mkdtemp(prefix='nomer-bot-test-')
os.environ['DATA_DIR'] = TMP
os.environ.setdefault('BOT_TOKEN', 'test')

import bot       # noqa: E402
import forecast  # noqa: E402
import nice      # noqa: E402
import series    # noqa: E402
import srs       # noqa: E402

REAL_FREE, REAL_AUCTIONS, REAL_ALL = srs.fetch_free, srs.fetch_auctions, srs.fetch_all_free      # дальше их подменяют

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

# красивые номера: оценка, подборки, кнопки
assert nice.rate('777NAN')[0] > nice.rate('777EEF')[0] > nice.rate('358EEF')[0]
assert nice.rate('001AAA')[0] > nice.rate('123AAA')[0] > nice.rate('358AAA')[0]
assert nice.rate('404NAN') == (44 + 70 + 18, 'нан, хлеб · зеркальный'), nice.rate('404NAN')
assert nice.rate('777HAH')[1] == 'НАН, хлеб · тройка' and nice.rate('358QXZ') == (0, '')
assert nice.rate('345BOS')[1] == 'босс · по порядку' and nice.rate('570WWW')[1] == 'три одинаковые · Lexus LX 570'
assert all(len(s) == 3 and s.isalpha() and s.isupper() and s.isascii() and g for s, (_, g) in nice.DICT.items())
assert not set(nice.DICT) & nice.BLOCKED and len(nice.DICT) == sum(
    len([x for x in t.replace('\n', '|').split('|') if x.strip()]) for t in nice.WORDS.values())      # без повторов
for n in range(1, 1000):
    assert 0 <= nice.digits('%03d' % n)[0] <= 100, n
pool_ = {'777NAN': 49600, '777KOT': 49600, '777QXZ': 33600, '404NAN': 12000, '505NAN': 12000, '606NAN': 12000,
         '786AZA': 6000, '316TTT': 15000, '358BMW': 1500, '358QXZ': 1500}
syms = lambda cat: [r[0] for r in nice.pick(cat, pool_)]
assert syms('top') == ['777NAN', '404NAN', '786AZA'], syms('top')     # одна серия — не больше двух, 777KOT запрещён
assert syms('cheap') == ['786AZA'] and syms('num') == ['777NAN', '777QXZ'], (syms('cheap'), syms('num'))
assert syms('mir') == ['777NAN', '404NAN', '505NAN', '606NAN'] and syms('tri') == ['316TTT'] and syms('auto') == ['358BMW']
assert syms('name') == ['786AZA'] and syms('cool') == [] and syms('kg')[0] == '777NAN'
many = {a + b + a + c * 3: 31500 for a, b, c in zip('12345678' * 3, '345678912' * 3, 'ABCDEFGHIJKLMNOPQRSTUVWX')}
assert len(many) == 24 and len(nice.pick('tri', many)) == 24 and len(nice.pick('top', many)) == nice.TRIPLES
free_ = [dict(pool_)]
srs.fetch_all_free = lambda region: (calls.append(('all', region)) or dict(free_[0]))
calls.clear()
for text, want in [('/blat', '01'), ('блатные', None), ('Красивые номера', None), ('/blat 2', '02')]:
    calls.clear()
    msg(text)
    t = texts()[-1]
    assert calls == ([('all', want)] if want else []) and 'Лучшие' in t, (text, calls, t)       # второй раз — из запаса
assert '/02_777NAN — 49 600 · нан, хлеб · тройка' in t and '02 г. Ош' in t, t
cb('b|01|mir|0')
assert sent[-1][0] == 'editMessageText' and '/404NAN' in texts()[-1]
cb('b|01|cool|7')
assert 'нет' in texts()[-1]
calls.clear()
cb('f|01|top')                                       # обновляли меньше минуты назад — портал не трогаем
assert not calls and '/777NAN' in texts()[-1]
bot.NICE['01'] = (time.time() - 100,) + bot.NICE['01'][1:]
free_[0] = {'100%s%s%s' % (a, b, a): 25600 for a in 'ABCDEFGHIJ' for b in 'KLMNOP'}
cb('b|01|num|0')
assert not calls and '/777QXZ' in texts()[-1]
cb('f|01|num')
t = texts()
assert calls == [('all', '01')] and 'читаю все' in t[0] and t[-1].count('/100') == nice.PER_PAGE, (calls, t)
cb('b|01|num|2')
assert texts()[-1].count('/100') == 60 - 2 * nice.PER_PAGE
assert [len(k) for k in bot.nice_kb('01', 'num', 1, 3)] == [2] + [2] * 5 + [1] and len(bot.nice_kb('01', 'top', 0, 1)) == 6
assert all(len(b['callback_data']) <= 64 for k in bot.nice_kb('01', 'num', 1, 3) for b in k)
for bad in ('b|01|xxx|0', 'b|10|top|0', 'b|01|top|0|0', 'f|', 'b|'):
    cb(bad)
    assert not texts(), bad
bot.NICE.clear()


def broken(region):
    raise RuntimeError('нет связи')


srs.fetch_all_free = broken
msg('/blat')
assert 'не ответил' in texts()[-1] and not bot.NICE and not bot.NICE_LOCK.locked()

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

# ---------- клиент портала: списки одной сессией (поддельный портал, сеть не нужна) ----------

class FakePortal:
    """Ведёт себя как списки nomer.srs.kg: сессия по куке, все фильтры одной формой, таблица помнит свою страницу."""
    UUIDS = ['%08d-0000-0000-0000-000000000000' % i for i in range(10)]

    def __init__(self):
        self.data, self.sess, self.log, self.hook = {}, {}, [], None

    def render(self, kind, rows, total):
        if kind == 'plates':
            x = ''.join('<li class="ui-datalist-item"><b class="lp-numbers">%s</b><b class="lp-letters">%s</b>'
                        '<b class="lp-pub-price">12 000</b></li>' % (r[:3], r[3:]) for r in rows)
        else:
            x = ''.join('<tr data-ri="%d"><td></td><td></td><td>01 %s %s</td><td>50 000</td><td></td><td></td><td></td>'
                        '<td></td><td>%s</td></tr>' % (i, sym[:3], sym[3:], at) for i, (sym, at) in enumerate(rows))
        return x + ('rows:%d,rowCount:%d,page:0' % (srs.ROWS[kind], total) if total is not None else '')

    def handle(self, cookie, path, data):
        kind = path.strip('/').split('.')[0]
        if data is None:
            sid = 's%d' % (len(self.log) + 1)
            self.sess[sid] = {'key': None, 'first': 0}
            self.log.append('GET')
            return sid, ('<input name="javax.faces.ViewState" value="vs"/><select id="%s_input">%s</select>'
                         % (srs.REGION_FIELD[kind], ''.join('<option value="%s">' % u for u in self.UUIDS)))
        st = self.sess.get(cookie)
        if st is None:
            self.log.append('expired')
            return cookie, '<partial-response><redirect url="/error/expired.xhtml"></redirect></partial-response>'
        per = srs.ROWS[kind]
        if data.get('table_pagination'):
            st['first'] = int(data['table_first'])
            self.log.append('page')
            if self.hook:
                self.hook()
            return cookie, self.render(kind, self.data.get(st['key'], [])[st['first']:st['first'] + per], None)
        region = '%02d' % self.UUIDS.index(data[srs.REGION_FIELD[kind] + '_input'])
        st['key'] = (kind, region, data['j_idt53_input'].rsplit('@', 1)[-1], data['digits'], data['series'])
        self.log.append('set')
        rows = self.data.get(st['key'], [])
        return cookie, self.render(kind, rows[st['first']:st['first'] + per], len(rows))      # страница — прежняя!


class FakeHttp:
    def __init__(self):
        self.c, self.used = True, 0.0

    def close(self):
        self.c = None

    def send(self, path, data=None, timeout=20, tries=3, jar=None, method=None):
        self.used = time.monotonic()
        if method == 'HEAD':
            PORTAL.log.append('HEAD')
            return ''
        with PORTAL_LOCK:
            sid, body = PORTAL.handle((jar or {}).get('JSESSIONID'), path, data)
        if jar is not None:
            jar['JSESSIONID'] = sid
        return body


PORTAL, PORTAL_LOCK = FakePortal(), threading.Lock()
srs._Http, srs._throttle = FakeHttp, lambda: None
for k in srs._pool:
    srs._pool[k].clear()
plates = lambda digits, n: ['%s%s' % (digits, 'A' + chr(65 + i // 26) + chr(65 + i % 26)) for i in range(n)]
PORTAL.data = {('plates', '01', 'notSold', '777', ''): plates('777', 5),
               ('plates', '02', 'notSold', '777', ''): plates('777', 70),
               ('plates', '01', 'notSold', '001', ''): plates('001', 200),
               ('plates', '01', 'notSold', '', 'BBB'): ['114BBB', '124BBB', '373BBB'],
               ('plates', '01', 'booked', '777', ''): ['777MUK'],
               ('plates', '01', 'notSold', '', ''): plates('100', 800)}


def portal(fn, *a):
    PORTAL.log.clear()
    return fn(*a), ' '.join(PORTAL.log)


r, log = portal(REAL_FREE, '01', '777')
assert sorted(r) == plates('777', 5) and log == 'GET set', (r, log)                # первая маска: открыть и выставить
r, log = portal(REAL_FREE, '01', '', 'BBB')
assert sorted(r) == ['114BBB', '124BBB', '373BBB'] and log == 'set', (r, log)      # следующая — один запрос
r, log = portal(REAL_FREE, '01', '777', '', 'booked')
assert list(r) == ['777MUK'] and log == 'set', (r, log)
r, log = portal(REAL_FREE, '02', '777')
assert len(r) == 70 and log == 'set page', (len(r), log)                           # две страницы
# ловушка портала: таблица осталась на второй странице. Короткий список пришёл бы пустым — а пустой список у маски
# значит «все номера ушли», и на следующей проверке бот разослал бы их все как новые
r, log = portal(REAL_FREE, '01', '', 'BBB')
assert sorted(r) == ['114BBB', '124BBB', '373BBB'] and log == 'set page', (r, log)
r, log = portal(REAL_FREE, '02', '777')
assert len(r) == 70 and log == 'set page', (len(r), log)
r, log = portal(REAL_FREE, '01', '001')                                            # длинный пришёл бы не с начала
assert sorted(r) == plates('001', 200) and log == 'set page page page page', (len(r), log)
r, log = portal(REAL_FREE, '01', '123', 'ZZZ')
assert r == {} and log == 'set page', (r, log)                                     # пустая маска — честно пусто
PORTAL.sess.clear()                                                                # сессия на портале истекла
r, log = portal(REAL_FREE, '01', '777')
assert sorted(r) == plates('777', 5) and log == 'expired GET set', (r, log)
try:
    REAL_FREE('01')
    raise AssertionError('слишком широкая маска должна давать ошибку')
except RuntimeError as e:
    assert 'слишком широкая' in str(e), e
r, log = portal(REAL_FREE, '01', '777')
assert sorted(r) == plates('777', 5) and log == 'set', (r, log)                    # сессия после этого жива
assert len(srs._pool['plates']) == 1, srs._pool

# весь список свободных: несколько сессий разом, ничего не теряется
row_ = lambda i: '%03d%s%s%s' % (i % 999 + 1, chr(65 + i // 676 % 26), chr(65 + i // 26 % 26), chr(65 + i % 26))
everything = [row_(i) for i in range(1030)]
PORTAL.data.update({('plates', '03', 'notSold', '', ''): everything, ('plates', '04', 'notSold', '', ''): everything[:40]})
r, log = portal(REAL_ALL, '03')
assert sorted(r) == sorted(everything) and log.count('set') == 4 and len(srs._pool['plates']) <= srs.POOL_KEEP, (len(r), log)
r, log = portal(REAL_ALL, '04')
assert sorted(r) == sorted(everything[:40]) and log.count('page') <= 1, (len(r), log)
assert portal(REAL_ALL, '05')[0] == {}


def expire():                                        # сессии истекли посреди выкачки — открываем новые и продолжаем
    if PORTAL.log.count('page') == 6:
        PORTAL.hook = None
        PORTAL.sess.clear()


PORTAL.hook = expire
r, log = portal(REAL_ALL, '03')
assert sorted(r) == sorted(everything) and 'expired' in log, (len(r), log)


def shrink():                                        # пока читали, номера раскупили: последней страницы больше нет
    PORTAL.hook = None
    PORTAL.data[('plates', '03', 'notSold', '', '')] = everything[:1020]


PORTAL.hook = shrink
r, log = portal(REAL_ALL, '03')
assert set(everything[:1020]) <= set(r) <= set(everything), len(r)
PORTAL.hook = lambda: PORTAL.data.update({('plates', '03', 'notSold', '', ''): everything[:64]})     # список рассыпался
try:
    REAL_ALL('03')
    raise AssertionError('половина списка — это ошибка, а не подборка')
except RuntimeError:
    pass
PORTAL.hook = None
# именной номер без цифр портал отдаёт в том же списке (04 OMED) — пропускаем; чужая вёрстка — по-прежнему ошибка
row = '<li class="ui-datalist-item"><a href="/plate.xhtml?symbols=%s">%s<b class="lp-letters">%s</b><b class="lp-pub-price">9</b></li>'
num = '<b class="lp-numbers">777</b>'
assert srs._plate_rows(row % ('777ABC', num, 'ABC') + row % ('nullOMED', '', 'OMED')) == {'777ABC': 9}
try:
    srs._plate_rows(row % ('777ABC', '', 'ABC'))
    raise AssertionError('непонятную строку нельзя молча пропускать')
except AttributeError:
    pass
r, log = portal(REAL_FREE, '01', '777')
assert sorted(r) == plates('777', 5), (r, log)       # после всего этого маски работают как прежде
srs._pool['plates'][1:] = []
srs._pool['http'][1:] = []

day = lambda n: (dt.datetime(2026, 11, 30) - dt.timedelta(days=n)).strftime('%d.%m.%Y 10:00')
sym = lambda c, i: '070%s%s%s' % (c, chr(65 + i // 26), chr(65 + i % 26))
PORTAL.data.update({('auctions', '01', '1', '070', ''): [(sym('P', i), day(-i)) for i in range(25)],
                    ('auctions', '01', '9', '070', ''): [(sym('F', i), day(i)) for i in range(40)],
                    ('auctions', '01', '1', '123', ''): []})
r, log = portal(REAL_AUCTIONS, '01', '070')
assert [a['sym'] for a in r] == [sym('P', i) for i in range(25)] and log == 'GET set page page', (r, log)
r, log = portal(REAL_AUCTIONS, '01', '070', '', srs.AUC_FAILED, dt.datetime(2026, 11, 18))
assert [a['sym'] for a in r] == [sym('F', i) for i in range(13)] and log == 'set page page', (r, log)   # с начала!
r, log = portal(REAL_AUCTIONS, '01', '123')
assert r == [] and log == 'set page', (r, log)
assert len(srs._pool['auctions']) == 1, srs._pool
assert len(srs._pool['http']) == 1, srs._pool        # одно соединение на всё

# соединение с порталом держим открытым, только пока человек пользуется ботом
srs._warm['until'] = 0.0
conn = srs._pool['http'][-1]
conn.used -= 100
assert not srs.warm_due()                             # никто не писал — портал не трогаем
bot.note(CID, {'first_name': 'Тест'}, '777')
assert srs.warm_due() and not srs.warm_due()          # написал, соединение простаивает — пора; второй раз не зовём
PORTAL.log.clear()
srs.warm()
assert PORTAL.log == ['HEAD'] and not srs._warm['busy'] and not srs.warm_due(), (PORTAL.log, srs._warm)
conn.used -= 100
srs._warm['until'] = 0.0
assert not srs.warm_due()                             # десять минут тишины — снова не трогаем

# Telegram: длинный опрос берёт самое давнее соединение, отправка — самое свежее; закрытое — повтор на новом
class FakeConn:
    def __init__(self, name, dead=False):
        self.name, self.sock, self.dead, self.calls = name, None, dead, []

    def request(self, m, path, body=None, headers=None):
        if self.dead:
            raise ConnectionResetError('закрыто')
        self.calls.append(path.rsplit('/', 1)[-1])

    def getresponse(self):
        return type('R', (), {'status': 200, 'read': staticmethod(lambda: b'{"ok": true}')})

    def close(self):
        self.calls.append('close')


A, B = FakeConn('A'), FakeConn('B')
bot._tg_idle[:] = [A, B]
assert bot._tg_post('getUpdates', b'{}', 'application/json', 5) == (200, b'{"ok": true}')
assert A.calls == ['getUpdates'] and [c.name for c in bot._tg_idle] == ['B', 'A'], (A.calls, bot._tg_idle)
bot._tg_post('sendMessage', b'{}', 'application/json', 5)
assert A.calls == ['getUpdates', 'sendMessage'] and B.calls == [], (A.calls, B.calls)
real_conn = bot.http.client.HTTPSConnection
bot.http.client.HTTPSConnection = lambda host, timeout=None: FakeConn('new')
bot._tg_idle[:] = [FakeConn('old', dead=True)]
assert bot._tg_post('sendMessage', b'{}', 'application/json', 5)[0] == 200
assert [c.name for c in bot._tg_idle] == ['new'] and bot._tg_idle[0].calls == ['sendMessage'], bot._tg_idle
bot.http.client.HTTPSConnection = real_conn
bot._tg_idle[:] = []

print('OK: все сценарии прошли')
