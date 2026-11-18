#!/usr/bin/env python3
"""Телеграм-бот «Номера»: карточка госномера с nomer.srs.kg и сообщения, когда номер выходит без аукциона.

Запуск:  python3 bot.py            — бот (long polling) и слежение
         python3 bot.py card CHAT 888KYH 111HAH   — разово прислать карточки в чат
Токен — BOT_TOKEN в окружении или в файле .env рядом. Состояние — data/state.json.
"""
import datetime as dt
import html
import json
import os
import re
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import uuid
import zoneinfo
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import base64    # noqa: E402
import card      # noqa: E402
import render    # noqa: E402
import series    # noqa: E402
import srs       # noqa: E402

TZ = zoneinfo.ZoneInfo('Asia/Bishkek')
DATA = os.environ.get('DATA_DIR') or os.path.join(ROOT, 'data')
STATE = os.path.join(DATA, 'state.json')
DEFAULT_REGION = '01'      # пока чат не выбрал свой в /region; команды-ссылки без префикса — всегда этот регион
MAX_WATCH, MAX_MASKS = 30, 10
ADMIN = int(os.environ.get('ADMIN_CHAT') or 376894754)      # чат владельца: только ему отвечаем на /stats
KEEP = 20           # сколько последних запросов каждого чата помним для /stats
IDLE = 600          # обычная проверка номера, сек
MASK_IDLE = 900     # обычная проверка маски, сек


def now():
    return dt.datetime.now(TZ).replace(tzinfo=None)


def log(*a):
    print(now().strftime('%d.%m %H:%M:%S'), *a, flush=True)


def load_env():
    path = os.path.join(ROOT, '.env')
    if os.path.exists(path):
        for line in open(path, encoding='utf-8'):
            if '=' in line and not line.lstrip().startswith('#'):
                k, v = line.strip().split('=', 1)
                os.environ.setdefault(k, v)


load_env()
TOKEN = os.environ.get('BOT_TOKEN', '')
API = 'https://api.telegram.org/bot%s/' % TOKEN


# ---------- Telegram ----------

def _multipart(fields, files):
    b = uuid.uuid4().hex
    out = []
    for k, v in fields.items():
        out.append(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (b, k, v)).encode('utf-8'))
    for k, (name, data, ctype) in files.items():
        out.append(('--%s\r\nContent-Disposition: form-data; name="%s"; filename="%s"\r\nContent-Type: %s\r\n\r\n'
                    % (b, k, name, ctype)).encode('utf-8') + data + b'\r\n')
    out.append(('--%s--\r\n' % b).encode())
    return b''.join(out), 'multipart/form-data; boundary=' + b


def tg(method, payload=None, files=None, timeout=60):
    payload = payload or {}
    for attempt in range(3):
        try:
            if files:
                fields = {k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
                          for k, v in payload.items()}
                body, ctype = _multipart(fields, files)
            else:
                body, ctype = json.dumps(payload, ensure_ascii=False).encode('utf-8'), 'application/json'
            req = urllib.request.Request(API + method, data=body, headers={'Content-Type': ctype})
            return json.load(urllib.request.urlopen(req, timeout=timeout))
        except urllib.error.HTTPError as e:
            try:
                r = json.load(e)
            except Exception:
                r = {'ok': False, 'description': 'HTTP %s' % e.code}
            wait = (r.get('parameters') or {}).get('retry_after')
            if wait and attempt < 2:
                time.sleep(min(wait, 30) + 0.5)
                continue
            return r
        except Exception as e:      # сеть
            if attempt == 2:
                return {'ok': False, 'description': 'сеть: %s' % e}
            time.sleep(1 + attempt)
    return {'ok': False, 'description': 'не отправлено'}


def say(chat, text, kb=None, silent=False):
    p = {'chat_id': chat, 'text': text, 'parse_mode': 'HTML', 'disable_web_page_preview': True}
    if kb:
        p['reply_markup'] = {'inline_keyboard': kb}
    if silent:
        p['disable_notification'] = True
    r = tg('sendMessage', p)
    if not r.get('ok'):
        log('sendMessage', chat, r.get('description'))
    return r


# ---------- состояние ----------

class Store:
    def __init__(self):
        self.lock = threading.RLock()
        self.dirty = False
        self.d = {'chats': {}, 'watch': {}, 'masks': {}, 'offset': 0}
        seed = os.environ.get('STATE_SEED')
        if seed and not os.path.exists(STATE):      # переезд: первый запуск на пустом томе
            try:
                os.makedirs(DATA, exist_ok=True)
                with open(STATE, 'wb') as f:
                    f.write(base64.b64decode(seed))
                log('state.json создан из STATE_SEED')
            except Exception as e:
                log('STATE_SEED не применён:', e)
        if os.path.exists(STATE):
            try:
                self.d.update(json.load(open(STATE, encoding='utf-8')))
            except Exception as e:
                log('state.json не прочитан:', e)

    def save(self, force=False):
        with self.lock:
            if not (self.dirty or force):
                return
            os.makedirs(DATA, exist_ok=True)
            tmp = STATE + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(self.d, f, ensure_ascii=False)
            os.replace(tmp, STATE)
            self.dirty = False

    def chat(self, cid):
        with self.lock:
            return self.d['chats'].setdefault(str(cid), {'mode': 'widget'})

    def touch(self):
        self.dirty = True


S = Store()


def wkey(region, sym):
    return '%s-%s' % (region, sym)


# ---------- учёт входящих ----------

def who(u):
    name = ' '.join(x for x in (u.get('first_name'), u.get('last_name')) if x)
    return (name + (' @' + u['username'] if u.get('username') else '')).strip() or '?'


def note(cid, user, text=None, button=None):
    """Кто пишет боту, сколько раз и что спрашивал: счёт и последние запросы — в состоянии, вся история — в логе."""
    with S.lock:
        S.d.setdefault('since', now().strftime('%d.%m.%Y %H:%M'))
        ch = S.chat(cid)
        ch['name'], ch['last'] = who(user), now().strftime('%d.%m.%Y %H:%M')
        if text is None:
            ch['cb'] = ch.get('cb', 0) + 1
        else:
            ch['n'] = ch.get('n', 0) + 1
            ch['q'] = (ch.get('q') or [])[1 - KEEP:] + [[ch['last'][:5] + ch['last'][10:], text[:60]]]
        S.touch()
        name = ch['name']
    log('IN', cid, name, '|', ' '.join(text.split())[:200] if text is not None else 'кнопка %s' % button)


def cmd_stats(cid):
    with S.lock:
        chats = sorted(S.d['chats'].items(), key=lambda kv: -kv[1].get('n', 0))
        since = S.d.get('since')
    e = html.escape
    lines = ['<b>Кто писал боту</b> — чатов %d, счёт с %s' % (len(chats), since or '—')]
    for k, ch in chats[:25]:
        lines.append('\n• <b>%s</b> · <code>%s</code> · регион %s' % (e(ch.get('name') or 'имя ещё не видел'), k,
                                                                 ch.get('region') or DEFAULT_REGION))
        if not ch.get('n') and not ch.get('cb'):
            lines.append('  с начала счёта не писал')
            continue
        lines.append('  сообщений %d, нажатий кнопок %d, последний раз %s' % (ch.get('n', 0), ch.get('cb', 0), ch.get('last')))
        if ch.get('q'):
            lines.append('  ' + ' · '.join('%s <code>%s</code>' % (t, e(q)) for t, q in ch['q'][-10:]))
    if len(chats) > 25:
        lines.append('\n… и ещё %d' % (len(chats) - 25))
    say(cid, '\n'.join(lines)[:4000])


def watching(cid, region, sym):
    e = S.d['watch'].get(wkey(region, sym))
    return bool(e and cid in e['chats'])


# ---------- разбор ввода ----------

CYR = str.maketrans('АВЕКМНОРСТУХ', 'ABEKMHOPCTYX')
PLATE = re.compile(r'(?<![0-9A-Z])(?:(0?[1-9])[\s\-]*(?:KG)?[\s\-]*)?(\d{3})[\s\-]*([A-Z]{3})(?![0-9A-Z])')


def norm(text):
    return (text or '').upper().translate(CYR)


def find_plates(text, region=DEFAULT_REGION):
    out = []
    for m in PLATE.finditer(norm(text)):
        item = ((m.group(1) or region).zfill(2), m.group(2) + m.group(3))
        if item not in out:
            out.append(item)
    return out[:5]


def parse_mask(text, region=DEFAULT_REGION):
    """«777», «01 777», «HAH», «02 *** HAH», «777 HAH» -> (регион, цифры, буквы) или None."""
    toks = [t for t in re.split(r'[\s,;]+', norm(text)) if t and set(t) != {'*'}]
    digits, series = '', ''
    if len(toks) > 1 and re.fullmatch(r'0?[1-9]', toks[0]):
        region = toks.pop(0).zfill(2)
    for t in toks:
        t = t.replace('KG', '') if t != 'KG' else ''
        m = re.fullmatch(r'(\d{3})?([A-Z]{3})?', t)
        if not t:
            continue
        if not m or not (m.group(1) or m.group(2)):
            return None
        digits = m.group(1) or digits
        series = m.group(2) or series
    return (region, digits, series) if digits or series else None


def mask_title(region, digits, series):
    return '%s · %s %s' % (region, digits or '***', series or '***')


# ---------- карточка ----------

def keyboard(c, cid):
    region, sym = c['region'], c['sym']
    rows = []
    if c['state'] not in ('sold', 'unknown'):
        w = watching(cid, region, sym)
        rows.append([{'text': '🔕 Не следить' if w else '🔔 Следить', 'callback_data': '%s|%s|%s' % ('u' if w else 'w', region, sym)},
                     {'text': '🔄 Обновить', 'callback_data': 'r|%s|%s' % (region, sym)}])
    row = [{'text': '🌐 Страница номера', 'url': c['url']}]
    if c['auction_id']:
        row.append({'text': '🔨 Торги', 'url': '%s/auction.xhtml?id=%d' % (srs.BASE, c['auction_id'])})
    rows.append(row)
    return rows


WIDGET_OFF = [0.0]      # до какого времени не пробовать виджеты: Telegram пока пускает к ним не всех ботов


def send_card(cid, region, sym, extra=None, p=None):
    """Прислать карточку номера. -> (страница номера, карточка) или (None, None)."""
    try:
        p = p or srs.fetch_plate(region, sym)
    except Exception as e:
        say(cid, 'Портал nomer.srs.kg не ответил, попробуй ещё раз через минуту.')
        log('fetch', region, sym, e)
        return None, None
    if not p['ok']:
        say(cid, '<b>%s KG %s %s</b>\n%s' % (region, sym[:3], sym[3:], html.escape(p['msg'])))
        return None, None
    t = now()
    c = card.build(p, t)
    cap = card.caption(c, extra)
    kb = {'inline_keyboard': keyboard(c, cid)}
    if S.chat(cid).get('mode') != 'photo' and time.time() > WIDGET_OFF[0]:
        doc, z = render.widget(c, t)
        if z <= 4096:
            r = tg('sendWidget', {'chat_id': cid, 'html': doc, 'caption': cap, 'parse_mode': 'HTML', 'reply_markup': kb})
            if r.get('ok'):
                return p, c
            log('sendWidget', cid, r.get('description'))
            if 'WIDGET_NOT_ALLOWED' in (r.get('description') or ''):
                WIDGET_OFF[0] = time.time() + 6 * 3600
    img = render.png(c, t)
    if img:
        r = tg('sendPhoto', {'chat_id': cid, 'caption': cap, 'parse_mode': 'HTML', 'reply_markup': kb},
               files={'photo': ('%s.png' % sym, img, 'image/png')})
        if r.get('ok'):
            return p, c
        log('sendPhoto', cid, r.get('description'))
    say(cid, cap, kb['inline_keyboard'])
    return p, c


# ---------- слежение за номером ----------

def refresh_entry(e, p, c):
    e['snap'] = card.snapshot(p, c)
    e['hot'] = [x.isoformat() for x in c['hot']]
    e['title'] = c['title']
    e['head'] = c['head']
    S.touch()


def delay_for(e, t):
    """Через сколько секунд проверить номер снова: чем ближе важный момент, тем чаще."""
    d = IDLE
    for iso in e.get('hot', []):
        gap = (t - dt.datetime.fromisoformat(iso)).total_seconds()      # <0 — до момента
        if -45 <= gap <= 180:
            d = min(d, 2)
        elif 180 < gap <= 900:
            d = min(d, 10)
        elif 900 < gap <= 6 * 3600:
            d = min(d, 120)
        elif -600 <= gap < -45:
            d = min(d, 30)
        elif gap < -600:
            d = min(d, max(30, -gap - 600))     # не проспать начало частых проверок
    return d


def add_watch(cid, region, sym, p, c):
    with S.lock:
        mine = sum(1 for e in S.d['watch'].values() if cid in e['chats'])
        if mine >= MAX_WATCH:
            return False
        e = S.d['watch'].setdefault(wkey(region, sym), {'region': region, 'sym': sym, 'chats': [], 'rem': {}, 'next': 0})
        if cid not in e['chats']:
            e['chats'].append(cid)
        refresh_entry(e, p, c)
        e['next'] = time.time() + delay_for(e, now())
    return True


def drop_watch(cid, region, sym):
    with S.lock:
        k = wkey(region, sym)
        e = S.d['watch'].get(k)
        if e and cid in e['chats']:
            e['chats'].remove(cid)
            if not e['chats']:
                del S.d['watch'][k]
            S.touch()
            return True
    return False


def watch_note(c):
    t = now()
    if c['state'] in ('in_auction', 'waiting'):
        when = ('по прогнозу %s в 00:00' % card.dm(c['rel'], t)) if c['rel'] else 'дату пока не посчитать'
        return ('🔔 Слежу за <b>%s</b>. Напишу, как только выйдет без аукциона (%s): за 3 часа, за 10 минут '
                'и в ту же минуту. Ещё сообщу о новых торгах и если на них запишутся участники.' % (c['title'], when))
    if c['state'] == 'booked':
        return ('🔔 Слежу за <b>%s</b>. К сроку оплаты брони проверяю страницу каждые 2 секунды — '
                'освободится, сразу напишу.' % c['title'])
    if c['state'] == 'idle':
        return ('🔔 Слежу за <b>%s</b>. Напишу, когда по нему назначат торги, а потом — когда он выйдет '
                'без аукциона.' % c['title'])
    return '🔔 Слежу за <b>%s</b>. Напишу, когда его забронируют, купят или изменится цена.' % c['title']


def link_kb(e):
    return [[{'text': '🌐 Страница номера', 'url': srs.plate_url(e['region'], e['sym'])},
             {'text': '📋 Карточка', 'callback_data': 'c|%s|%s' % (e['region'], e['sym'])}]]


def remind(e, t):
    """Напоминания за 3 часа и за 10 минут до важного момента. -> [(чат, текст, кнопки)], слать вне замка."""
    out = []
    for iso in e.get('hot', []):
        T = dt.datetime.fromisoformat(iso)
        left = (T - t).total_seconds()
        sent = e.setdefault('rem', {}).setdefault(iso, [])
        kind = None
        if 3 * 3600 - 1200 <= left <= 3 * 3600 and '3h' not in sent:
            kind = '3h'
        elif 30 <= left <= 600 and '10m' not in sent:
            kind = '10m'
        if not kind:
            continue
        sent.append(kind)
        S.touch()
        mins = round(left / 60)
        left_txt = 'через %d мин' % mins if mins < 90 else 'через %d ч' % round(mins / 60)
        st = (e.get('snap') or {}).get('status')
        if st == 'booked':
            text = ('⏰ <b>%s</b>: срок оплаты брони истекает в %s (%s). Не оплатят — номер освободится, напишу сразу.'
                    % (e['title'], T.strftime('%H:%M'), left_txt))
        else:
            text = ('⏰ <b>%s</b> по прогнозу выходит без аукциона в %s (%s). Войди в кабинет портала заранее — '
                    'напишу, как только номер откроется.' % (e['title'], T.strftime('%d.%m %H:%M'), left_txt))
        out += [(cid, text, link_kb(e)) for cid in e['chats']]
    return out


def check_watch(key):
    """Одна проверка номера: страница, сравнение со снимком, сообщения."""
    with S.lock:
        e = S.d['watch'].get(key)
        if not e:
            return
        region, sym, old = e['region'], e['sym'], e.get('snap')
    t = now()
    try:
        p = srs.fetch_plate(region, sym, timeout=12, tries=2)
    except Exception as ex:
        with S.lock:
            e = S.d['watch'].get(key)
            if e:
                e['busy'] = False
                e['next'] = time.time() + min(60, max(3, delay_for(e, t)))
        log('watch fetch', key, ex)
        return
    with S.lock:
        e = S.d['watch'].get(key)
        if not e:
            return
        e['busy'] = False
        if not p['ok']:
            e['next'] = time.time() + IDLE
            return
        c = card.build(p, t)
        new = card.snapshot(p, c)
        msgs = card.changes(old, new, p, c, t)
        chats = list(e['chats'])
        refresh_entry(e, p, c)
        e['next'] = time.time() + delay_for(e, t)
        sold = new['status'] == 'sold'
        if sold:
            del S.d['watch'][key]
            S.touch()
    for loud, text in msgs:
        log('CHANGE', key, re.sub(r'<[^>]+>', '', text))
        for cid in chats:
            say(cid, text, link_kb({'region': region, 'sym': sym}), silent=not loud)


# ---------- маски: свободные номера без аукциона ----------

def mkey(region, digits, series):
    return '%s|%s|%s' % (region, digits, series)


def mask_delay(t):
    secs = t.hour * 3600 + t.minute * 60 + t.second
    if secs < 360:                       # 00:00–00:06 — номера выходят в полночь
        return 40
    return min(MASK_IDLE, 86400 - secs + 8)


def check_mask(key):
    with S.lock:
        e = S.d['masks'].get(key)
        if not e:
            return
        region, digits, series = key.split('|')
    try:
        cur = srs.fetch_free(region, digits, series)
    except Exception as ex:
        with S.lock:
            e = S.d['masks'].get(key)
            if e:
                e['busy'] = False
                e['next'] = time.time() + 120
        log('mask fetch', key, ex)
        return
    with S.lock:
        e = S.d['masks'].get(key)
        if not e:
            return
        e['busy'] = False
        seen = e.get('seen')
        fresh = sorted(k for k in cur if k not in seen) if seen is not None else []
        e['seen'] = cur
        e['next'] = time.time() + mask_delay(now())
        chats = list(e['chats'])
        S.touch()
    if not fresh:
        return
    log('MASK', key, fresh)
    lines = ['• %s — %s сом' % (card.pcmd(region, s), card.money(cur[s])) for s in fresh[:15]]
    if len(fresh) > 15:
        lines.append('… и ещё %d' % (len(fresh) - 15))
    text = '🟢 <b>Свободны без аукциона</b> · маска %s\n%s' % (mask_title(region, digits, series), '\n'.join(lines))
    kb = [[{'text': '%s %s' % (s[:3], s[3:]), 'callback_data': 'c|%s|%s' % (region, s)} for s in fresh[i:i + 3]]
          for i in range(0, min(len(fresh), 6), 3)]
    for cid in chats:
        say(cid, text, kb)


PRICES_EVERY = 7 * 86400      # прайс-лист (какие цифры аукционные) перечитываем раз в неделю


def refresh_prices(_key=None):
    try:
        digs = series.fetch_auction_digits()
    except Exception as e:
        digs = None
        log('прайс-лист не прочитан:', e)
    with S.lock:
        old = (S.d.get('prices') or {}).get('auction')
        # не вышло — повторим через сутки, а пока живём на прежнем списке
        S.d['prices'] = {'at': time.time() if digs else time.time() - PRICES_EVERY + 86400,
                         'auction': digs or old or series.AUCTION_DIGITS}
        S.touch()
    if digs:
        log('прайс-лист: аукционных цифр %d%s' % (len(digs), '' if digs == (old or digs) else ' — список изменился'))


def watcher():
    pool = ThreadPoolExecutor(4)

    def run(fn, key):
        try:
            fn(key)
        except Exception:
            log('watcher', key, traceback.format_exc())
            with S.lock:
                e = S.d['watch'].get(key) or S.d['masks'].get(key)
                if e:
                    e['busy'] = False
                    e['next'] = time.time() + 60

    for e in list(S.d['watch'].values()) + list(S.d['masks'].values()):
        e['busy'] = False
    saved = time.time()
    prices_busy = [False]
    while True:
        try:
            t, ts = now(), time.time()
            notes = []
            with S.lock:
                for key, e in list(S.d['watch'].items()):
                    notes += remind(e, t)
                    if not e.get('busy') and ts >= e.get('next', 0):
                        e['busy'] = True
                        pool.submit(run, check_watch, key)
                for key, e in list(S.d['masks'].items()):
                    if not e.get('busy') and ts >= e.get('next', 0):
                        e['busy'] = True
                        pool.submit(run, check_mask, key)
            for cid, text, kb in notes:
                say(cid, text, kb)
            if ts - (S.d.get('prices') or {}).get('at', 0) > PRICES_EVERY and not prices_busy[0]:
                prices_busy[0] = True
                pool.submit(lambda: (refresh_prices(), prices_busy.__setitem__(0, False)))
            if ts - saved > 5:
                S.save()
                saved = ts
        except Exception:
            log('watcher loop', traceback.format_exc())
        time.sleep(1)


# ---------- команды ----------

HELP = (
    '<b>Номера</b> — госномера с портала nomer.srs.kg.\n\n'
    'Пришли номер — <code>888 KYH</code>, <code>01 777 LVS</code>, можно несколько через пробел. '
    'Покажу номер, его статус и, если он на аукционе, график: прошедшие торги, будущие попытки и дату, '
    'когда он выйдет без аукциона.\n\n'
    '🌍 Регион по умолчанию сейчас — <b>%s</b>, сменить — /region. В нём ищу, когда регион не указан; '
    'разово другой — <code>02 888 KYH</code>, <code>02 EEF</code>, <code>02 777</code>.\n\n'
    '🔔 <b>Следить</b> под карточкой — напишу, когда номер выйдет без аукциона (за 3 часа, за 10 минут и '
    'в ту же минуту), когда освободится бронь, когда его купят.\n\n'
    'Пришли три буквы — <code>EEF</code> — покажу всю серию: свободные без аукциона, номера на торгах и между '
    'торгами, сгруппированно по цене и датам. Пришли три цифры — <code>777</code> — покажу все номера с ними: '
    'свободные, забронированные и те, что на торгах; длинный список листается кнопками. '
    'Номера в списках нажимаются: /777EEF — карточка, /EEF — серия, /777 — цифры. Такие ссылки без региона — '
    'всегда 01 (Бишкек), другой регион пишется в самой ссылке: /02_777EEF, /02_777.\n\n'
    '/sub 777 — сообщать о любых свободных номерах без аукциона с цифрами 777. '
    'Ещё так: <code>/sub HAH</code>, <code>/sub 02 001</code>, <code>/sub 777 HAH</code>\n'
    '/list — за чем слежу, там же отписка\n'
    '/region — регион по умолчанию\n'
    '/mode — карточка виджетом или картинкой'
)


def region_of(cid):
    return S.chat(cid).get('region') or DEFAULT_REGION


def region_title(region):
    return '%s %s' % (region, srs.REGIONS[region])


def cmd_region(cid, arg='', intro=None):
    m = re.fullmatch(r'0?([1-9])', arg.strip())
    if m:
        return set_region(cid, '0' + m.group(1))
    cur = region_of(cid)
    marks = [{'text': ('✅ ' if r == cur else '') + region_title(r), 'callback_data': 'g|' + r} for r in sorted(srs.REGIONS)]
    say(cid, intro or 'Регион по умолчанию — <b>%s</b>. В нём ищу, когда пишешь номер, серию или цифры без региона. '
                      'Выбери другой:' % region_title(cur), [marks[i:i + 2] for i in range(0, len(marks), 2)])


def set_region(cid, region, mid=None):
    with S.lock:
        S.chat(cid)['region'] = region
        S.touch()
    show(cid, mid, '✅ Регион по умолчанию — <b>%s</b>\nТеперь <code>888 KYH</code>, <code>EEF</code>, <code>777</code> и '
                   '/sub без региона — это %s. Разово другой регион: <code>0%d 888 KYH</code>. Сменить: /region'
         % (region_title(region), region, 2 if region == '01' else 1))


def cmd_list(cid):
    with S.lock:
        ws = [(k, e) for k, e in S.d['watch'].items() if cid in e['chats']]
        ms = [k for k, e in S.d['masks'].items() if cid in e['chats']]
    if not ws and not ms:
        return say(cid, 'Пока ни за чем не слежу. Пришли номер и нажми «🔔 Следить» или подпишись на маску: /sub 777')
    t = now()
    lines, kb = [], []
    if ws:
        lines.append('<b>Номера</b>')
        for k, e in sorted(ws):
            hot = ''
            fut = [dt.datetime.fromisoformat(x) for x in e.get('hot', []) if dt.datetime.fromisoformat(x) > t]
            if fut:
                hot = ' · ловлю %s' % card.dm(min(fut), t, True)
            lines.append('• %s — %s%s' % (card.pcmd(e['region'], e['sym']), html.escape(e.get('head', '')), hot))
            kb.append([{'text': '📋 %s' % e.get('title', k)[6:], 'callback_data': 'c|%s|%s' % (e['region'], e['sym'])},
                       {'text': '✖ убрать', 'callback_data': 'u|%s|%s' % (e['region'], e['sym'])}])
    if ms:
        lines.append('\n<b>Маски</b> — свободные без аукциона')
        for k in sorted(ms):
            r_, d_, s_ = k.split('|')
            full = (' — вся серия %s' % card.scmd(r_, s_) if s_ and not d_ else
                    ' — все номера %s' % card.scmd(r_, d_) if d_ and not s_ else '')
            lines.append('• %s%s' % (mask_title(r_, d_, s_), full))
            kb.append([{'text': '✖ маска %s' % mask_title(*k.split('|')), 'callback_data': 'x|' + k}])
    say(cid, '\n'.join(lines).strip(), kb)


def cmd_sub(cid, arg):
    m = parse_mask(arg, region_of(cid))
    if not m:
        return say(cid, 'Маска — три цифры и/или три буквы, перед ними можно регион:\n'
                        '<code>/sub 777</code> · <code>/sub HAH</code> · <code>/sub 02 001</code> · <code>/sub 777 HAH</code>')
    key = mkey(*m)
    with S.lock:
        if sum(1 for e in S.d['masks'].values() if cid in e['chats']) >= MAX_MASKS and key not in S.d['masks']:
            return say(cid, 'Масок уже %d — убери лишние в /list.' % MAX_MASKS)
    try:
        cur = srs.fetch_free(*m)
    except Exception as ex:
        return say(cid, 'Не получилось открыть список: %s' % html.escape(str(ex)))
    with S.lock:
        e = S.d['masks'].setdefault(key, {'chats': [], 'seen': None, 'next': 0})
        if cid not in e['chats']:
            e['chats'].append(cid)
        e['seen'] = cur
        e['next'] = time.time() + mask_delay(now())
        S.touch()
    lines = ['🔔 Маска <b>%s</b>: напишу, когда появится свободный номер без аукциона. '
             'Проверяю каждые 15 минут, а сразу после полуночи — каждые 40 секунд.' % mask_title(*m)]
    if cur:
        lines.append('\nСейчас свободны (%d):' % len(cur))
        lines += ['• %s — %s сом' % (card.pcmd(m[0], s), card.money(v)) for s, v in sorted(cur.items())[:12]]
        if len(cur) > 12:
            lines.append('… и ещё %d' % (len(cur) - 12))
    else:
        lines.append('Сейчас свободных нет.')
    say(cid, '\n'.join(lines))


LISTS = {}          # (регион, три буквы или три цифры) -> (когда собран, страницы текста)
LIST_TTL = 600
SERIES_IN = re.compile(r'(?:(0?[1-9])[\s\-_]*)?(?:KG[\s\-]*)?([A-Z]{3})')
DIGITS_IN = re.compile(r'(?:(0?[1-9])(?:[\s\-_]*KG[\s\-_]*|[\s\-_]+))?(\d{3})')     # «01777» не берём: двусмысленно
LIST_KEY = r'[A-Z]{3}|\d{3}'


def auction_digits():
    return (S.d.get('prices') or {}).get('auction') or series.AUCTION_DIGITS


def paginate(text, chars=3800, marks=90):
    """Разрезать список на страницы: в сообщении Telegram помещается 4096 знаков и нажимаются первые 100 выделений."""
    count = lambda s: len(re.findall(r'(?<![<\w])/\w|<b>', s))     # команды-номера и жирный
    lines = text.split('\n')
    pages, cur, head = [], [], None
    for line in lines:
        if cur and (sum(len(x) + 1 for x in cur) + len(line) > chars or sum(map(count, cur)) + count(line) > marks):
            carry = []
            while len(cur) > 1 and not cur[-1].startswith('  '):   # заголовки без строк под ними уносим на новую страницу
                carry.insert(0, cur.pop())
            pages.append(cur)
            cur = [lines[0]] + carry
            if line.startswith('  ') and head and head not in carry:    # страница рвёт группу — повторяем её заголовок
                cur.append('… ' + head.strip())
        if line.strip() and not line.startswith('  '):
            head = line
        cur.append(line)
    pages.append(cur)
    return ['\n'.join(p).strip() for p in pages]


def list_kb(region, key, n, total):
    rows = []
    if total > 1:
        turn = lambda to, text: {'text': text, 'callback_data': 'p|%s|%s|%d' % (region, key, to)}
        rows.append(([turn(n - 1, '◂ стр. %d' % n)] if n else [])
                    + ([turn(n + 1, 'стр. %d из %d ▸' % (n + 2, total))] if n + 1 < total else []))
    rows.append([{'text': '🔔 Сообщать о свободных %s' % key, 'callback_data': 'm|%s|%s' % (region, key)},
                 {'text': '🔄 Обновить', 'callback_data': 's|%s|%s' % (region, key)}])
    return rows


def show(cid, mid, text, kb=None):
    """Показать текст в сообщении mid; нет его или правка не прошла — новым сообщением."""
    if mid:
        p = {'chat_id': cid, 'message_id': mid, 'text': text, 'parse_mode': 'HTML', 'disable_web_page_preview': True}
        if kb:
            p['reply_markup'] = {'inline_keyboard': kb}
        r = tg('editMessageText', p)
        if r.get('ok') or 'not modified' in (r.get('description') or ''):
            return
    say(cid, text, kb)


def send_list(cid, region, key, fresh=False, page=0, mid=None):
    """Список номеров: key — три буквы (серия) или три цифры (все номера с ними). mid — сообщение, которое листаем."""
    hit = LISTS.get((region, key))
    if not hit or (mid is None and time.time() - hit[0] > (60 if fresh else LIST_TTL)):
        wait = ('🔎 Серия <b>%s</b>: читаю около 40 страниц портала, это 10–15 секунд…' % key if key.isalpha() else
                '🔎 Цифры <b>%s</b>: читаю списки портала. Обычно это несколько секунд, а если на торгах сотни '
                'номеров — до минуты…' % key)
        if mid:
            show(cid, mid, wait)
        else:
            mid = (say(cid, wait).get('result') or {}).get('message_id')
        try:
            t, t0 = now(), time.time()
            data = (series.collect(region, key, t, auction_digits()) if key.isalpha()
                    else series.collect_digits(region, key, t))
            hit = (time.time(), paginate(series.fmt(region, key, data, t)))
            log('LIST', region, key, '%.1f с, страниц %d' % (time.time() - t0, len(hit[1])))
        except Exception as e:
            log('list', region, key, e)
            return show(cid, mid, 'Портал nomer.srs.kg не ответил, попробуй ещё раз через минуту.')
        if len(LISTS) >= 300:
            LISTS.pop(min(LISTS, key=lambda k: LISTS[k][0]))
        LISTS[(region, key)] = hit
    pages = hit[1]
    page = max(0, min(page, len(pages) - 1))
    show(cid, mid, pages[page], list_kb(region, key, page, len(pages)))


def show_plates(cid, plates):
    for region, sym in plates:
        p, c = send_card(cid, region, sym)
        if p and watching(cid, region, sym):
            with S.lock:
                e = S.d['watch'].get(wkey(region, sym))
                if e:
                    refresh_entry(e, p, c)


def on_message(m):
    cid = m['chat']['id']
    text = (m.get('text') or '').strip()
    if not text:
        return
    note(cid, m.get('from') or m['chat'], text=text)
    region = region_of(cid)
    cmd, _, arg = text.partition(' ')
    cmd = cmd.split('@')[0].lower()
    if cmd == '/stats' and cid == ADMIN:
        return cmd_stats(cid)
    if cmd.startswith('/') and cmd not in ('/start', '/help', '/list', '/sub', '/mode', '/region'):
        # быстрый доступ: /777EEF и /02_777EEF — карточка, /EEF и /02_EEF — вся серия, /777 и /02_777 — все с цифрами
        q = cmd[1:].upper()
        mm = re.fullmatch(r'(?:(0[1-9])_)?(\d{3}[A-Z]{3})', q)
        if mm:
            return show_plates(cid, [(mm.group(1) or DEFAULT_REGION, mm.group(2))])
        mm = re.fullmatch(r'(?:(0[1-9])_)?(%s)' % LIST_KEY, q)
        if mm:
            return send_list(cid, mm.group(1) or DEFAULT_REGION, mm.group(2))
        return say(cid, HELP % region_title(region))
    if cmd in ('/start', '/help'):
        say(cid, HELP % region_title(region))
        if cmd == '/start' and 'region' not in S.chat(cid):     # первый запуск — один раз спрашиваем регион
            cmd_region(cid, intro='Для начала выбери регион — в нём буду искать, когда регион не указан. '
                                  'Сменить потом: /region')
        return
    if cmd == '/region':
        return cmd_region(cid, arg)
    if cmd == '/list':
        return cmd_list(cid)
    if cmd == '/sub':
        return cmd_sub(cid, arg)
    if cmd == '/mode':
        ch = S.chat(cid)
        ch['mode'] = 'photo' if ch.get('mode') != 'photo' else 'widget'
        S.touch()
        return say(cid, 'Карточку теперь шлю %s.' % ('картинкой' if ch['mode'] == 'photo' else 'виджетом (где он не поддержан — картинкой или текстом)'))
    plates = find_plates(text, region)
    if plates:
        return show_plates(cid, plates)
    mm = SERIES_IN.fullmatch(norm(text).strip()) or DIGITS_IN.fullmatch(norm(text).strip())
    if mm:
        return send_list(cid, (mm.group(1) or region).zfill(2), mm.group(2))
    say(cid, 'Не вижу номера. Пришли номер — <code>888 KYH</code>, три буквы серии — <code>EEF</code> — '
             'или три цифры — <code>777</code>. /help')


def on_callback(q):
    cid = q['message']['chat']['id']
    mid = q['message']['message_id']
    act, _, rest = (q.get('data') or '').partition('|')
    done = lambda text='': tg('answerCallbackQuery', {'callback_query_id': q['id'], 'text': text})
    note(cid, q.get('from') or {}, button=q.get('data') or '')
    if act == 'x':
        with S.lock:
            e = S.d['masks'].get(rest)
            if e and cid in e['chats']:
                e['chats'].remove(cid)
                if not e['chats']:
                    del S.d['masks'][rest]
                S.touch()
        done('Маска убрана')
        return cmd_list(cid)
    if act == 'g':      # выбор региона по умолчанию
        ok = re.fullmatch(r'0[1-9]', rest)
        done('Регион %s' % rest if ok else '')
        return set_region(cid, rest, mid) if ok else None
    if act == 'p':      # листаем список: регион|буквы или цифры|страница
        mm = re.fullmatch(r'(0[1-9])\|(%s)\|(\d{1,3})' % LIST_KEY, rest)
        done()
        return send_list(cid, mm.group(1), mm.group(2), page=int(mm.group(3)), mid=mid) if mm else None
    region, _, sym = rest.partition('|')
    if act in ('m', 's') and re.fullmatch(r'0[1-9]', region) and re.fullmatch(LIST_KEY, sym):
        if act == 'm':
            done('Подписываю')
            return cmd_sub(cid, '%s %s' % (region, sym))
        done('Обновляю…')
        return send_list(cid, region, sym, fresh=True)
    if not re.fullmatch(r'0[1-9]', region) or not re.fullmatch(r'\d{3}[A-Z]{3}', sym):
        return done()
    if act == 'u':
        drop_watch(cid, region, sym)
        done('Больше не слежу')
        p = srs.fetch_plate(region, sym)
        if p['ok']:
            tg('editMessageReplyMarkup', {'chat_id': cid, 'message_id': mid,
                                          'reply_markup': {'inline_keyboard': keyboard(card.build(p, now()), cid)}})
        return
    if act == 'w':
        p = srs.fetch_plate(region, sym)
        if not p['ok']:
            return done('Портал не показал номер')
        c = card.build(p, now())
        if c['state'] in ('sold', 'unknown'):
            return done('За этим номером следить незачем')
        if not add_watch(cid, region, sym, p, c):
            return done('Уже %d номеров — убери лишние в /list' % MAX_WATCH)
        done('Слежу')
        tg('editMessageReplyMarkup', {'chat_id': cid, 'message_id': mid, 'reply_markup': {'inline_keyboard': keyboard(c, cid)}})
        return say(cid, watch_note(c))
    if act in ('r', 'c'):
        done('Обновляю…' if act == 'r' else '')
        return show_plates(cid, [(region, sym)])
    done()


def main():
    if not TOKEN:
        sys.exit('нет BOT_TOKEN (окружение или .env)')
    if len(sys.argv) > 2 and sys.argv[1] == 'card':
        cid = int(sys.argv[2])
        for region, sym in find_plates(' '.join(sys.argv[3:])):
            p, c = send_card(cid, region, sym)
            print(region, sym, 'ok' if p else 'не отправлено')
        return
    me = tg('getMe')
    if not me.get('ok'):
        sys.exit('getMe: %s' % me.get('description'))
    log('бот @%s, номеров под слежением %d, масок %d' % (me['result']['username'], len(S.d['watch']), len(S.d['masks'])))
    try:
        t0 = time.time()
        ok = srs.fetch_plate('01', '777AAA', timeout=15, tries=2)['ok']
        log('портал отвечает: %s, %d мс' % ('да' if ok else 'страница не разобрана', (time.time() - t0) * 1000))
    except Exception as e:
        log('ПОРТАЛ НЕ ОТВЕЧАЕТ отсюда:', e)
    tg('setMyCommands', {'commands': [
        {'command': 'list', 'description': 'За чем слежу'},
        {'command': 'sub', 'description': 'Маска: /sub 777 или /sub HAH'},
        {'command': 'region', 'description': 'Регион по умолчанию'},
        {'command': 'mode', 'description': 'Виджет или картинка'},
        {'command': 'help', 'description': 'Как пользоваться'}]})
    threading.Thread(target=watcher, daemon=True).start()
    pool = ThreadPoolExecutor(4)

    def handle(u):
        try:
            if 'message' in u:
                on_message(u['message'])
            elif 'callback_query' in u:
                on_callback(u['callback_query'])
        except Exception:
            log('update', traceback.format_exc())

    while True:
        r = tg('getUpdates', {'offset': S.d.get('offset', 0), 'timeout': 50,
                              'allowed_updates': ['message', 'callback_query']}, timeout=70)
        if not r.get('ok'):
            log('getUpdates', r.get('description'))
            time.sleep(3)
            continue
        for u in r['result']:
            S.d['offset'] = u['update_id'] + 1
            S.touch()
            pool.submit(handle, u)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        S.save(force=True)
