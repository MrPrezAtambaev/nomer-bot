"""Красивые («блатные») номера: оценка номера и подборки из списка свободной продажи."""
import card
import srs

PER_PAGE = 25       # номеров на странице подборки
TOP = 50            # сколько номеров в «Лучших» и «Недорогих»
SAME_SERIES = 2     # в них же — не больше стольких номеров с одними буквами: иначе одна серия займёт весь список
TRIPLES = 12        # и не больше стольких с тремя одинаковыми буквами: для них есть своя подборка
CHEAP = 10000       # «недорогие»: до этой цены, сом

# Подборки: ключ -> (кнопка, пояснение под заголовком). Порядок — порядок кнопок.
CATS = {
    'top': ('🔥 Лучшие', 'Сильные цифры и понятные буквы, от самых красивых.'),
    'cheap': ('💰 До 10 000', 'Самые красивые не дороже 10 000 сом.'),
    'num': ('👑 Цифры', 'Тройки, 00X, X00, 0X0, 007 — с любыми буквами.'),
    'mir': ('🪞 Зеркальные', 'Цифры и буквы читаются одинаково с двух сторон.'),
    'tri': ('🔠 ААА', 'Три одинаковые буквы с любыми цифрами.'),
    'kg': ('🇰🇬 Кыргызча', 'Буквы — кыргызское слово.'),
    'word': ('💬 Слова', 'Буквы — русское или английское слово.'),
    'name': ('🙋 Имена', 'Буквы — имя.'),
    'auto': ('🚗 Авто', 'Марки, модели, автожаргон.'),
    'cool': ('⭐️ Статус', 'Бренды, клубы, сокращения.'),
}

# Словарь букв. Основа — страница владельца «Номера 01 Бишкек» и её cur.py (17.11.2026), остальное добавлено 24.11.2026
# по списку свободных. Часть слов читается кириллицей (HYP — НУР). Запрещённых порталом сочетаний здесь нет — проверено.
WORDS = {
    'kg': '''AGA ага, старший брат|AIT айт, праздник|AKE аке|ALP алп, великан|ANA ана, мама|APA апа, мама|ATA ата, отец
        BAI бай|BAK бак, удача|BAL бал, мёд|BAR бар, есть|BAY бай, богач|BEK бек|BEL бел, перевал|BIR бир, один
        CYY суу, вода|DEM дем, дух|DIL дил, душа|DOS дос, друг|DUA дуба, молитва|EKI эки, два|ERK эрк, воля
        GUL гүл, цветок|HAH НАН, хлеб|HAN хан|HYP НУР, свет|ISH иш, работа|JAN жан, душа|JAZ жаз, весна|JOL жол, дорога
        JUZ жүз, сто|JYL жыл, год|KAP КАР, снег|KAR кар, снег|KEL кел, иди сюда|KET КЕТ, уходи|KGZ Кыргызстан
        KOK көк, синий|KOL көл, озеро|KUN күн, солнце|KUT кут, счастье|KYH КҮН, солнце|KYT КУТ, счастье|KYZ кыз, девушка
        MAL мал, скот|MEH МЕН, я|MUZ муз, лёд|MYK мык, крутой|NAN нан, хлеб|NUR нур, свет|OSH Ош|SIZ сиз, вы|SOM сом
        SOZ сөз, слово|SUU суу, вода|TAN таң, рассвет|TAY тай, жеребёнок|TEZ тез, быстро|TIL тил, язык|TOO тоо, гора
        TOY той, праздник|TUU туу, флаг|TYH ТҮН, ночь|ULU улуу, великий|XAH ХАН|ZOR зор, здорово''',
    'word': '''AHA ага|AIR воздух|ANT муравей|AXE топор|BAD плохой|BAX ВАХ|BEC ВЕС|BIN корзина|BIZ бизнес|BOI бой|BOS босс
        BOT бот|BOX бокс|BRO бро|BUM бум|BUS автобус|BYE пока|CAH САН|CAM САМ|CAN могу|CAR машина|CAT кот|CMC СМС|COM СОМ
        DAD папа|DAR дар|DED дед|DOC док|DOG пёс|DOK док|DUO дуэт|ECO эко|EGG яйцо|ELF эльф|ERA эра|FIT в форме|FLY лети
        FOX лиса|GAS газ|GIF гифка|GOD бог|GUN пушка|GYM зал|HET НЕТ|HEY эй|HIT хит|HOC НОС|IAM я есть|ICE лёд|IZI изи
        JAM джем|JOY радость|KAK КАК|KEK кек|KEY ключ|KID ребёнок|KIT кит|KMC КМС|KOM КОМ|KTO КТО|KUB куб|KUM кум|LAW закон
        LED свет|LES лес|LEV лев|LOM лом|LOV love|LOW низкий|LVL level|MAD безумный|MAG маг|MAK мак|MAN мужик|MAY май
        MED мёд|MEM мем|MEN мужики|MIR мир|MOM мама|MUR мур|NET нет|NEW новый|NOL ноль|NOW сейчас|OFF выкл|OHO ОНО
        OIL нефть|OKO ОКО|OLD старый|ONE один|OPA опа!|OUR наш|PAY плати|POK РОК|POM РОМ|POP поп|POT РОТ|PRO про|PYC РУС
        RAP рэп|RAT крыса|RAY луч|RED красный|RIM Рим|ROK рок|ROM ром|RUM ром|RUN беги|SAD сад|SAY скажи|SEA море
        SEE смотри|SEM семь|SIR сэр|SKY небо|SOK сок|SON сон, сын|SOS SOS|SPA спа|SUN солнце|TAX налог|TEN десять|THE the
        TOK ток|TON тонна|TRI три|TRY попробуй|TUR тур|TUT тут|TUZ туз|TWO два|UNO один|VEK век|WHO кто|WHY почему|WIN победа
        WOW вау|XAX ХАХ|XEX ХЕХ|YES да|YEP ага|YOU ты|YXA УХА|ZAL зал|ZAR царь|ZUB зуб''',
    'name': '''ALI Али|ALX Алекс|AMY Эми|ANN Анна|ARI Ари|AVA Ава|AYA Ая|AZA Аза|BEN Бен|BIL Билл|BOB Боб|DAN Дан|DEN Ден
        DIM Дима|DIN Дин|DON Дон|EBA ЕВА|ELI Эли|EMA Эма|ERL Ерлан|EVA Ева|IRA Ира|ISA Иса|ISL Ислам|JEK Джек|JON Джон
        KAI Кай|KEN Кен|KIM Ким|KIR Кир|LEA Лея|LEE Ли|LEO Лео|LIA Лия|LIL Лил|MAX Макс|MIA Миа|NAZ Наз|NEO Нео|NIA Ния
        NIK Ник|OMA Ома|REX Рекс|RIK Рик|ROZ Роза|RUS Рус|SAL Сал|SAM Сэм|SID Сид|TIM Тим|TOR Тор|ULA Ула|UMA Ума
        YAN Ян|ZIA Зия''',
    'auto': '''AMG AMG|AWD полный привод|BMW BMW|BRZ Subaru BRZ|BTR БТР|BYD BYD|EVO Lancer Evo|GAZ ГАЗ|GMC GMC|GTR Nissan GT-R
        JDM JDM|JZX Toyota JZX|KIA Kia|KTM KTM|MBZ Mercedes-Benz|NFS Need for Speed|NIO NIO|RAM Dodge RAM|RAV RAV4|RIO Kia Rio
        STI Subaru STI|SUV внедорожник|TDI TDI|TLC Land Cruiser|TRD Toyota TRD|UAZ УАЗ|VAG VAG|VAN фургон|VAZ ВАЗ|VIN VIN-код
        WRX Subaru WRX|XLE Toyota XLE|ZAZ ЗАЗ|ZIL ЗИЛ''',
    'cool': '''AMD AMD|APP приложение|ATM банкомат|BFF лучшие друзья|BNB BNB|BTC биткоин|BTS BTS|BZB B2B|CEO гендиректор
        CFO финдиректор|DEV dev|DJI DJI|DMX DMX|DNA ДНК|DVD DVD|DXB Дубай|EUR евро|FAQ FAQ|FCB Барселона|GPT GPT|GRR грр
        GUM ГУМ|IBM IBM|JBL JBL|KFC KFC|KGB КГБ|KGS код сома|MAC Mac|MIG МиГ|MMA MMA|MOB моб|MSK Москва|MTV MTV|MVP MVP
        NBA NBA|NSA NSA|NYC Нью-Йорк|OMG о боже|PDF PDF|PSG ПСЖ|QWE клавиатура|RPG РПГ|RUB рубль|SAS SAS|SPB Петербург
        SUB саб|SZS S2S|TNT тротил|UAE ОАЭ|UFC UFC|USA США|USB USB|USD доллар|VII семь|VIP VIP|VPN VPN|WBC бокс WBC
        XXL XXL|ZXC клавиатура''',
}
DICT = {}           # буквы -> (подборка, смысл)
for _cat, _text in WORDS.items():
    for _item in _text.replace('\n', '|').split('|'):
        if _item.strip():
            _l, _, _g = _item.strip().partition(' ')
            DICT[_l] = (_cat, _g)

# Сочетания из реестра запрещённых: портал держит такие номера в списке свободных, но не выдаёт.
BLOCKED = set('AYE CHN GEI GEY GNS GTS IIM KAZ KOT MEF MID WOR XYU ZLO'.split())

DIGITS = {'777': 100, '001': 100, '888': 92, '999': 90, '111': 90, '555': 85, '007': 85, '222': 78, '333': 78, '444': 76,
          '666': 70, '100': 72}
CODES = {'996': (55, 'код Кыргызстана'), '312': (45, 'код Бишкека'), '911': (45, '911'), '786': (45, '786'),
         '570': (50, 'Lexus LX 570'), '470': (45, 'Lexus 470'), '460': (45, 'Lexus GX 460')}
WORD_SCORE = {'kg': 62, 'word': 58, 'auto': 58, 'cool': 55, 'name': 52}


def digits(n):
    """Цифры -> (оценка, подпись)."""
    a, b, c = n
    if a == b == c or n == '007':
        return DIGITS.get(n, 70), 'тройка' if n != '007' else '007'
    if a == b == '0':
        return DIGITS.get(n, 70), '00X'
    if b == c == '0':
        return DIGITS.get(n, 64), 'сотня'
    if a == c == '0':
        return 58, '0X0'
    if n in CODES:
        return CODES[n]
    if a == c:
        return (44 if b == '0' else 38), 'зеркальные цифры'
    step = {(int(b) - int(a)), (int(c) - int(b))}
    if step == {1}:
        return 55 if n == '123' else 40, 'по порядку'
    if step == {-1}:
        return 32, 'по порядку'
    if a == b or b == c:
        return (28 if '0' in n else 14), 'пара'
    return 0, ''


def letters(s):
    """Буквы -> (оценка, подпись)."""
    if s[0] == s[1] == s[2]:
        return 75, 'три одинаковые'
    pal = s[0] == s[2]
    if s in DICT:
        cat, gloss = DICT[s]
        return WORD_SCORE[cat] + (8 if pal else 0), gloss
    if pal:
        return 34, ''
    return (10 if s[0] == s[1] or s[1] == s[2] else 0), ''


def rate(sym):
    """Номер -> (оценка, подпись «чем красив»)."""
    n, s = sym[:3], sym[3:]
    (dn, tn), (ds, ts) = digits(n), letters(s)
    mirror = n[0] == n[2] and s[0] == s[2]
    tn = '' if tn == 'пара' else tn
    note = [x for x in (ts, 'зеркальный' if mirror and not (tn == 'тройка' or ts == 'три одинаковые') else tn) if x]
    return dn + ds + (18 if mirror else 0), ' · '.join(note)


def fits(cat, sym, price):
    n, s = sym[:3], sym[3:]
    if cat == 'num':
        return digits(n)[0] >= 58
    if cat == 'mir':
        return n[0] == n[2] and s[0] == s[2]
    if cat == 'tri':
        return s[0] == s[1] == s[2]
    if cat in WORDS:
        return s in DICT and DICT[s][0] == cat
    # top и cheap: и цифры, и буквы чем-то хороши; одни сильные цифры со случайными буквами — это «Цифры»
    return digits(n)[0] >= 14 and letters(s)[0] >= 34 and (cat == 'top' or price <= CHEAP)


def pick(cat, free):
    """Подборка из свободных {sym: цена}. -> [(sym, цена, подпись)], от самых красивых, при равной красоте — дешевле."""
    rows = [(rate(sym), sym, price) for sym, price in free.items() if sym[3:] not in BLOCKED and fits(cat, sym, price)]
    rows.sort(key=lambda r: (-r[0][0], r[2], r[1]))
    if cat in ('top', 'cheap'):
        seen, out = {}, []
        for r in rows:
            s = r[1][3:]
            keys = [(s, SAME_SERIES)] + ([('AAA*', TRIPLES)] if s[0] == s[1] == s[2] else [])
            if all(seen.get(k, 0) < limit for k, limit in keys):
                seen.update({k: seen.get(k, 0) + 1 for k, _ in keys})
                out.append(r)
        rows = out[:TOP]
    return [(sym, price, note) for (_, note), sym, price in rows]


def pages(region, cat, free, at):
    """Страницы подборки для Telegram (HTML). at — когда собран список свободных."""
    rows = pick(cat, free)
    head = '<b>%s</b> · %s %s\n%s' % (CATS[cat][0], region, srs.REGIONS[region], CATS[cat][1])
    tail = '\n\nВсе — свободная продажа без аукциона, цена в сомах. Список на %s. Нажми на номер — пришлю карточку.' \
        % at.strftime('%H:%M')
    if not rows:
        return [head + '\n\nСейчас таких свободных номеров нет.' + tail]
    out = []
    for i in range(0, len(rows), PER_PAGE):
        lines = ['%s — %s%s' % (card.pcmd(region, sym), card.money(price), ' · ' + note if note else '')
                 for sym, price, note in rows[i:i + PER_PAGE]]
        out.append('%s\n\n%s%s' % (head, '\n'.join(lines), tail))
    return out
