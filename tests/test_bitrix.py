"""
Тесты для интеграции с Битрикс24 (bot/bitrix.py).

Покрывает:
- _make_deadline
- _get_disk_folder_id (кэширование, папка/хранилище/общий диск)
- upload_file_to_bitrix
- send_message_to_chat
- create_task
"""
import datetime
from unittest.mock import AsyncMock, mock_open, patch

import pytest

from bot import bitrix

# ===================== Моки для aiohttp =====================

class FakeResponse:
    """Фейковый HTTP-ответ с асинхронным .json()."""
    def __init__(self, data, ok=True, status=200):
        self.data = data
        self.ok = ok
        self.status = status

    async def json(self):
        return self.data


class FakeAsyncCM:
    """Фейковый async context manager, возвращаемый session.post()."""
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeSession:
    """Фейковый aiohttp.ClientSession, возвращающий последовательность ответов."""
    def __init__(self):
        self.responses = []
        self.post_calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def post(self, *args, **kwargs):
        self.post_calls.append((args, kwargs))
        if self.responses:
            response = self.responses.pop(0)
        else:
            response = FakeResponse({})
        return FakeAsyncCM(response)

    def add_response(self, data, ok=True, status=200):
        self.responses.append(FakeResponse(data, ok, status))


@pytest.fixture
def mock_session():
    """Мокирует aiohttp.ClientSession в модуле bitrix, возвращая FakeSession."""
    with patch("bot.bitrix.ClientSession") as MockSession:
        session = FakeSession()
        MockSession.return_value = session
        yield session


# ===================== Тесты _make_deadline =====================

class TestMakeDeadline:
    def test_no_hours_configured(self):
        with patch.object(bitrix, "BITRIX_TASK_DEADLINE_HOURS", ""):
            assert bitrix._make_deadline() is None

    def test_valid_hours(self):
        with patch.object(bitrix, "BITRIX_TASK_DEADLINE_HOURS", "2"):
            deadline = bitrix._make_deadline()
            assert deadline is not None
            dt = datetime.datetime.fromisoformat(deadline)
            delta = (dt - datetime.datetime.now(datetime.timezone.utc)).total_seconds()
            assert abs(delta - 7200) < 60

    def test_invalid_hours(self):
        with patch.object(bitrix, "BITRIX_TASK_DEADLINE_HOURS", "abc"):
            assert bitrix._make_deadline() is None


# ===================== Тесты _get_disk_folder_id =====================

class TestGetDiskFolderId:
    def setup_method(self):
        bitrix._disk_folder_id_cache = None

    async def test_no_webhook_url(self):
        with patch.object(bitrix, "BITRIX_WEBHOOK_URL", ""):
            assert await bitrix._get_disk_folder_id() is None

    async def test_cached_value(self, mock_session):
        bitrix._disk_folder_id_cache = 42
        with patch.object(bitrix, "BITRIX_WEBHOOK_URL", "https://crm.test/rest/1/token/"):
            assert await bitrix._get_disk_folder_id() == 42
            assert mock_session.post_calls == []

    async def test_folder_id_direct(self, mock_session):
        mock_session.add_response({"result": {"ID": 100}})
        with patch.object(bitrix, "BITRIX_WEBHOOK_URL", "https://crm.test/rest/1/token/"), \
             patch.object(bitrix, "BITRIX_DISK_FOLDER_ID", "100"):
            assert await bitrix._get_disk_folder_id() == 100

    async def test_storage_id_resolved(self, mock_session):
        # disk.folder.get — ошибка, disk.storage.get — успех
        mock_session.add_response({"error": "not found"})
        mock_session.add_response({"result": {"ROOT_OBJECT_ID": 500}})
        with patch.object(bitrix, "BITRIX_WEBHOOK_URL", "https://crm.test/rest/1/token/"), \
             patch.object(bitrix, "BITRIX_DISK_FOLDER_ID", "500"):
            assert await bitrix._get_disk_folder_id() == 500

    async def test_common_disk(self, mock_session):
        # BITRIX_DISK_FOLDER_ID пуст → только disk.storage.getlist
        mock_session.add_response({"result": [{"ROOT_OBJECT_ID": 700}]})
        with patch.object(bitrix, "BITRIX_WEBHOOK_URL", "https://crm.test/rest/1/token/"), \
             patch.object(bitrix, "BITRIX_DISK_FOLDER_ID", ""):
            assert await bitrix._get_disk_folder_id() == 700

    async def test_common_disk_fallback_after_failed_folder(self, mock_session):
        # BITRIX_DISK_FOLDER_ID задан, но не папка и не хранилище → getlist
        mock_session.add_response({"error": "not found"})  # disk.folder.get
        mock_session.add_response({"error": "not found"})  # disk.storage.get
        mock_session.add_response({"result": [{"ROOT_OBJECT_ID": 800}]})  # disk.storage.getlist
        with patch.object(bitrix, "BITRIX_WEBHOOK_URL", "https://crm.test/rest/1/token/"), \
             patch.object(bitrix, "BITRIX_DISK_FOLDER_ID", "500"):
            assert await bitrix._get_disk_folder_id() == 800

    async def test_invalid_folder_id_format(self, mock_session):
        with patch.object(bitrix, "BITRIX_WEBHOOK_URL", "https://crm.test/rest/1/token/"), \
             patch.object(bitrix, "BITRIX_DISK_FOLDER_ID", "abc"):
            assert await bitrix._get_disk_folder_id() is None


# ===================== Тесты upload_file_to_bitrix =====================

class TestUploadFileToBitrix:
    def setup_method(self):
        bitrix._disk_folder_id_cache = None

    async def test_no_webhook_url(self):
        with patch.object(bitrix, "BITRIX_WEBHOOK_URL", ""):
            assert await bitrix.upload_file_to_bitrix("some/path") is None

    async def test_file_not_found(self):
        with patch.object(bitrix, "BITRIX_WEBHOOK_URL", "https://crm.test/rest/1/token/"), \
             patch("os.path.isfile", return_value=False):
            assert await bitrix.upload_file_to_bitrix("nonexistent.txt") is None

    async def test_no_folder_id(self, mock_session):
        bitrix._disk_folder_id_cache = None
        with patch.object(bitrix, "BITRIX_WEBHOOK_URL", "https://crm.test/rest/1/token/"), \
             patch("os.path.isfile", return_value=True), \
             patch.object(bitrix, "_get_disk_folder_id", new=AsyncMock(return_value=None)):
            assert await bitrix.upload_file_to_bitrix("file.txt") is None

    async def test_upload_success(self, mock_session):
        bitrix._disk_folder_id_cache = 10
        # Шаг 1: получаем uploadUrl
        mock_session.add_response({"result": {"uploadUrl": "https://upload.test/", "field": "file"}})
        # Шаг 2: загружаем файл
        mock_session.add_response({"result": {"ID": 55}})

        with patch.object(bitrix, "BITRIX_WEBHOOK_URL", "https://crm.test/rest/1/token/"), \
             patch("os.path.isfile", return_value=True), \
             patch("builtins.open", mock_open(read_data=b"data")):
            file_id = await bitrix.upload_file_to_bitrix("file.txt")
            assert file_id == 55

    async def test_upload_error(self, mock_session):
        bitrix._disk_folder_id_cache = 10
        mock_session.add_response({"error": "upload error"})
        with patch.object(bitrix, "BITRIX_WEBHOOK_URL", "https://crm.test/rest/1/token/"), \
             patch("os.path.isfile", return_value=True):
            assert await bitrix.upload_file_to_bitrix("file.txt") is None


# ===================== Тесты send_message_to_chat =====================

class TestSendMessageToChat:
    async def test_no_chat_id(self):
        with patch.object(bitrix, "BITRIX_CHAT_ID", ""), \
             patch.object(bitrix, "BITRIX_WEBHOOK_URL", "https://crm.test/rest/1/token/"):
            assert await bitrix.send_message_to_chat("hello") is False

    async def test_no_webhook_url(self):
        with patch.object(bitrix, "BITRIX_CHAT_ID", "123"), \
             patch.object(bitrix, "BITRIX_WEBHOOK_URL", ""):
            assert await bitrix.send_message_to_chat("hello") is False

    async def test_chat_id_numeric(self, mock_session):
        mock_session.add_response({"result": True})
        with patch.object(bitrix, "BITRIX_CHAT_ID", "123"), \
             patch.object(bitrix, "BITRIX_WEBHOOK_URL", "https://crm.test/rest/1/token/"):
            assert await bitrix.send_message_to_chat("hello") is True
            call_kwargs = mock_session.post_calls[0][1]
            assert call_kwargs["json"]["DIALOG_ID"] == "chat123"

    async def test_chat_id_with_chat_prefix(self, mock_session):
        mock_session.add_response({"result": True})
        with patch.object(bitrix, "BITRIX_CHAT_ID", "chat999"), \
             patch.object(bitrix, "BITRIX_WEBHOOK_URL", "https://crm.test/rest/1/token/"):
            assert await bitrix.send_message_to_chat("hello") is True
            call_kwargs = mock_session.post_calls[0][1]
            assert call_kwargs["json"]["DIALOG_ID"] == "chat999"

    async def test_invalid_chat_id(self):
        with patch.object(bitrix, "BITRIX_CHAT_ID", "abc"), \
             patch.object(bitrix, "BITRIX_WEBHOOK_URL", "https://crm.test/rest/1/token/"):
            assert await bitrix.send_message_to_chat("hello") is False

    async def test_error_response(self, mock_session):
        mock_session.add_response({"error": "some error"})
        with patch.object(bitrix, "BITRIX_CHAT_ID", "123"), \
             patch.object(bitrix, "BITRIX_WEBHOOK_URL", "https://crm.test/rest/1/token/"):
            assert await bitrix.send_message_to_chat("hello") is False

    async def test_http_error(self, mock_session):
        mock_session.add_response({}, ok=False, status=500)
        with patch.object(bitrix, "BITRIX_CHAT_ID", "123"), \
             patch.object(bitrix, "BITRIX_WEBHOOK_URL", "https://crm.test/rest/1/token/"):
            assert await bitrix.send_message_to_chat("hello") is False

    async def test_payload_uses_dialog_id_only(self, mock_session):
        """Сообщение идёт в чат через DIALOG_ID; отправитель (USER_ID/FROM_USER_ID) не передаётся.

        USER_ID у im.message.add означает получателя (личный диалог), а не отправителя,
        поэтому его передавать нельзя — иначе сообщение уйдёт в личку вместо чата.
        """
        mock_session.add_response({"result": True})
        with patch.object(bitrix, "BITRIX_CHAT_ID", "chat123"), \
             patch.object(bitrix, "BITRIX_WEBHOOK_URL", "https://crm.test/rest/1/token/"):
            assert await bitrix.send_message_to_chat("hello") is True
            payload = mock_session.post_calls[0][1]["json"]
            assert payload["DIALOG_ID"] == "chat123"
            assert "USER_ID" not in payload
            assert "FROM_USER_ID" not in payload


# ===================== Тесты create_task =====================

class TestCreateTask:
    async def test_no_webhook_url(self):
        with patch.object(bitrix, "BITRIX_WEBHOOK_URL", ""):
            assert await bitrix.create_task("Title", "Desc", 123) is None

    async def test_success(self, mock_session):
        mock_session.add_response({"result": {"task": {"id": 777}}})
        with patch.object(bitrix, "BITRIX_WEBHOOK_URL", "https://crm.test/rest/1/token/"), \
             patch.object(bitrix, "BITRIX_TASK_PRIORITY", "2"), \
             patch.object(bitrix, "BITRIX_TASK_DEADLINE_HOURS", ""):
            task_id = await bitrix.create_task("Заголовок", "Описание", 123)
            assert task_id == 777

    async def test_creator_is_always_414(self, mock_session):
        """Постановщик задачи всегда 414 (бизнес-требование), независимо от BITRIX_CREATED_BY."""
        mock_session.add_response({"result": {"task": {"id": 778}}})
        # Пробуем передать другой created_by и задать конфигурацию — должно быть проигнорировано
        with patch.object(bitrix, "BITRIX_WEBHOOK_URL", "https://crm.test/rest/1/token/"), \
             patch.object(bitrix, "BITRIX_TASK_PRIORITY", "3"), \
             patch.object(bitrix, "BITRIX_TASK_DEADLINE_HOURS", ""):
            await bitrix.create_task("Заголовок", "Описание", 123, created_by=999)

        # Проверяем payload последнего запроса — CREATED_BY всегда 414
        post_calls = mock_session.post_calls
        create_call = next(
            (kwargs for args, kwargs in post_calls if "tasks.task.add" in str(args[0])),
            None
        )
        assert create_call is not None
        fields = create_call["json"]["fields"]
        assert fields["CREATED_BY"] == 414
        # Ответственный должен совпадать с переданным
        assert fields["RESPONSIBLE_ID"] == 123
        # Приоритет из конфигурации
        assert fields["PRIORITY"] == "3"

    async def test_error_response(self, mock_session):
        mock_session.add_response({"error": "permission denied"})
        with patch.object(bitrix, "BITRIX_WEBHOOK_URL", "https://crm.test/rest/1/token/"):
            assert await bitrix.create_task("Title", "Desc", 123) is None

    async def test_http_error(self, mock_session):
        mock_session.add_response({}, ok=False, status=403)
        with patch.object(bitrix, "BITRIX_WEBHOOK_URL", "https://crm.test/rest/1/token/"):
            assert await bitrix.create_task("Title", "Desc", 123) is None

    async def test_no_task_id_in_response(self, mock_session):
        mock_session.add_response({"result": {"task": {}}})
        with patch.object(bitrix, "BITRIX_WEBHOOK_URL", "https://crm.test/rest/1/token/"):
            assert await bitrix.create_task("Title", "Desc", 123) is None

    async def test_exception_handled(self, mock_session):
        # Симулируем исключение: делаем post, который бросает исключение
        with patch.object(bitrix, "BITRIX_WEBHOOK_URL", "https://crm.test/rest/1/token/"):
            # Патчим ClientSession, чтобы post бросал исключение
            with patch("bot.bitrix.ClientSession") as MockSession:
                session = FakeSession()
                def raise_error(*args, **kwargs):
                    raise Exception("network error")
                session.post = raise_error
                MockSession.return_value = session
                assert await bitrix.create_task("Title", "Desc", 123) is None
