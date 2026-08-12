"""
Фикстуры для тестирования Telegram-бота Hotline Service.

Содержит:
- Фейковые объекты User, Chat, Message, CallbackQuery
- Моки для Database (in-memory SQLite)
- Моки для Bot (aiogram)
- Фикстуры для FSM-состояний
"""
import datetime
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    Chat,
    Contact,
    Document,
    Message,
    PhotoSize,
    User,
    Video,
)

from bot.database import Database

# ===================== Базовые фикстуры пользователей =====================

@pytest.fixture
def fake_user() -> User:
    """Создаёт фейкового пользователя Telegram."""
    return User(
        id=123456789,
        is_bot=False,
        first_name="Иван",
        last_name="Петров",
        username="ivan_petrov",
        language_code="ru",
    )


@pytest.fixture
def fake_engineer_user() -> User:
    """Создаёт фейкового пользователя-инженера."""
    return User(
        id=987654321,
        is_bot=False,
        first_name="Сергей",
        last_name="Инженеров",
        username="sergey_engineer",
        language_code="ru",
    )


@pytest.fixture
def fake_admin_user() -> User:
    """Создаёт фейкового пользователя-администратора."""
    return User(
        id=7039700975,
        is_bot=False,
        first_name="Админ",
        last_name="Системы",
        username="admin_user",
        language_code="ru",
    )


@pytest.fixture
def fake_chat(fake_user: User) -> Chat:
    """Создаёт фейковый чат."""
    return Chat(
        id=fake_user.id,
        type="private",
        first_name=fake_user.first_name,
        last_name=fake_user.last_name,
        username=fake_user.username,
    )


# ===================== Фикстуры для Message =====================

def make_message(
    user: User,
    chat: Chat,
    text: str | None = None,
    caption: str | None = None,
    photo: list | None = None,
    video: Video | None = None,
    document: Document | None = None,
    contact: Contact | None = None,
    reply_to_message: Message | None = None,
    message_id: int = 1,
) -> Message:
    """Фабрика для создания фейковых сообщений.

    Использует model_construct для обхода валидации pydantic
    и object.__setattr__ для установки моков на frozen-модель.
    """
    msg = Message.model_construct(
        message_id=message_id,
        date=datetime.datetime.now(datetime.timezone.utc),
        chat=chat,
        from_user=user,
        text=text,
        caption=caption,
        photo=photo,
        video=video,
        document=document,
        contact=contact,
        reply_to_message=reply_to_message,
    )
    # Мокаем методы ответа (используем object.__setattr__ для frozen-модели)
    answer_msg = Message.model_construct(
        message_id=message_id + 100,
        date=datetime.datetime.now(datetime.timezone.utc),
        chat=chat,
        from_user=user,
    )
    # Моки на ответном сообщении (нужны для цепочек вида status_msg.edit_text())
    object.__setattr__(answer_msg, 'answer', AsyncMock(return_value=answer_msg))
    object.__setattr__(answer_msg, 'reply', AsyncMock(return_value=answer_msg))
    object.__setattr__(answer_msg, 'delete', AsyncMock())
    object.__setattr__(answer_msg, 'edit_text', AsyncMock())
    object.__setattr__(answer_msg, 'edit_caption', AsyncMock())

    object.__setattr__(msg, 'answer', AsyncMock(return_value=answer_msg))
    object.__setattr__(msg, 'reply', AsyncMock(return_value=msg))
    object.__setattr__(msg, 'delete', AsyncMock())
    object.__setattr__(msg, 'edit_text', AsyncMock())
    object.__setattr__(msg, 'edit_caption', AsyncMock())
    return msg


@pytest.fixture
def fake_message(fake_user: User, fake_chat: Chat) -> Message:
    """Создаёт фейковое текстовое сообщение."""
    return make_message(fake_user, fake_chat, text="Тестовое сообщение")


@pytest.fixture
def fake_photo_message(fake_user: User, fake_chat: Chat) -> Message:
    """Создаёт фейковое сообщение с фото."""
    photo = PhotoSize(
        file_id="test_photo_file_id",
        file_unique_id="test_unique_id",
        width=800,
        height=600,
        file_size=102400,
    )
    return make_message(
        fake_user, fake_chat,
        caption="Фото проблемы",
        photo=[photo],
    )


@pytest.fixture
def fake_contact_message(fake_user: User, fake_chat: Chat) -> Message:
    """Создаёт фейковое сообщение с контактом."""
    contact = Contact(
        phone_number="+79991234567",
        first_name="Иван",
        last_name="Петров",
        user_id=fake_user.id,
    )
    return make_message(fake_user, fake_chat, contact=contact)


# ===================== Фикстуры для CallbackQuery =====================

def make_callback_query(
    user: User,
    data: str,
    message: Message | None = None,
) -> CallbackQuery:
    """Фабрика для создания фейковых callback-запросов."""
    cb = CallbackQuery.model_construct(
        id=f"cb_{user.id}_{data[:20]}",
        from_user=user,
        chat_instance="test_chat_instance",
        data=data,
        message=message,
    )
    cb.answer = AsyncMock()
    return cb


@pytest.fixture
def fake_callback_query(fake_user: User, fake_message: Message) -> CallbackQuery:
    """Создаёт фейковый callback-запрос."""
    return make_callback_query(fake_user, "test_data", fake_message)


# ===================== Фикстуры для Database =====================

@pytest.fixture
async def db():
    """Создаёт in-memory SQLite базу данных для тестов."""
    database = Database(":memory:")
    await database.connect()
    await database.init_db()
    await database.migrate()
    yield database
    await database.close()


@pytest.fixture
async def db_with_engineer(db: Database, fake_engineer_user: User):
    """База данных с добавленным активным инженером."""
    await db.add_engineer(fake_engineer_user.id, f"{fake_engineer_user.first_name} {fake_engineer_user.last_name}")
    return db


@pytest.fixture
async def db_with_admin(db: Database, fake_admin_user: User):
    """База данных с добавленным администратором."""
    await db.add_admin(fake_admin_user.id)
    return db


@pytest.fixture
async def db_with_ticket(db: Database, fake_user: User):
    """База данных с созданной заявкой."""
    ticket_id = await db.create_ticket(
        client_id=fake_user.id,
        client_name=f"{fake_user.first_name} {fake_user.last_name}",
        company="ООО Тест",
        equipment_type="Лазерный станок",
        brand="Wattsan",
        cnc_model="1610",
        problem="Станок не включается, ошибка AL-01",
        media_id=None,
        city="Москва",
        inn_contract="7712345678",
        contact="+79991234567",
        machine_info="Wattsan 1610, Fanuc 0i-MF",
        company_city="Москва, ООО Тест",
    )
    return db, ticket_id


# ===================== Фикстуры для Bot =====================

@pytest.fixture
def mock_bot():
    """Создаёт мокированный объект Bot."""
    bot = AsyncMock()
    default_chat = Chat.model_construct(id=123456789, type="private")
    bot.send_message = AsyncMock(return_value=Message.model_construct(
        message_id=999,
        date=datetime.datetime.now(datetime.timezone.utc),
        chat=default_chat,
    ))
    bot.send_photo = AsyncMock(return_value=Message.model_construct(
        message_id=998,
        date=datetime.datetime.now(datetime.timezone.utc),
        chat=default_chat,
    ))
    bot.send_video = AsyncMock(return_value=Message.model_construct(
        message_id=997,
        date=datetime.datetime.now(datetime.timezone.utc),
        chat=default_chat,
    ))
    bot.send_document = AsyncMock(return_value=Message.model_construct(
        message_id=996,
        date=datetime.datetime.now(datetime.timezone.utc),
        chat=default_chat,
    ))
    bot.delete_message = AsyncMock()
    bot.get_file = AsyncMock()
    bot.download = AsyncMock()
    return bot


# ===================== Фикстуры для FSM =====================

@pytest.fixture
async def fsm_context():
    """Создаёт изолированный FSM-контекст с MemoryStorage."""
    storage = MemoryStorage()
    # Создаём контекст для конкретного чата с правильным StorageKey
    key = StorageKey(chat_id=123456789, user_id=123456789, bot_id=1)
    context = FSMContext(storage=storage, key=key)
    yield context
    await context.clear()


@pytest.fixture
async def fsm_storage():
    """Создаёт MemoryStorage для FSM."""
    return MemoryStorage()


# ===================== Фикстуры для мокирования внешних сервисов =====================

@pytest.fixture
def mock_bitrix():
    """Мокирует функции интеграции с Битрикс24."""
    with patch("bot.handlers.engineer.create_task", new_callable=AsyncMock) as mock_create_task, \
         patch("bot.handlers.engineer.upload_file_to_bitrix", new_callable=AsyncMock) as mock_upload, \
         patch("bot.handlers.engineer.send_message_to_chat", new_callable=AsyncMock) as mock_chat:
        mock_create_task.return_value = 12345
        mock_upload.return_value = 100
        mock_chat.return_value = True
        yield {
            "create_task": mock_create_task,
            "upload_file": mock_upload,
            "send_message_to_chat": mock_chat,
        }


@pytest.fixture
def mock_media_save():
    """Мокирует сохранение медиафайлов."""
    with patch("bot.handlers.client.save_media_file", new_callable=AsyncMock) as mock_save:
        mock_save.return_value = "media/ticket_1/001_photo.jpg"
        yield mock_save


@pytest.fixture
def mock_admin_ids():
    """Мокирует список администраторов из конфигурации."""
    with patch("bot.config.ADMIN_IDS", [7039700975]):
        yield [7039700975]


# ===================== Вспомогательные фикстуры =====================

@pytest.fixture
def sample_ticket_data():
    """Возвращает образец данных заявки для тестов."""
    return {
        "client_id": 123456789,
        "client_name": "Иван Петров",
        "company": "ООО Тест",
        "equipment_type": "Лазерный станок",
        "brand": "Wattsan",
        "cnc_model": "1610",
        "problem": "Станок не включается, ошибка AL-01",
        "media_id": None,
        "city": "Москва",
        "inn_contract": "7712345678",
        "contact": "+79991234567",
        "machine_info": "Wattsan 1610, Fanuc 0i-MF",
        "company_city": "Москва, ООО Тест",
    }


@pytest.fixture
def sample_ticket_row():
    """Возвращает образец строки заявки (как из БД) для тестов форматирования."""
    return {
        "id": 1,
        "client_id": 123456789,
        "client_name": "Иван Петров",
        "company": "ООО Тест",
        "equipment_type": "Лазерный станок",
        "brand": "Wattsan",
        "cnc_model": "1610",
        "machine_info": "Wattsan 1610",
        "company_city": "Москва",
        "problem": "Станок не включается",
        "media_id": None,
        "city": "Москва",
        "inn_contract": "7712345678",
        "contact": "+79991234567",
        "status": "open",
        "engineer_id": None,
        "close_comment": None,
        "created_at": "2024-01-15T10:00:00+00:00",
        "closed_at": None,
        "machine_media_id": None,
    }