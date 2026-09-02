"""
Веб-дашборд руководителя Hotline Service.

Встроенный aiohttp-сервер с двумя страницами:
  GET /               — список проблемных заявок (оценка ≤ 3)
  GET /ticket/{id}    — детальный просмотр заявки + переписка без медиа

Аутентификация: заголовок X-Dashboard-Key (query-параметр ?key= оставлен для
навигации по ссылкам). Сравнение — hmac.compare_digest (constant-time).
Если MANAGER_DASHBOARD_KEY пуст — доступ открыт (только localhost, dev-режим).
"""

import hmac
import html as _html
import logging

from aiohttp import web

from bot.config import MANAGER_DASHBOARD_KEY, SLA_MINUTES
from bot.database import Database

logger = logging.getLogger(__name__)

# ─── CSS (единый тёмный стиль в духе dashboard.py) ────────────────────────

CSS = (
    '*{margin:0;padding:0;box-sizing:border-box}'
    'body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;'
    'background:#0d1526;color:#c8cdd8;padding:20px;min-height:100vh;font-size:14px;line-height:1.5}'
    '.header{display:flex;align-items:center;justify-content:space-between;margin-bottom:24px;flex-wrap:wrap;gap:12px}'
    '.header h1{font-size:22px;font-weight:700;color:#f5a623}'
    '.header .badge{font-size:10px;background:rgba(245,166,35,.12);color:#f5a623;'
    'padding:5px 12px;border-radius:4px;font-weight:600;text-transform:uppercase;letter-spacing:.5px}'
    '.kpi-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin-bottom:24px}'
    '.kpi{background:#141e33;border:1px solid rgba(255,255,255,.05);border-radius:10px;padding:14px 16px}'
    '.kpi .kpi-val{font-size:28px;font-weight:800;color:#f5a623;display:block;line-height:1;margin-bottom:4px}'
    '.kpi .kpi-lbl{font-size:10px;color:#6b7280;text-transform:uppercase;letter-spacing:.5px;font-weight:600}'
    '.card{background:#141e33;border:1px solid rgba(255,255,255,.05);border-radius:10px;padding:20px;margin-bottom:16px}'
    '.card h2{font-size:11px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:.8px;margin-bottom:16px}'
    'table{width:100%;border-collapse:collapse;font-size:12px}'
    'th{padding:10px 12px;text-align:left;font-size:10px;font-weight:600;color:#6b7280;'
    'text-transform:uppercase;letter-spacing:.5px;border-bottom:1px solid rgba(255,255,255,.08);background:rgba(0,0,0,.15)}'
    'td{padding:9px 12px;border-bottom:1px solid rgba(255,255,255,.04)}'
    'tr:hover td{background:rgba(255,255,255,.02)}'
    'a{color:#f5a623;text-decoration:none;font-weight:600}'
    'a:hover{text-decoration:underline}'
    '.stars{color:#f5a623;font-size:13px;letter-spacing:1px}'
    '.status{display:inline-block;padding:2px 8px;border-radius:4px;font-size:10px;font-weight:600;text-transform:uppercase}'
    '.status.open{background:rgba(245,166,35,.15);color:#f5a623}'
    '.status.in_progress{background:rgba(67,97,238,.15);color:#4361ee}'
    '.status.completed{background:rgba(15,155,88,.15);color:#0f9b58}'
    '.status.canceled{background:rgba(233,69,96,.15);color:#e94560}'
    '.back-link{display:inline-flex;align-items:center;gap:6px;color:#6b7280;font-size:12px;margin-bottom:16px}'
    '.back-link:hover{color:#c8cdd8}'
    '.chat-msg{margin:12px 0;padding:12px 16px;background:#0d1526;border-radius:8px;border-left:3px solid #141e33}'
    '.chat-msg.client{border-left-color:#f5a623}'
    '.chat-msg.engineer{border-left-color:#4361ee}'
    '.chat-msg .sender{font-size:10px;color:#6b7280;text-transform:uppercase;letter-spacing:.4px;margin-bottom:4px}'
    '.chat-msg .text{font-size:13px;color:#c8cdd8;white-space:pre-wrap}'
    '.ticket-info{display:grid;grid-template-columns:1fr 1fr;gap:8px 20px;margin-bottom:20px}'
    '.ticket-info dt{font-size:10px;color:#6b7280;text-transform:uppercase;letter-spacing:.4px}'
    '.ticket-info dd{font-size:13px;color:#c8cdd8;margin-bottom:8px}'
    '.media-note{font-size:11px;color:#6b7280;background:rgba(255,255,255,.03);padding:8px 12px;border-radius:6px;margin:16px 0}'
    '@media(max-width:700px){body{padding:10px}.ticket-info{grid-template-columns:1fr}}'
)

# ─── Аутентификация ────────────────────────────────────────────────────────


def _check_auth(request: web.Request) -> bool:
    """Проверяет ключ доступа: заголовок X-Dashboard-Key или query ?key=.

    Сравнение выполняется через hmac.compare_digest (constant-time), чтобы
    исключить возможность timing-атаки. Заголовок — основной способ (не попадает
    в URL, логи и историю браузера); query-параметр оставлен для навигации
    по внутренним ссылкам и ссылкам из Telegram.
    """
    if not MANAGER_DASHBOARD_KEY:
        return True  # ключ не задан — доступ открыт (только localhost, dev-режим)
    header_key = request.headers.get("X-Dashboard-Key", "")
    if header_key and hmac.compare_digest(header_key, MANAGER_DASHBOARD_KEY):
        return True
    return hmac.compare_digest(request.query.get("key", ""), MANAGER_DASHBOARD_KEY)


def _base_page(title: str, body: str) -> str:
    """Оборачивает контент в HTML-страницу с тёмной темой."""
    return (
        f"<!DOCTYPE html><html lang=\"ru\"><head><meta charset=\"utf-8\">"
        f"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{_html.escape(title)} — Hotline Manager</title>"
        f"<style>{CSS}</style></head><body>{body}</body></html>"
    )


# ─── Хелперы для статусов и оценок ─────────────────────────────────────────


def _status_badge(status: str) -> str:
    labels = {
        "open": "Открыта",
        "in_progress": "В работе",
        "completed": "Завершена",
        "canceled": "Отменена",
    }
    label = labels.get(status, status)
    return f'<span class="status {status}">{label}</span>'


def _stars_html(rating: int) -> str:
    return f'<span class="stars">{"⭐" * rating}</span>'


# ─── Страница: список проблемных заявок (GET /) ────────────────────────────


async def _index_handler(request: web.Request, db: Database) -> web.Response:
    if not _check_auth(request):
        return web.Response(text="Unauthorized", status=401)

    tickets = await db.low_rated.get_list(limit=200, offset=0)
    total = await db.low_rated.count()
    avg = await db.low_rated.avg_rating()
    week = await db.low_rated.this_week()
    dash = await db.get_dashboard_data(sla_minutes=SLA_MINUTES)

    key_param = ""
    if MANAGER_DASHBOARD_KEY:
        key_param = f"?key={MANAGER_DASHBOARD_KEY}"

    # KPI-блоки
    avg_str = f"{avg:.1f}" if avg is not None else "—"
    kpi_html = (
        f'<div class="kpi-row">'
        f'<div class="kpi"><span class="kpi-val">{total}</span>'
        f'<span class="kpi-lbl">Проблемных заявок</span></div>'
        f'<div class="kpi"><span class="kpi-val">{week}</span>'
        f'<span class="kpi-lbl">За последние 7 дней</span></div>'
        f'<div class="kpi"><span class="kpi-val">{avg_str}</span>'
        f'<span class="kpi-lbl">Средняя оценка проблемных</span></div>'
        f'<div class="kpi"><span class="kpi-val" id="w-session">{dash["session_stats"]["avg_seconds"] // 60}м</span>'
        f'<span class="kpi-lbl">Среднее время сессии</span></div>'
        f'<div class="kpi"><span class="kpi-val" id="w-sla">{dash["sla_stats"]["in_sla_pct"]}%</span>'
        f'<span class="kpi-lbl">Заявок взято в SLA</span></div>'
        f'<div class="kpi"><span class="kpi-val" id="w-followup">{dash["followup_stats"]["sent_week"]}/{dash["followup_stats"]["reopened_week"]}</span>'
        f'<span class="kpi-lbl">Опросы за нед. (отпр/повтор)</span></div>'
        f'<div class="kpi"><span class="kpi-val" id="w-avg-reaction">{dash["avg_reaction"]["avg_min"]}м</span>'
        f'<span class="kpi-lbl">Среднее время реакции</span></div>'
        f"</div>"
    )

    # Таблица заявок
    if tickets:
        rows = []
        for t in tickets:
            ticket_link = (
                f'<a href="/ticket/{t["id"]}{key_param}">#{t["id"]}</a>'
            )
            eng = _html.escape(str(t["engineer_name"] or "—"))
            client = _html.escape(str(t["client_name"] or "—"))
            stars = _stars_html(t["rating"] or 0)
            msg_count = t["message_count"] or 0
            rows.append(
                f"<tr>"
                f"<td>{ticket_link}</td>"
                f"<td>{eng}</td>"
                f"<td>{stars}</td>"
                f"<td>{client}</td>"
                f"<td>{msg_count}</td>"
                f"<td>{_status_badge(t['status'])}</td>"
                f"</tr>"
            )
        table_html = (
            f'<div class="card"><h2>Проблемные заявки (всего: {total})</h2>'
            f"<table><thead><tr>"
            f"<th>№</th><th>Инженер</th><th>Оценка</th>"
            f"<th>Клиент</th><th>Сообщений</th><th>Статус</th>"
            f"</tr></thead><tbody>"
            f"{''.join(rows)}"
            f"</tbody></table></div>"
        )
    else:
        table_html = (
            '<div class="card"><h2>Проблемные заявки</h2>'
            '<p style="color:#0f9b58;font-size:14px">'
            "✅ Проблемных заявок нет! Все оценки выше 3.</p></div>"
        )

    body = (
        f'<div class="header">'
        f"<h1>🔴 Дашборд руководителя</h1>"
        f'<span class="badge">Hotline Service</span>'
        f"</div>"
        f"{kpi_html}"
        f"{table_html}"
        '<script>'
        "(function(){"
        "  var q = window.location.search || '';"
        "  function refresh(){"
        "    fetch('/api/dashboard-data' + q)"
        "      .then(function(r){ if(!r.ok){ return; } return r.json(); })"
        "      .then(function(d){"
        "        if(!d){ return; }"
        "        if(d.session_stats){ var e=document.getElementById('w-session');"
        "          if(e){ e.textContent = Math.floor((d.session_stats.avg_seconds||0)/60) + 'м'; } }"
        "        if(d.sla_stats){ var e2=document.getElementById('w-sla');"
        "          if(e2){ e2.textContent = (d.sla_stats.in_sla_pct||0) + '%'; } }"
        "        if(d.followup_stats){ var e3=document.getElementById('w-followup');"
        "          if(e3){ e3.textContent = (d.followup_stats.sent_week||0) + '/' + (d.followup_stats.reopened_week||0); } }"
        "        if(d.avg_reaction){ var e4=document.getElementById('w-avg-reaction');"
        "          if(e4){ e4.textContent = (d.avg_reaction.avg_min||0) + 'м'; } }"
        "      })"
        "      .catch(function(){});"
        "  }"
        "  setInterval(refresh, 20000);"
        "})();"
        "</script>"
    )
    return web.Response(
        text=_base_page("Дашборд", body),
        content_type="text/html",
    )


# ─── Страница: детальный просмотр заявки (GET /ticket/{id}) ─────────────────


async def _ticket_handler(request: web.Request, db: Database) -> web.Response:
    if not _check_auth(request):
        return web.Response(text="Unauthorized", status=401)

    ticket_id_str = request.match_info.get("id", "")
    try:
        ticket_id = int(ticket_id_str)
    except ValueError:
        return web.Response(text="Invalid ticket ID", status=400)

    ticket = await db.tickets.get(ticket_id)
    if not ticket:
        return web.Response(text="Ticket not found", status=404)

    # Получаем имя инженера отдельно
    eng_name = "—"
    if ticket["engineer_id"]:
        ename = await db.engineers.get_name(ticket["engineer_id"])
        if ename:
            eng_name = ename

    # Получаем только текстовые сообщения (без медиа)
    messages = await db.low_rated.chat_text_only(ticket_id)
    rating = await db.ratings.get_for_ticket(ticket_id)

    key_param = ""
    if MANAGER_DASHBOARD_KEY:
        key_param = f"?key={MANAGER_DASHBOARD_KEY}"

    stars = _stars_html(rating["rating"]) if rating else "—"
    rating_val = f'{rating["rating"]}/5' if rating else "—"
    comment = _html.escape(str(rating["comment"] or "—")) if rating else "—"
    eng = _html.escape(str(eng_name))
    client_name = _html.escape(str(ticket["client_name"] or "—"))
    company = _html.escape(str(ticket["company_city"] or "—"))
    machine = _html.escape(str(ticket["machine_info"] or "—"))
    problem = _html.escape(str(ticket["problem"] or "—"))
    contact = _html.escape(str(ticket["contact"] or "—"))

    # Информация о заявке
    info_html = (
        f'<dl class="ticket-info">'
        f"<dt>Статус</dt><dd>{_status_badge(ticket['status'])}</dd>"
        f"<dt>Оценка</dt><dd>{stars} ({rating_val})</dd>"
        f"<dt>Клиент</dt><dd>{client_name}</dd>"
        f"<dt>Инженер</dt><dd>{eng}</dd>"
        f"<dt>Компания / Город</dt><dd>{company}</dd>"
        f"<dt>Станок</dt><dd>{machine}</dd>"
        f"<dt>Проблема</dt><dd>{problem}</dd>"
        f"<dt>Контакты</dt><dd>{contact}</dd>"
        f"<dt>Комментарий клиента</dt><dd>{comment}</dd>"
        f"</dl>"
    )

    # Переписка (только текст)
    if messages:
        chat_parts = []
        total_msgs = len(messages)
        for msg in messages:
            role_class = "client" if msg["sender_role"] == "client" else "engineer"
            role_label = "👤 Клиент" if msg["sender_role"] == "client" else "👨‍🔧 Инженер"
            try:
                ts = msg["created_at"][:16].replace("T", " ")
            except (TypeError, IndexError):
                ts = ""
            text = _html.escape(msg["text"] or "")
            chat_parts.append(
                f'<div class="chat-msg {role_class}">'
                f'<div class="sender">{role_label} · {ts}</div>'
                f'<div class="text">{text}</div>'
                f"</div>"
            )
        chat_html = (
            f'<div class="card"><h2>Переписка ({total_msgs} текстовых сообщений)</h2>'
            f'<div class="media-note">📎 Медиафайлы (фото, видео, документы) '
            f"не отображаются в этом представлении.</div>"
            f'{"".join(chat_parts)}'
            f"</div>"
        )
    else:
        chat_html = (
            '<div class="card"><h2>Переписка</h2>'
            '<p style="color:#6b7280">Переписка пуста или содержит только медиафайлы.</p>'
            "</div>"
        )

    body = (
        f'<a class="back-link" href="/{key_param}">← Назад к дашборду</a>'
        f'<div class="header">'
        f"<h1>📋 Заявка #{ticket_id}</h1>"
        f'<span class="badge">Hotline Service</span>'
        f"</div>"
        f'<div class="card">{info_html}</div>'
        f"{chat_html}"
    )
    return web.Response(
        text=_base_page(f"Заявка #{ticket_id}", body),
        content_type="text/html",
    )


# ─── App factory ────────────────────────────────────────────────────────────


def create_app(db: Database) -> web.Application:
    """Создаёт aiohttp-приложение с дашбордом руководителя."""

    async def index(request: web.Request) -> web.Response:
        return await _index_handler(request, db)

    async def ticket_detail(request: web.Request) -> web.Response:
        return await _ticket_handler(request, db)

    async def health(request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    async def dashboard_data(request: web.Request) -> web.Response:
        """JSON-эндпоинт для живого дашборда. Защищён тем же MANAGER_DASHBOARD_KEY."""
        if not _check_auth(request):
            return web.Response(status=403, text="Forbidden")
        data = await db.get_dashboard_data(sla_minutes=SLA_MINUTES)
        return web.json_response(data)

    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/ticket/{id}", ticket_detail)
    app.router.add_get("/api/dashboard-data", dashboard_data)
    app.router.add_get("/health", health)

    return app
