"""
Стресс-тест всех систем бота.

Проверяет:
1. Конкурентное создание заявок (параллельные INSERT).
2. Гонку при взятии заявки несколькими инженерами (race condition).
3. Параллельное сохранение сообщений.
4. Оценки и статистику.
5. Отсутствие ошибок/исключений под нагрузкой.

Использует временную БД (не трогает боевую hotline_service.db).
"""
import asyncio
import os
import tempfile
import time

from bot.database import Database


async def stress_create_tickets(db: Database, count: int = 100) -> float:
    """Создаёт count заявок конкурентно. Возвращает время выполнения."""
    start = time.monotonic()

    async def create_one(i: int):
        return await db.create_ticket(
            client_id=1000 + i,
            client_name=f"Клиент {i}",
            company="",
            equipment_type="",
            brand="",
            cnc_model="",
            problem=f"Проблема {i}: станок не включается",
            media_id=None,
            city="",
            inn_contract="",
            contact=f"+7999{i:06d}",
            machine_info="Wattsan 1610",
            company_city="Москва",
        )

    results = await asyncio.gather(*[create_one(i) for i in range(count)])
    elapsed = time.monotonic() - start
    assert len(results) == count
    assert len(set(results)) == count, "Обнаружены дубликаты ID заявок!"
    return elapsed


async def stress_take_ticket(db: Database, ticket_id: int, engineers: list) -> dict:
    """Пытается взять одну заявку несколькими инженерами одновременно. Проверяет гонку."""
    start = time.monotonic()

    async def try_take(eng_id: int):
        return eng_id, await db.take_ticket(ticket_id, eng_id)

    results = await asyncio.gather(*[try_take(e) for e in engineers])
    elapsed = time.monotonic() - start

    # Должен выиграть ровно один инженер
    winners = [eng for eng, ok in results if ok]
    assert len(winners) == 1, f"Гонка: победителей {len(winners)}, ожидался 1"
    return {"winner": winners[0], "elapsed": elapsed, "attempts": len(engineers)}


async def stress_messages(db: Database, ticket_id: int, count: int = 200) -> float:
    """Сохраняет count сообщений конкурентно. Возвращает время."""
    start = time.monotonic()

    async def save_one(i: int):
        role = 'client' if i % 2 == 0 else 'engineer'
        await db.save_message(ticket_id, sender_id=1000 + i, sender_role=role, text=f"Сообщение {i}")

    await asyncio.gather(*[save_one(i) for i in range(count)])
    elapsed = time.monotonic() - start

    messages = await db.get_messages_for_ticket(ticket_id)
    assert len(messages) == count, f"Сохранено {len(messages)} сообщений, ожидалось {count}"
    return elapsed


async def stress_ratings(db: Database, tickets: list) -> float:
    """Сохраняет оценки для нескольких заявок."""
    start = time.monotonic()

    async def rate_one(ticket_id: int):
        await db.save_rating(ticket_id, client_id=1000, rating=(ticket_id % 5) + 1, comment="Тест")

    await asyncio.gather(*[rate_one(t) for t in tickets])
    elapsed = time.monotonic() - start

    avg = await db.get_avg_rating()
    assert avg is not None, "Средняя оценка не рассчитана"
    return elapsed


async def stress_stats(db: Database) -> float:
    """Выполняет запросы статистики под нагрузкой."""
    start = time.monotonic()

    # Несколько статистических запросов параллельно
    await asyncio.gather(
        db.get_all_tickets(),
        db.get_open_tickets(),
        db.get_expired_open_tickets(300),
        db.get_engineer_stats(),
        db.get_avg_resolution_time(),
        db.get_tickets_by_city(),
        db.get_avg_rating(),
    )
    elapsed = time.monotonic() - start
    return elapsed


async def main():
    print("=" * 60)
    print("СТРЕСС-ТЕСТ СИСТЕМ БОТА")
    print("=" * 60)

    # Временная БД
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "stress_test.db")
    db = Database(db_path)
    await db.connect()
    await db.init_db()
    await db.migrate()

    try:
        # 1. Массовое создание заявок
        print("\n[1] Создание 100 заявок конкурентно...")
        t = await stress_create_tickets(db, 100)
        print(f"    ✅ 100 заявок создано за {t:.3f} сек")

        # 2. Гонка при взятии заявки
        print("\n[2] Гонка при взятии одной заявки (50 инженеров)...")
        # Создаём новую заявку для теста гонки
        race_ticket_id = await db.create_ticket(
            client_id=9999, client_name="Гонка", company="", equipment_type="",
            brand="", cnc_model="", problem="Гонка", media_id=None, city="",
            inn_contract="", contact="+79990000000", machine_info="", company_city=""
        )
        engineers = [f"eng_{i}" for i in range(50)]
        # Добавляем инженеров в БД
        for i, e in enumerate(engineers):
            await db.add_engineer(5000 + i, f"Инженер {i}")
        result = await stress_take_ticket(db, race_ticket_id, [5000 + i for i in range(50)])
        print(f"    ✅ Гонка корректна: победил {result['winner']} за {result['elapsed']:.3f} сек "
              f"({result['attempts']} попыток)")

        # 3. Массовая переписка
        print("\n[3] Сохранение 200 сообщений конкурентно...")
        t = await stress_messages(db, race_ticket_id, 200)
        print(f"    ✅ 200 сообщений сохранено за {t:.3f} сек")

        # 4. Оценки
        print("\n[4] Оценки для 50 заявок...")
        tickets = await db.get_all_tickets()
        ticket_ids = [t['id'] for t in tickets[:50]]
        t = await stress_ratings(db, ticket_ids)
        print(f"    ✅ {len(ticket_ids)} оценок сохранено за {t:.3f} сек")

        # 5. Статистика под нагрузкой
        print("\n[5] Запросы статистики под нагрузкой...")
        t = await stress_stats(db)
        print(f"    ✅ Статистика рассчитана за {t:.3f} сек")

        # 6. Проверка целостности
        print("\n[6] Проверка целостности данных...")
        all_tickets = await db.get_all_tickets()
        open_tickets = await db.get_open_tickets()
        print(f"    ✅ Всего заявок: {len(all_tickets)}")
        print(f"    ✅ Открытых (без инженера): {len(open_tickets)}")
        print(f"    ✅ В работе: {sum(1 for t in all_tickets if t['status'] == 'in_progress')}")
        print(f"    ✅ Оценок: {len(await db.get_rating_for_ticket(ticket_ids[0])) and 'есть'}")

        print("\n" + "=" * 60)
        print("✅ ВСЕ СТРЕСС-ТЕСТЫ ПРОЙДЕНЫ УСПЕШНО")
        print("=" * 60)

    except AssertionError as e:
        print(f"\n❌ СТРЕСС-ТЕСТ ПРОВАЛЕН: {e}")
    except Exception as e:
        print(f"\n❌ ИСКЛЮЧЕНИЕ В СТРЕСС-ТЕСТЕ: {type(e).__name__}: {e}")
    finally:
        await db.close()
        # Удаляем временную БД
        try:
            os.remove(db_path)
        except OSError:
            pass


if __name__ == "__main__":
    asyncio.run(main())