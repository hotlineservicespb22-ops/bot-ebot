"""Тесты для нормализации городов в статистике дашборда (get_dashboard_data)."""
import pytest

from bot.database import Database


async def _ticket_with_city(db: Database, city: str) -> int:
    return await db.create_ticket(
        client_id=1, client_name="Клиент", company="", equipment_type="",
        brand="", cnc_model="", problem="Проблема", media_id=None, city="",
        inn_contract="", contact="12345", company_city=city,
    )


@pytest.mark.asyncio
async def test_city_variants_collapse_to_canonical_name(db: Database):
    """Разные написания одного города («вольск», «г.вольск», «ГВольск») схлопываются."""
    await _ticket_with_city(db, "вольск")
    await _ticket_with_city(db, "г.Вольск")
    await _ticket_with_city(db, "Ооо ЮСТ гВольск")

    data = await db.get_dashboard_data(sla_minutes=15)
    cities = {c["city"]: c["cnt"] for c in data["cities"]}
    assert cities.get("Вольск") == 3


@pytest.mark.asyncio
async def test_city_aliases_collapse_spb_variants(db: Database):
    """Разговорные варианты («спб», «питер») сводятся к каноничному имени."""
    await _ticket_with_city(db, "спб")
    await _ticket_with_city(db, "Питер")
    await _ticket_with_city(db, "Санкт-Петербург")

    data = await db.get_dashboard_data(sla_minutes=15)
    cities = {c["city"]: c["cnt"] for c in data["cities"]}
    assert cities.get("Санкт-Петербург") == 3


@pytest.mark.asyncio
async def test_unrecognized_city_kept_as_is(db: Database):
    """Текст, не похожий ни на один известный город, остаётся без изменений."""
    await _ticket_with_city(db, "Ромашка 771234567")

    data = await db.get_dashboard_data(sla_minutes=15)
    cities = {c["city"]: c["cnt"] for c in data["cities"]}
    assert cities.get("Ромашка 771234567") == 1
