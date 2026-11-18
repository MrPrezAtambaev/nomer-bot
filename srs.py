"""Клиент портала nomer.srs.kg: страница номера, список свободных номеров без аукциона и список торгов.

Страницу номера читаем без сессии, поэтому язык по умолчанию кыргызский — метки ниже на нём.
"""
import datetime as dt
import html
import http.client
import http.cookiejar
import re
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE = 'https://nomer.srs.kg'
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128 Safari/537.36'
DT = re.compile(r'^\d\d\.\d\d\.\d{4} \d\d:\d\d$')

REGIONS = {
    '01': 'г. Бишкек', '02': 'г. Ош', '03': 'Баткенская обл.', '04': 'Джалал-Абадская обл.',
    '05': 'Нарынская обл.', '06': 'Ошская обл.', '07': 'Таласская обл.', '08': 'Чуйская обл.',
    '09': 'Иссык-Кульская обл.',
}
STATUS = {
    'Аукциондо': 'in_auction', 'Аукционду күтүүдө': 'waiting', 'Бош': 'free',
    'Сатып алууну күтүүдө': 'booked', 'Сатылды': 'sold', 'Белгисиз': 'unknown',
}
AUC = {'Пландалган': 'planned', 'Өткөн жок': 'failed', 'Сатып алынды': 'sold'}
MSG = [
    ('тыюу салынган', 'Такое сочетание букв в реестре запрещённых — номер не выдаётся.'),
    ('3 сандан', 'Нужны 3 цифры и 3 буквы, например 777 ABC.'),
    ('Туура эмес аймак', 'Регион — от 01 до 09.'),
]

_gate = threading.Lock()
_last = [0.0]
MIN_GAP = 0.25      # не чаще 4 запросов в секунду на весь бот


def _throttle():
    with _gate:
        wait = _last[0] + MIN_GAP - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.monotonic()


def _open(opener, url, data=None, ajax=False, timeout=20, tries=3):
    err = None
    for k in range(tries):
        _throttle()
        try:
            h = {'User-Agent': UA}
            if ajax:
                h.update({'Faces-Request': 'partial/ajax', 'X-Requested-With': 'XMLHttpRequest',
                          'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'})
            body = urllib.parse.urlencode(data).encode() if data is not None else None
            r = opener.open(urllib.request.Request(url, data=body, headers=h), timeout=timeout)
            return r.read().decode('utf-8', 'replace')
        except Exception as e:      # сеть, 5xx — повторяем
            err = e
            if k + 1 < tries:
                time.sleep(1 + 2 * k)
    raise RuntimeError('nomer.srs.kg: %s' % err)


HOST = 'nomer.srs.kg'
_local = threading.local()


def _get(path, timeout=20, tries=3):
    """GET по постоянному соединению (своё на каждый поток): без нового TLS-рукопожатия на каждый запрос.

    Издалека это в разы быстрее. Сервер закрывает простаивающее соединение — тогда первый повтор сразу, на новом.
    """
    err = None
    for k in range(tries + 1):
        _throttle()
        c = getattr(_local, 'conn', None)
        reused = c is not None
        try:
            if c is None:
                c = _local.conn = http.client.HTTPSConnection(HOST, timeout=timeout)
            elif c.sock is not None:
                c.sock.settimeout(timeout)
            c.request('GET', path, headers={'User-Agent': UA, 'Accept-Encoding': 'identity'})
            r = c.getresponse()
            body = r.read()
            if r.status != 200:
                raise RuntimeError('HTTP %d' % r.status)
            return body.decode('utf-8', 'replace')
        except Exception as e:
            err = e
            try:
                c.close()
            except Exception:
                pass
            _local.conn = None
            if reused and k == 0:
                continue                 # соединение протухло — это не сбой портала
            if k + 1 >= tries:
                break
            time.sleep(1 + 2 * k)
    raise RuntimeError('nomer.srs.kg: %s' % err)


def ts(s):
    return dt.datetime.strptime(s, '%d.%m.%Y %H:%M')


def _num(s):
    d = re.sub(r'\D', '', s or '')
    return int(d) if d else None


def plate_url(region, sym):
    return '%s/plate.xhtml?region=%s&symbols=%s' % (BASE, region, sym)


def parse_plate(page, region, sym):
    m = re.search(r'summary:"([^"]+)"', page)
    i = page.find('id="content"')
    body = page[i:] if i > 0 else page
    t = re.sub(r'<script.*?</script>|<style.*?</style>', '', body, flags=re.S)
    L = [x.strip() for x in html.unescape(re.sub(r'<[^>]+>', '\n', t)).split('\n') if x.strip()]

    def after(lbl, k=1):
        return L[L.index(lbl) + k] if lbl in L and L.index(lbl) + k < len(L) else None

    if 'Тиби' not in L:
        raw = m.group(1) if m else ''
        text = next((ru for key, ru in MSG if key in raw), raw or 'Портал не показал этот номер.')
        return {'ok': False, 'msg': text, 'region': region, 'sym': sym}

    typ = after('Тиби') or ''
    st_raw = after('Макамы') or ''
    price = _num(after('Баасы'))
    nxt = after('Баасы', 2) or ''
    price2 = _num(nxt) if nxt.startswith('/') else None
    sold = _num(after('Сатылган баа'))

    auctions, bookings, book_toks = [], [], []
    mode = None
    for x in L:
        if x.startswith('©'):
            break
        if x == 'Акыркы төлөө мөөнөтү':
            mode = 'book'
            continue
        if x == 'Күнү':
            mode = 'auc'
            continue
        if mode == 'book':
            book_toks.append(x)
        elif mode == 'auc':
            if DT.match(x):
                auctions.append({'at': x, 'verif': None, 'total': None, 'st_raw': ''})
            elif auctions:
                a = auctions[-1]
                if x.startswith('/'):
                    a['total'] = _num(x)
                elif x.isdigit() and a['verif'] is None:
                    a['verif'] = int(x)
                elif x not in ('Тастыкталган', 'Бардыгы', 'Катышуучулардын саны', 'Макамы'):
                    a['st_raw'] = x
    k = 0
    while k < len(book_toks) - 1:
        if DT.match(book_toks[k]) and DT.match(book_toks[k + 1]):
            st = book_toks[k + 2] if k + 2 < len(book_toks) else ''
            bookings.append({'sum': _num(book_toks[k - 1]) if k else None, 'at': book_toks[k],
                             'due': book_toks[k + 1], 'paid': st == 'Төлөндү'})
            k += 2
        else:
            k += 1
    ids = re.findall(r'auction\.xhtml[^\'"?]*\?id=(\d+)', body)     # кнопка «Просмотр» есть у активных торгов
    for n, a in enumerate(auctions):
        a['st'] = AUC.get(a['st_raw'], 'other')
        a['id'] = int(ids[n]) if n < len(ids) else None
    return {
        'ok': True, 'region': region, 'sym': sym, 'digits': sym[:3], 'letters': sym[3:],
        'type': 'auction' if typ.startswith('Аукциондук') else 'plain',
        'status': STATUS.get(st_raw, 'other'), 'status_raw': st_raw,
        'price': price, 'price2': price2, 'sold': sold,
        'auctions': auctions, 'bookings': bookings,
    }


def fetch_plate(region, sym, timeout=20, tries=3):
    page = _get('/plate.xhtml?region=%s&symbols=%s' % (region, sym), timeout=timeout, tries=tries)
    return parse_plate(page, region, sym)


ROWS = {'plates': 64, 'auctions': 10}       # размер страницы задан порталом: другой даёт IllegalArgumentException
REGION_FIELD = {'plates': 'j_idt75', 'auctions': 'j_idt58'}
AUC_PLANNED, AUC_FAILED = 1, 9              # статусы в списке торгов: назначены, не состоялись
AUC_MAX_PAGES = 240                         # потолок одной выкачки списка торгов — минута работы при 4 запросах/с


class _List:
    """Список портала с фильтрами — plates.xhtml или auctions.xhtml: JSF-сессия и чтение по страницам."""

    def __init__(self, kind, region, status, digits='', series=''):
        self.url = '%s/%s.xhtml' % (BASE, kind)
        self.kind, self.rows = kind, ROWS[kind]
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        page = _open(self.op, self.url)
        self.vs = re.search(r'name="javax.faces.ViewState"[^>]*value="([^"]+)"', page).group(1)
        rf = REGION_FIELD[kind]
        sel = re.search(r'<select id="%s_input".*?</select>' % rf, page, re.S).group(0)
        uuids = re.findall(r'<option value="([0-9a-f-]{36})"', sel)      # «все регионы», затем 01…09
        self.form = {'j_idt48': 'j_idt48', 'j_idt53_input': status, rf + '_input': uuids[int(region)],
                     'digits': digits, 'series': series}
        if kind == 'plates':
            self.form.update({'j_idt61_input': '', 'j_idt68_input': ''})
        r = ''
        for src in ['j_idt53', rf] + (['digits'] if digits else []) + (['series'] if series else []):
            r = self.ajax({'javax.faces.source': src, 'javax.faces.partial.execute': src,
                           'javax.faces.partial.render': 'table j_idt48', 'javax.faces.behavior.event': 'valueChange',
                           'javax.faces.partial.event': 'change'})
        m = re.search(r'rowCount:(\d+)', r)
        if not m:
            raise RuntimeError('nomer.srs.kg: список не открылся')
        self.total, self.head = int(m.group(1)), r

    def ajax(self, extra):
        d = dict(self.form)
        d.update(extra)
        d['javax.faces.ViewState'] = self.vs
        d['javax.faces.partial.ajax'] = 'true'
        s = _open(self.op, self.url, d, ajax=True, timeout=40)
        m = re.search(r'ViewState[^>]*><!\[CDATA\[([^\]]+)\]\]>', s)
        if m:
            self.vs = m.group(1)
        return s

    def page(self, n):
        """Страница списка, счёт с нуля. Нулевая приходит вместе с фильтрами."""
        if n == 0:
            return self.head
        d = {'javax.faces.source': 'table', 'javax.faces.partial.execute': 'table',
             'javax.faces.partial.render': 'table', 'table': 'table', 'table_pagination': 'true',
             'table_first': str(n * self.rows), 'table_rows': str(self.rows), 'table_page': str(n)}
        if self.kind == 'auctions':
            d['table_encodeFeature'] = 'true'       # без него таблица торгов не листается: отдаёт первую страницу
        return self.ajax(d)


def _plate_rows(x):
    out = {}
    for li in re.findall(r'<li class="ui-datalist-item.*?</li>', x, re.S):
        n = re.search(r'lp-numbers">([^<]*)', li).group(1).strip()
        l = re.search(r'lp-letters">([^<]*)', li).group(1).strip()
        out[n + l] = _num(html.unescape(re.search(r'lp-pub-price">([^<]*)', li).group(1))) or 0
    return out


def fetch_free(region, digits='', series='', status='notSold'):
    """Номера из списка свободной продажи по региону и маске (цифры и/или буквы). -> {sym: цена}.

    status: notSold — свободные, booked — забронированные. Аукционных номеров в этом списке нет вообще.
    """
    lst = _List('plates', region, status, digits, series)
    if lst.total > 64 * 12:
        raise RuntimeError('маска слишком широкая: %d номеров' % lst.total)
    res = _plate_rows(lst.head)
    page_no = 1
    while len(res) < lst.total and page_no * 64 < lst.total:
        got = _plate_rows(lst.page(page_no))
        if not got:
            raise RuntimeError('nomer.srs.kg: пустая страница списка')
        res.update(got)
        page_no += 1
    if digits:
        res = {k: v for k, v in res.items() if k[:3] == digits}
    if series:
        res = {k: v for k, v in res.items() if k[3:] == series}
    return res


def _auction_rows(x):
    out = []
    for tr in re.findall(r'<tr data-ri=.*?</tr>', x, re.S):
        tds = re.findall(r'<td[^>]*>(.*?)</td>', tr, re.S)
        if len(tds) < 9:
            continue
        m = re.fullmatch(r'\s*\d\d (\d{3}) ([A-Z]{3})\s*', tds[2])
        at = re.search(r'\d\d\.\d\d\.\d{4} \d\d:\d\d', tds[8])
        if not m or not at:
            continue
        verif, total = re.search(r':verif">(\d+)', tr), re.search(r':total">(\d+)', tr)
        out.append({'sym': m.group(1) + m.group(2), 'start': _num(html.unescape(tds[3])), 'at': ts(at.group(0)),
                    'verif': int(verif.group(1)) if verif else 0, 'total': int(total.group(1)) if total else 0})
    return out


def fetch_auctions(region, digits='', series='', status=AUC_PLANNED, since=None):
    """Торги из списка аукционов по региону и маске. -> [{'sym', 'start', 'at', 'verif', 'total'}].

    Назначенные (AUC_PLANNED) портал отдаёт от ближайших, несостоявшиеся (AUC_FAILED) — от последних; since обрывает
    чтение на первых торгах раньше этого момента. В странице 10 строк, поэтому длинный список читаем в четыре сессии.
    """
    args = ('auctions', region, 'kg.infocom.auction.enums.AuctionStatus@%d' % status, digits, series)
    first = _List(*args)
    pages = -(-first.total // 10)
    if not pages:
        return []
    if since:
        pages = min(pages, AUC_MAX_PAGES // 4)
    elif pages > AUC_MAX_PAGES:
        raise RuntimeError('маска слишком широкая: %d торгов' % first.total)

    def read(nums):
        lst = first if nums[0] == 0 else _List(*args)
        out = []
        for n in nums:
            got = _auction_rows(lst.page(n))
            if not got:                             # сессия протухла посреди выкачки или список успел сократиться
                lst = _List(*args)
                if n * 10 >= lst.total:
                    break
                got = _auction_rows(lst.page(n))
                if not got:
                    raise RuntimeError('nomer.srs.kg: пустая страница списка торгов')
            if out and got == out[-len(got):]:      # та же страница второй раз — не долбим портал по кругу
                raise RuntimeError('nomer.srs.kg: список торгов не листается')
            out += got
            if since and got[-1]['at'] < since:
                break
        return out

    if since:
        return [a for a in read(range(pages)) if a['at'] >= since]
    size = -(-pages // max(1, min(4, pages // 8)))
    with ThreadPoolExecutor(4) as ex:
        parts = list(ex.map(read, [range(i, min(i + size, pages)) for i in range(0, pages, size)]))
    return [a for part in parts for a in part]
