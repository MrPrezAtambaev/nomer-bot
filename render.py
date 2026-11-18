"""Карточка номера как Telegram-виджет (HTML) и как картинка (PNG, запасной вид)."""
import datetime as dt
import html
import io
import os
import zlib

from card import dm

CSS = (
    '<style>'
    'body{margin:0;padding:12px 14px 8px;font:14px/1.35 system-ui}'
    '.p{display:flex;width:264px;max-width:100%;height:58px;margin:0 auto;box-sizing:border-box;'
    'border:3px solid #111;border-radius:9px;background:#fff;color:#111;font-weight:700}'
    '.r{width:46px;border-right:2px solid #111;display:flex;flex-direction:column;align-items:center;'
    'justify-content:center;line-height:1}'
    '.r b{font-size:19px}'
    '.r span{font-size:10px;margin-top:4px;display:flex;align-items:center;gap:3px}'
    '.r i{width:15px;height:10px;border-radius:1px;background:radial-gradient(circle,#ffd200 0 2.6px,#e8112d 3.2px)}'
    '.n{flex:1;display:flex;align-items:center;justify-content:center;gap:12px;font-size:34px;letter-spacing:3px}'
    '.h{margin:10px 0 3px;font-weight:600;font-size:15px}'
    '.s{color:var(--tg-theme-hint-color);font-size:13px}'
    '.g{color:light-dark(#1a8f5c,#3ccf8e)}.o{color:light-dark(#c2600a,#f0903a)}'
    '.d{color:light-dark(#c9302c,#ef5b56)}.l{color:var(--tg-theme-link-color)}'
    'svg{display:block;width:100%;margin:10px 0 4px}'
    '.f{fill:var(--tg-theme-hint-color)}.a{fill:var(--tg-theme-link-color)}'
    '.c{fill:var(--tg-theme-bg-color);stroke:var(--tg-theme-link-color);stroke-width:1.5;stroke-dasharray:2 2}'
    '.e{fill:light-dark(#1a8f5c,#3ccf8e)}.b{stroke:var(--tg-theme-hint-color);stroke-width:1.5}'
    '.m{stroke:var(--tg-theme-text-color);stroke-width:1.5}'
    'text{font-size:9px;fill:var(--tg-theme-hint-color)}text.e{fill:light-dark(#1a8f5c,#3ccf8e)}'
    '.t{display:flex;gap:8px;font-size:13px;padding:2px 0;font-variant-numeric:tabular-nums}'
    '.t b{min-width:92px;font-weight:600}'
    '</style>'
)
TONE = {'good': 'g', 'warn': 'o', 'bad': 'd', 'link': 'l', 'hint': 's', 'text': ''}


def _scale(tl, now, x0, x1):
    ds = [x['d'] for x in tl]
    lo, hi = min(ds), max(ds)
    span = (hi - lo).total_seconds() or 1
    pos = lambda d: x0 + (x1 - x0) * (d - lo).total_seconds() / span
    today = pos(now) if lo <= now <= hi else None
    return pos, today, lo, hi


def _svg(c, now):
    tl = c['tl']
    if len(tl) < 2:
        return ''
    pos, today, lo, hi = _scale(tl, now, 10, 290)
    out = ['<svg viewBox="0 0 300 34"><path d="M10 11H290" class="b"/>']
    if today is not None:
        out.append('<path d="M%.0f 3V19" class="m"/>' % today)
        if 50 < today < 250:
            out.append('<text x="%.0f" y="31" text-anchor="middle">сегодня</text>' % today)
    for x in tl:
        cls, r = {'fail': ('f', 3.5), 'plan': ('a', 5), 'fc': ('c', 4), 'rel': ('e', 6)}[x['k']]
        out.append('<circle cx="%.0f" cy="11" r="%s" class="%s"/>' % (pos(x['d']), r, cls))
    out.append('<text x="10" y="31">%s</text>' % dm(lo, now))
    last = tl[-1]
    out.append('<text x="290" y="31" text-anchor="end"%s>%s</text>'
               % (' class="e"' if last['k'] == 'rel' else '', dm(hi, now)))
    out.append('</svg>')
    return ''.join(out)


def widget(c, now):
    """HTML виджета. -> (html, сжатый размер в байтах)."""
    e = html.escape
    d3, l3 = c['sym'][:3], c['sym'][3:]
    out = [CSS,
           '<div class="p"><div class="r"><b>%s</b><span><i></i>KG</span></div>'
           '<div class="n"><span>%s</span><span>%s</span></div></div>' % (c['region'], d3, l3),
           '<div class="h %s">%s</div>' % (TONE[c['tone']], e(c['head']))]
    out += ['<div class="s">%s</div>' % e(x) for x in c['sub']]
    if c['warn']:
        out.append('<div class="s o">%s</div>' % e(c['warn']))
    out.append(_svg(c, now))
    for a, b, tone in c['rows']:
        cls = TONE.get(tone, '')
        out.append('<div class="t"><b%s>%s</b><span class="%s">%s</span></div>'
                   % ((' class="%s"' % cls) if tone in ('good', 'warn') else '', e(a),
                      cls if tone != 'text' else '', e(b)))
    doc = ''.join(out)
    return doc, len(zlib.compress(doc.encode('utf-8'), 9))


# ---------- PNG ----------

_FONTS = {}
PLATE_FONTS = ['/System/Library/Fonts/Supplemental/DIN Alternate Bold.ttf',
               '/System/Library/Fonts/Supplemental/Arial Bold.ttf',
               '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf']
BOLD_FONTS = ['/System/Library/Fonts/Supplemental/Arial Bold.ttf',
              '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf']
TEXT_FONTS = ['/System/Library/Fonts/Supplemental/Arial.ttf',
              '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']
COL = {'good': '#1a8f5c', 'warn': '#c2600a', 'bad': '#c9302c', 'link': '#2a78d6', 'hint': '#707579', 'text': '#111111'}


def _font(paths, size):
    from PIL import ImageFont
    key = (tuple(paths), size)
    if key not in _FONTS:
        f = None
        for p in paths:
            if os.path.exists(p):
                f = ImageFont.truetype(p, size)
                break
        _FONTS[key] = f or ImageFont.load_default(size)
    return _FONTS[key]


def _wrap(draw, text, font, width):
    lines, cur = [], ''
    for w in text.split():
        t = (cur + ' ' + w).strip()
        if draw.textlength(t, font=font) <= width or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    return lines + ([cur] if cur else [])


def png(c, now):
    """Картинка карточки. -> bytes PNG или None, если Pillow не установлен."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    W, P = 900, 56
    fh, fs, fr, frb = _font(BOLD_FONTS, 38), _font(TEXT_FONTS, 30), _font(TEXT_FONTS, 30), _font(BOLD_FONTS, 30)
    probe = ImageDraw.Draw(Image.new('RGB', (10, 10)))
    subs = []
    for x in c['sub'] + (['⚠ ' + c['warn']] if c['warn'] else []):
        subs += [(ln, x.startswith('⚠')) for ln in _wrap(probe, x.replace('⚠ ', '! '), fs, W - 2 * P)]
    has_tl = len(c['tl']) >= 2
    H = P + 170 + 36 + 54 + 42 * len(subs) + (130 if has_tl else 20) + 50 * len(c['rows']) + P - 10
    im = Image.new('RGB', (W, H), '#ffffff')
    d = ImageDraw.Draw(im)

    # номерной знак
    pw, ph = 700, 160
    x0, y0 = (W - pw) // 2, P
    d.rounded_rectangle((x0, y0, x0 + pw, y0 + ph), 24, fill='#ffffff', outline='#111111', width=8)
    rx = x0 + 128
    d.line((rx, y0 + 4, rx, y0 + ph - 4), fill='#111111', width=6)
    f_reg, f_kg, f_num = _font(PLATE_FONTS, 62), _font(PLATE_FONTS, 30), _font(PLATE_FONTS, 118)
    d.text((x0 + 66, y0 + 54), c['region'], font=f_reg, fill='#111111', anchor='mm')
    fx, fy = x0 + 24, y0 + 100
    d.rounded_rectangle((fx, fy, fx + 44, fy + 30), 3, fill='#e8112d')
    d.ellipse((fx + 14, fy + 7, fx + 30, fy + 23), fill='#ffd200')
    d.text((fx + 52, fy + 15), 'KG', font=f_kg, fill='#111111', anchor='lm')
    d.text(((rx + x0 + pw) // 2, y0 + ph // 2 + 4), '%s  %s' % (c['sym'][:3], c['sym'][3:]),
           font=f_num, fill='#111111', anchor='mm')

    y = y0 + ph + 36
    d.text((P, y), c['head'], font=fh, fill=COL[c['tone']] if c['tone'] != 'hint' else COL['text'])
    y += 58
    for ln, warn in subs:
        d.text((P, y), ln, font=fs, fill=COL['warn'] if warn else COL['hint'])
        y += 42
    if has_tl:
        y += 30
        from render import _scale as sc
        pos, today, lo, hi = sc(c['tl'], now, P + 14, W - P - 14)
        d.line((P + 14, y, W - P - 14, y), fill='#b9bdc2', width=4)
        if today is not None:
            d.line((today, y - 22, today, y + 22), fill='#111111', width=4)
            if P + 150 < today < W - P - 150:
                d.text((today, y + 46), 'сегодня', font=_font(TEXT_FONTS, 24), fill=COL['hint'], anchor='mm')
        for x in c['tl']:
            cx = pos(x['d'])
            if x['k'] == 'fail':
                d.ellipse((cx - 9, y - 9, cx + 9, y + 9), fill='#9aa0a6')
            elif x['k'] == 'plan':
                d.ellipse((cx - 13, y - 13, cx + 13, y + 13), fill=COL['link'])
            elif x['k'] == 'fc':
                d.ellipse((cx - 11, y - 11, cx + 11, y + 11), fill='#ffffff', outline=COL['link'], width=4)
            else:
                d.ellipse((cx - 16, y - 16, cx + 16, y + 16), fill=COL['good'])
        f_l = _font(TEXT_FONTS, 24)
        d.text((P + 14, y + 46), dm(lo, now), font=f_l, fill=COL['hint'], anchor='lm')
        d.text((W - P - 14, y + 46), dm(hi, now), font=f_l,
               fill=COL['good'] if c['tl'][-1]['k'] == 'rel' else COL['hint'], anchor='rm')
        y += 100
    else:
        y += 20
    for a, b, tone in c['rows']:
        col = COL.get(tone, COL['text'])
        d.text((P, y), a, font=frb, fill=col if tone in ('good', 'warn') else COL['text'])
        d.text((P + 270, y), b, font=fr, fill=col)
        y += 50
    buf = io.BytesIO()
    im.save(buf, 'PNG', optimize=True)
    return buf.getvalue()
