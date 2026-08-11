import asyncio
import pytest
from bot.database import Database


@pytest.fixture
async def db():
    database = Database(":memory:")
    await database.connect()
    await database.init_db()
    await database.migrate()
    yield database
    await database.close()


@pytest.mark.asyncio
async def test_create_and_get_ticket(db):
    ticket_id = await db.create_ticket(
        client_id=123,
        client_name="Тест Клиент",
        company="",
        equipment_type="",
        brand="",
        cnc_model="",
        problem="Станок не включается",
        media_id=None,
        city="",
        inn_contract="",
        contact="+79990000000",
        machine_info="Wattsan 1610",
        company_city="Москва"
    )
    assert ticket_id > 0

    ticket = await db.get_ticket(ticket_id)
    assert ticket is not None
    assert ticket['client_id'] == 123
    assert ticket['status'] == 'open'
    assert ticket['created_at'] is not None


@pytest.mark.asyncio
async def test_take_ticket(db):
    ticket_id = await db.create_ticket(
        client_id=1,
        client_name="Клиент",
        company="",
        equipment_type="",
        brand="",
        cnc_model="",
        problem="Проблема",
        media_id=None,
        city="",
        inn_contract="",
        contact="12345"
    )

    # Добавляем инженера
    await db.add_engineer(999, "Инженер Тест")

    success = await db.take_ticket(ticket_id, 999)
    assert success is True

    # Повторное взятие должно быть неуспешным
    success2 = await db.take_ticket(ticket_id, 888)
    assert success2 is False

    ticket = await db.get_ticket(ticket_id)
    assert ticket['status'] == 'in_progress'
    assert ticket['engineer_id'] == 999


@pytest.mark.asyncio
async def test_close_ticket(db):
    ticket_id = await db.create_ticket(
        client_id=1,
        client_name="Клиент",
        company="",
        equipment_type="",
        brand="",
        cnc_model="",
        problem="Проблема",
        media_id=None,
        city="",
        inn_contract="",
        contact="12345"
    )

    await db.close_ticket(ticket_id, status='completed', comment='Готово')
    ticket = await db.get_ticket(ticket_id)
    assert ticket['status'] == 'completed'
    assert ticket['close_comment'] == 'Готово'
    assert ticket['closed_at'] is not None


@pytest.mark.asyncio
async def test_rating(db):
    ticket_id = await db.create_ticket(
        client_id=1,
        client_name="Клиент",
        company="",
        equipment_type="",
        brand="",
        cnc_model="",
        problem="Проблема",
        media_id=None,
        city="",
        inn_contract="",
        contact="12345"
    )

    await db.save_rating(ticket_id, client_id=1, rating=5)
    rating = await db.get_rating_for_ticket(ticket_id)
    assert rating is not None
    assert rating['rating'] == 5

    avg = await db.get_avg_rating()
    assert avg == 5.0


@pytest.mark.asyncio
async def test_messages(db):
    ticket_id = await db.create_ticket(
        client_id=1,
        client_name="Клиент",
        company="",
        equipment_type="",
        brand="",
        cnc_model="",
        problem="Проблема",
        media_id=None,
        city="",
        inn_contract="",
        contact="12345"
    )

    await db.save_message(ticket_id, sender_id=1, sender_role='client', text='Привет')
    await db.save_message(ticket_id, sender_id=2, sender_role='engineer', text='Здравствуйте')

    messages = await db.get_messages_for_ticket(ticket_id)
    assert len(messages) == 2
    assert messages[0]['text'] == 'Привет'


@pytest.mark.asyncio
async def test_create_ticket_with_machine_media(db):
    """Проверяет сохранение и возврат machine_media_id (фото шильдика)."""
    ticket_id = await db.create_ticket(
        client_id=123,
        client_name="Тест Клиент",
        company="",
        equipment_type="",
        brand="",
        cnc_model="",
        problem="Станок не включается",
        media_id="photo_problem_123",
        city="",
        inn_contract="",
        contact="+79990000000",
        machine_info="Wattsan 1610",
        company_city="Москва",
        machine_media_id="photo_shield_456"
    )
    assert ticket_id > 0

    ticket = await db.get_ticket(ticket_id)
    assert ticket is not None
    assert ticket['machine_media_id'] == "photo_shield_456"
    assert ticket['media_id'] == "photo_problem_123"


@pytest.mark.asyncio
async def test_engineer_active_toggle(db):
    """Проверяет управление дежурными инженерами (вкл/выкл)."""
    await db.add_engineer(111, "Инженер 1")
    await db.add_engineer(222, "Инженер 2")
    await db.add_engineer(333, "Инженер 3")

    # Все инженеры активны по умолчанию
    all_eng = await db.get_all_engineers()
    assert len(all_eng) == 3
    assert all(e['is_active'] == 1 for e in all_eng)

    # Выключаем инженера 222
    await db.set_engineer_active(222, 0)

    # Активные — только 111 и 333
    active = await db.get_engineers()
    active_ids = [e['user_id'] for e in active]
    assert len(active) == 2
    assert 111 in active_ids
    assert 333 in active_ids
    assert 222 not in active_ids

    # Все инженеры — 3 (включая неактивного)
    all_eng = await db.get_all_engineers()
    assert len(all_eng) == 3

    # Снова включаем 222
    await db.set_engineer_active(222, 1)
    active = await db.get_engineers()
    assert len(active) == 3


@pytest.mark.asyncio
async def test_engineer_stats(db):
    # Без инженеров статистика пуста или не падает
    stats = await db.get_engineer_stats()
    assert isinstance(stats, list)
