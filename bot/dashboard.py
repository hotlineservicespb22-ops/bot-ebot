#!/usr/bin/env python3
"""Dashboard WebApp: pure HTML+CSS bars, no JavaScript needed."""
import html as _html
import json
import sys

CSS = (
    '*{margin:0;padding:0;box-sizing:border-box}'
    'body{font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#1a1a2e;color:#e0e0e0;padding:10px;min-height:100vh;font-size:14px}'
    'h1{font-size:18px;text-align:center;margin:0 0 12px;color:#e94560}'
    'h2{font-size:14px;margin:0 0 8px;opacity:.9}'
    '.kpi-row{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px}'
    '.kpi{background:#16213e;border-radius:8px;padding:8px 6px;flex:1 1 auto;min-width:55px;text-align:center}'
    '.kpi .num{font-size:20px;font-weight:bold;display:block}'
    '.kpi .label{font-size:10px;opacity:.7;display:block;margin-top:2px}'
    '.kpi .sub{font-size:9px;opacity:.5}'
    '.green .num{color:#0f9b58}.yellow .num{color:#f0a500}.red .num{color:#e94560}.blue .num{color:#4361ee}'
    '.card{background:#16213e;border-radius:8px;padding:10px;margin-bottom:8px}'
    'table{width:100%;border-collapse:collapse;font-size:13px;margin-top:4px}'
    'th,td{padding:5px 8px;text-align:left;border-bottom:1px solid rgba(255,255,255,.06)}'
    'th{opacity:.5;font-weight:500;font-size:11px}'
    '.stars{color:#f0a500;font-size:13px}'
    '.bar-wrap{display:flex;align-items:center;margin:3px 0;gap:6px}'
    '.bar-label{width:105px;font-size:12px;text-align:right;flex-shrink:0}'
    '.bar-track{flex:1;height:16px;background:rgba(255,255,255,.05);border-radius:4px;overflow:hidden}'
    '.bar-fill{height:100%;border-radius:4px;min-width:2px}'
    '.bar-val{font-size:11px;min-width:30px;text-align:right}'
    '.row{display:flex;flex-wrap:wrap;gap:0 8px}'
    '.half{width:calc(50% - 4px);display:inline-block;vertical-align:top}'
)

def _bar(label, value, max_val, color='#4361ee', suffix=''):
    """Renders a horizontal bar. Always escapes label for XSS protection."""
    pct = min(value / max(max_val, 1) * 100, 100)
    escaped_label = _html.escape(str(label))
    return (
        '<div class="bar-wrap"><span class="bar-label">' + escaped_label + '</span>'
        '<div class="bar-track"><div class="bar-fill" style="width:' + str(int(pct)) + '%;background:' + color + '"></div></div>'
        '<span class="bar-val">' + str(value) + str(suffix) + '</span></div>'
    )

def generate_dashboard(data_json: str) -> str:
    d = json.loads(data_json)
    parts = []

    # KPI
    trend_html = ''
    tw = d['this_week']
    pw = d['prev_week']
    if pw > 0:
        ch = (tw - pw) / pw * 100
        color = '#e94560' if ch >= 0 else '#0f9b58'
        sign = '+' if ch >= 0 else ''
        trend_html = '<span class="sub" style="color:' + color + '">' + sign + str(int(ch)) + '% к прош.</span>'
    tw_cls = 'red' if tw < pw else 'green'

    parts.append('<div class="kpi-row">')
    parts.append('<div class="kpi green"><span class="num">' + str(d['total']) + '</span><span class="label">Всего</span></div>')
    parts.append('<div class="kpi yellow"><span class="num">' + str(d['open']) + '</span><span class="label">Открыто</span></div>')
    parts.append('<div class="kpi red"><span class="num">' + str(d['in_progress']) + '</span><span class="label">В работе</span></div>')
    parts.append('<div class="kpi green"><span class="num">' + str(d['closed']) + '</span><span class="label">Закрыто</span></div>')
    parts.append('<div class="kpi blue"><span class="num">' + str(d['reaction_min']) + 'м</span><span class="label">Реакция</span></div>')
    parts.append('<div class="kpi ' + tw_cls + '"><span class="num">' + str(tw) + '</span><span class="label">За неделю</span>' + trend_html + '</div>')
    parts.append('<div class="kpi"><span class="num">' + str(d['repeat_pct']) + '%</span><span class="label">Повторных</span></div>')
    parts.append('</div>')

    # Статусы
    sf = d['status_flow']
    sm = max(sf) if max(sf) else 1
    parts.append('<div class="card"><h2>Статусы</h2>')
    parts.append(_bar('Открыто', sf[0], sm, '#e94560'))
    parts.append(_bar('В работе', sf[1], sm, '#4361ee'))
    parts.append(_bar('Завершено', sf[2], sm, '#0f9b58'))
    parts.append(_bar('Отменено', sf[3], sm, '#e94560'))
    parts.append('</div>')

    # Оборудование + Оценки
    eq = d['equipment']
    em = max(e['cnt'] for e in eq) if eq else 1
    ecols = ['#e94560','#4361ee','#0f9b58','#f0a500','#7209b7']
    parts.append('<div class="row"><div class="card half"><h2>Оборудование</h2>')
    for i, e in enumerate(eq):
        parts.append(_bar(str(e['type']), e['cnt'], em, ecols[i % 5]))
    parts.append('</div>')

    rd = d['ratings_dist']
    rm = max(rd) if max(rd) else 1
    rcols = ['#e94560','#f0a500','#f0a500','#0f9b58','#0f9b58']
    rlbl = ['1star','2star','3star','4star','5star']
    parts.append('<div class="card half"><h2>Оценки</h2>')
    for i in range(5):
        parts.append(_bar(rlbl[i], rd[i], rm, rcols[i]))
    parts.append('</div></div>')

    # Топ проблем
    pr = d['problems']
    pm = max(p['cnt'] for p in pr) if pr else 1
    parts.append('<div class="card"><h2>Топ проблем</h2>')
    for p in pr:
        parts.append(_bar(str(p['name']), p['cnt'], pm, '#e94560'))
    parts.append('</div>')

    # По дням
    dl = d['daily_labels']
    dc = d['daily_counts']
    dm = max(dc) if dc else 1
    parts.append('<div class="card"><h2>По дням</h2>')
    for i, lbl in enumerate(dl):
        parts.append(_bar(str(lbl), dc[i], dm, '#e94560'))
    parts.append('</div>')

    # Дни недели
    dw = d['dow_load']
    dwm = max(dw) if max(dw) else 1
    dwc = ['#e94560','#4361ee','#4361ee','#4361ee','#4361ee','#4361ee','#0f9b58']
    parts.append('<div class="card"><h2>Дни недели</h2>')
    for i in range(7):
        parts.append(_bar(str(d['day_names'][i]), dw[i], dwm, dwc[i]))
    parts.append('</div>')

    # Инженеры
    en = d['eng_names']
    el = d['eng_loads']
    et = d['eng_avg_times']
    ec = d['eng_colors']
    elm = max(el) if max(el) else 1
    etm = max(et) if max(et) else 1
    parts.append('<div class="row"><div class="card half"><h2>Загрузка</h2>')
    for i in range(len(en)):
        parts.append(_bar(str(en[i]), el[i], elm, ec[i]))
    parts.append('</div><div class="card half"><h2>Время (ч)</h2>')
    for i in range(len(en)):
        parts.append(_bar(str(en[i]), et[i], etm, ec[i], 'ч'))
    parts.append('</div></div>')

    # Рейтинг
    parts.append('<div class="card"><h2>Рейтинг</h2><table><thead><tr><th>Инженер</th><th>Оценка</th><th>Заявок</th></tr></thead><tbody>')
    for r in d['ratings']:
        stars = '★' * round(r['rating'])
        parts.append('<tr><td>' + _html.escape(str(r['name'])) + '</td><td class="stars">' + stars + ' ' + str(round(r['rating'], 1)) + '</td><td>' + str(r['count']) + '</td></tr>')
    parts.append('</tbody></table></div>')

    # Города
    parts.append('<div class="card"><h2>Города</h2><table><thead><tr><th>Город</th><th>Заявок</th></tr></thead><tbody>')
    for c in d['cities']:
        parts.append('<tr><td>' + _html.escape(str(c['city'])) + '</td><td>' + str(c['cnt']) + '</td></tr>')
    parts.append('</tbody></table></div>')

    return '<!DOCTYPE html><html lang="ru"><head>\n<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0,maximum-scale=3.0,user-scalable=yes">\n<title>Hotline Service - Дашборд</title>\n<style>' + CSS + '</style></head><body>\n<h1>Hotline Service</h1>\n' + '\n'.join(parts) + '\n</body></html>'

if __name__ == '__main__':
    data = json.loads(sys.stdin.read())
    print(generate_dashboard(json.dumps(data, ensure_ascii=False)))
