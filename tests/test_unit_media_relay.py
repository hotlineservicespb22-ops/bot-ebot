"""
Unit-тесты для модулей media.py и утилитарных функций relay.py.

Проверяет:
- sanitize_filename (очистка имён файлов от недопустимых символов)
- get_ticket_dir / ensure_ticket_dir
- next_file_number
- _guess_extension
- get_file_id_and_size
- format_ticket_detail
- build_list_text
- format_ticket_history
"""
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

from bot.handlers.relay import (
    MAX_FILE_SIZE,
    build_list_text,
    format_ticket_detail,
    format_ticket_history,
    get_file_id_and_size,
)
from bot.media import (
    MAX_NAME_LENGTH,
    _guess_extension,
    ensure_ticket_dir,
    get_ticket_dir,
    next_file_number,
    sanitize_filename,
    save_media_file,
)


class TestSanitizeFilename:
    """Тесты для sanitize_filename."""

    def test_normal_filename(self):
        """Обычное имя файла не изменяется."""
        assert sanitize_filename("photo.jpg") == "photo.jpg"

    def test_filename_with_forbidden_chars(self):
        """Недопустимые символы заменяются на '_'."""
        result = sanitize_filename('file\\/*?:"<>|name.txt')
        assert "\\" not in result
        assert "/" not in result
        assert "*" not in result
        assert "?" not in result
        assert ":" not in result
        assert '"' not in result
        assert "<" not in result
        assert ">" not in result
        assert "|" not in result

    def test_empty_filename(self):
        """Пустое имя файла заменяется на 'file'."""
        assert sanitize_filename("") == "file"
        assert sanitize_filename("   ") == "file"

    def test_long_filename_truncated(self):
        """Длинные имена файлов обрезаются."""
        long_name = "a" * 200 + ".jpg"
        result = sanitize_filename(long_name)
        assert len(result) <= MAX_NAME_LENGTH

    def test_filename_preserves_extension(self):
        """Расширение сохраняется при обрезке."""
        long_name = "a" * 200 + ".png"
        result = sanitize_filename(long_name)
        assert result.endswith(".png")

    def test_sql_injection_in_filename(self):
        """SQL-инъекция в имени файла обезвреживается."""
        result = sanitize_filename("'; DROP TABLE users; --.jpg")
        # Символы SQL не являются недопустимыми для ФС, но файл создаётся безопасно
        assert result is not None
        assert len(result) > 0

    def test_xss_in_filename(self):
        """XSS-символы в имени файла заменяются."""
        result = sanitize_filename("<script>alert(1)</script>.jpg")
        assert "<" not in result
        assert ">" not in result


class TestTicketDir:
    """Тесты для функций работы с папками заявок."""

    def test_get_ticket_dir(self):
        """Проверяет формирование пути к папке заявки."""
        with patch("bot.media.MEDIA_DIR", "media"):
            result = get_ticket_dir(42)
            assert result == os.path.join("media", "ticket_42")

    def test_ensure_ticket_dir_creates_directory(self):
        """Проверяет создание папки заявки."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("bot.media.MEDIA_DIR", tmpdir):
                ticket_dir = ensure_ticket_dir(999)
                assert os.path.isdir(ticket_dir)
                assert "ticket_999" in ticket_dir

    def test_next_file_number_empty_dir(self):
        """Для пустой папки возвращает 1."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("bot.media.MEDIA_DIR", tmpdir):
                assert next_file_number(1) == 1

    def test_next_file_number_with_existing_files(self):
        """Возвращает следующий номер после максимального."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("bot.media.MEDIA_DIR", tmpdir):
                ticket_dir = os.path.join(tmpdir, "ticket_1")
                os.makedirs(ticket_dir)
                # Создаём файлы
                for name in ["001_photo.jpg", "002_video.mp4", "003_doc.pdf"]:
                    open(os.path.join(ticket_dir, name), "w").close()
                assert next_file_number(1) == 4

    def test_next_file_number_ignores_non_matching_files(self):
        """Игнорирует файлы, не соответствующие шаблону NNN_*."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("bot.media.MEDIA_DIR", tmpdir):
                ticket_dir = os.path.join(tmpdir, "ticket_1")
                os.makedirs(ticket_dir)
                open(os.path.join(ticket_dir, "readme.txt"), "w").close()
                open(os.path.join(ticket_dir, "abc_file.jpg"), "w").close()
                assert next_file_number(1) == 1


class TestGuessExtension:
    """Тесты для _guess_extension."""

    def test_extension_from_filename(self):
        """Определяет расширение из имени файла."""
        assert _guess_extension("photo.jpg", None, "photo") == ".jpg"
        assert _guess_extension("video.MP4", None, "video") == ".mp4"

    def test_extension_from_mime_type(self):
        """Определяет расширение из MIME-типа."""
        result = _guess_extension(None, "image/jpeg", "photo")
        assert result in (".jpg", ".jpeg", ".jpe")

    def test_extension_from_media_type(self):
        """Определяет расширение из типа медиа."""
        assert _guess_extension(None, None, "photo") == ".jpg"
        assert _guess_extension(None, None, "video") == ".mp4"
        assert _guess_extension(None, None, "voice") == ".ogg"

    def test_unknown_media_type(self):
        """Для неизвестного типа возвращает пустую строку."""
        assert _guess_extension(None, None, "unknown") == ""


class TestGetFileIdAndSize:
    """Тесты для get_file_id_and_size."""

    def _make_message_with_media(self, media_type: str):
        """Создаёт мок сообщения с медиа."""
        msg = MagicMock()
        msg.photo = None
        msg.video = None
        msg.document = None
        msg.voice = None
        msg.audio = None
        msg.animation = None
        msg.sticker = None
        msg.location = None
        msg.contact = None

        if media_type == "photo":
            photo = MagicMock()
            photo.file_id = "photo_id"
            photo.file_size = 1000
            msg.photo = [photo]
        elif media_type == "video":
            msg.video = MagicMock(file_id="video_id", file_size=2000)
        elif media_type == "document":
            msg.document = MagicMock(file_id="doc_id", file_size=3000)
        elif media_type == "voice":
            msg.voice = MagicMock(file_id="voice_id", file_size=4000)
        elif media_type == "audio":
            msg.audio = MagicMock(file_id="audio_id", file_size=5000)
        elif media_type == "animation":
            msg.animation = MagicMock(file_id="anim_id", file_size=6000)
        elif media_type == "sticker":
            msg.sticker = MagicMock(file_id="sticker_id", file_size=7000)
        elif media_type == "location":
            msg.location = MagicMock(latitude=55.75, longitude=37.61)
        elif media_type == "contact":
            msg.contact = MagicMock(phone_number="+79991234567", first_name="Иван")
        return msg

    def test_photo(self):
        """Извлекает file_id и размер фото."""
        msg = self._make_message_with_media("photo")
        method, file_id, size = get_file_id_and_size(msg)
        assert method == "send_photo"
        assert file_id == "photo_id"
        assert size == 1000

    def test_video(self):
        """Извлекает file_id и размер видео."""
        msg = self._make_message_with_media("video")
        method, file_id, size = get_file_id_and_size(msg)
        assert method == "send_video"
        assert file_id == "video_id"
        assert size == 2000

    def test_document(self):
        """Извлекает file_id и размер документа."""
        msg = self._make_message_with_media("document")
        method, file_id, size = get_file_id_and_size(msg)
        assert method == "send_document"
        assert file_id == "doc_id"
        assert size == 3000

    def test_location(self):
        """Извлекает геолокацию."""
        msg = self._make_message_with_media("location")
        method, file_id, size = get_file_id_and_size(msg)
        assert method == "send_location"
        assert file_id.latitude == 55.75
        assert size is None

    def test_contact(self):
        """Извлекает контакт."""
        msg = self._make_message_with_media("contact")
        method, file_id, size = get_file_id_and_size(msg)
        assert method == "send_contact"
        assert file_id.phone_number == "+79991234567"

    def test_no_media(self):
        """Для сообщения без медиа возвращает None."""
        msg = self._make_message_with_media("none")
        method, file_id, size = get_file_id_and_size(msg)
        assert method is None
        assert file_id is None
        assert size is None

    def test_max_file_size_constant(self):
        """Проверяет константу максимального размера файла."""
        assert MAX_FILE_SIZE == 20 * 1024 * 1024  # 20 MB


class TestFormatTicketDetail:
    """Тесты для format_ticket_detail."""

    def test_basic_formatting(self, sample_ticket_row):
        """Проверяет базовое форматирование заявки."""
        result = format_ticket_detail(sample_ticket_row)
        assert "Заявка #1" in result
        assert "Москва" in result
        assert "Станок не включается" in result
        assert "🟡 Нераспределена" in result  # status = 'open'

    def test_status_in_progress(self, sample_ticket_row):
        """Проверяет отображение статуса 'in_progress'."""
        sample_ticket_row["status"] = "in_progress"
        result = format_ticket_detail(sample_ticket_row)
        assert "🔵 В работе" in result

    def test_status_completed(self, sample_ticket_row):
        """Проверяет отображение статуса 'completed'."""
        sample_ticket_row["status"] = "completed"
        result = format_ticket_detail(sample_ticket_row)
        assert "✅ Завершена" in result

    def test_status_canceled(self, sample_ticket_row):
        """Проверяет отображение статуса 'canceled'."""
        sample_ticket_row["status"] = "canceled"
        result = format_ticket_detail(sample_ticket_row)
        assert "🚫 Отменена" in result

    def test_none_fields_replaced_with_dash(self, sample_ticket_row):
        """Проверяет замену None на '—'."""
        sample_ticket_row["company_city"] = None
        sample_ticket_row["machine_info"] = None
        sample_ticket_row["problem"] = None
        sample_ticket_row["contact"] = None
        sample_ticket_row["client_name"] = None
        result = format_ticket_detail(sample_ticket_row)
        assert result.count("—") >= 5

    def test_html_escaping(self, sample_ticket_row):
        """Проверяет экранирование HTML в данных заявки."""
        sample_ticket_row["problem"] = "<script>alert('xss')</script>"
        result = format_ticket_detail(sample_ticket_row)
        # HTML-теги должны быть экранированы (заменены на HTML-сущности)
        assert "<script>" not in result  # Исходный тег не должен присутствовать
        # Проверяем наличие экранированной версии
        assert "script" in result  # Слово script присутствует, но экранировано

    def test_sql_injection_safe(self, sample_ticket_row):
        """SQL-инъекция в данных не влияет на форматирование."""
        sample_ticket_row["problem"] = "'; DROP TABLE tickets; --"
        result = format_ticket_detail(sample_ticket_row)
        assert "DROP TABLE" in result  # Текст присутствует как данные


class TestBuildListText:
    """Тесты для build_list_text."""

    def test_mine_mode_header(self):
        """Проверяет заголовок для режима 'mine'."""
        tickets = [{"id": 1, "company_city": "Москва", "problem": "Проблема"}]
        result = build_list_text(tickets, "mine")
        assert "Ваши активные заявки" in result

    def test_open_mode_header(self):
        """Проверяет заголовок для режима 'open'."""
        tickets = [{"id": 1, "company_city": "Москва", "problem": "Проблема"}]
        result = build_list_text(tickets, "open")
        assert "Нераспределенные заявки" in result

    def test_empty_list(self):
        """Проверяет пустой список."""
        result = build_list_text([], "mine")
        assert "Заявка #" not in result
        assert "Выберите заявку для просмотра" in result

    def test_multiple_tickets(self):
        """Проверяет список из нескольких заявок."""
        tickets = [
            {"id": 1, "company_city": "Москва", "problem": "Проблема 1"},
            {"id": 2, "company_city": "Казань", "problem": "Проблема 2"},
        ]
        result = build_list_text(tickets, "mine")
        assert "Заявка #1" in result
        assert "Заявка #2" in result

    def test_html_escaping_in_list(self):
        """Проверяет экранирование HTML в списке."""
        tickets = [{"id": 1, "company_city": "<b>Москва</b>", "problem": "<i>Проблема</i>"}]
        result = build_list_text(tickets, "mine")
        # Проверяем, что пользовательские данные экранированы
        # Исходные теги <b>Москва</b> не должны присутствовать как есть
        assert "<b>Москва</b>" not in result
        assert "<i>Проблема</i>" not in result
        # Но текст должен присутствовать (в экранированном виде)
        assert "Москва" in result
        assert "Проблема" in result


class TestFormatTicketHistory:
    """Тесты для format_ticket_history."""

    def test_empty_history(self):
        """Проверяет пустую историю."""
        result = format_ticket_history([])
        assert "пока нет сообщений" in result

    def test_client_message(self):
        """Проверяет форматирование сообщения клиента."""
        messages = [
            {
                "sender_role": "client",
                "created_at": "2024-01-15T10:30:00+00:00",
                "text": "Привет, у меня проблема",
                "media_type": None,
            }
        ]
        result = format_ticket_history(messages)
        assert "👤 Клиент" in result
        assert "Привет, у меня проблема" in result
        assert "15.01" in result

    def test_engineer_message(self):
        """Проверяет форматирование сообщения инженера."""
        messages = [
            {
                "sender_role": "engineer",
                "created_at": "2024-01-15T11:00:00+00:00",
                "text": "Здравствуйте, чем могу помочь?",
                "media_type": None,
            }
        ]
        result = format_ticket_history(messages)
        assert "👨‍🔧 Инженер" in result

    def test_media_message(self):
        """Проверяет форматирование сообщения с медиа."""
        messages = [
            {
                "sender_role": "client",
                "created_at": "2024-01-15T10:30:00+00:00",
                "text": "",
                "media_type": "photo",
            }
        ]
        result = format_ticket_history(messages)
        assert "📎 photo" in result

    def test_invalid_date_format(self):
        """Проверяет обработку некорректного формата даты."""
        messages = [
            {
                "sender_role": "client",
                "created_at": "invalid-date",
                "text": "Тест",
                "media_type": None,
            }
        ]
        # Не должно падать
        result = format_ticket_history(messages)
        assert "Тест" in result

    def test_html_escaping_in_history(self):
        """Проверяет экранирование HTML в истории."""
        messages = [
            {
                "sender_role": "client",
                "created_at": "2024-01-15T10:30:00+00:00",
                "text": "<script>alert('xss')</script>",
                "media_type": None,
            }
        ]
        result = format_ticket_history(messages)
        assert "<script>" not in result


class TestSaveMediaFile:
    """Тесты для save_media_file (скачивание и сохранение файлов)."""

    async def test_no_file_id_returns_none(self):
        """Пустой file_id возвращает None."""
        bot = MagicMock()
        result = await save_media_file(bot, "", 1, "photo")
        assert result is None

    async def test_no_file_path_returns_none(self):
        """Если у файла нет file_path, возвращает None."""
        bot = MagicMock()
        bot.get_file = MagicMock(return_value=MagicMock(file_path=None))
        result = await save_media_file(bot, "file_id", 1, "photo")
        assert result is None

    async def test_successful_save(self):
        """Успешное скачивание и сохранение файла."""
        bot = MagicMock()
        bot.get_file = AsyncMock(return_value=MagicMock(file_path="photos/photo.jpg"))
        bot.download = AsyncMock()

        with patch("bot.media.ensure_ticket_dir", return_value="/tmp/ticket_1"), \
             patch("bot.media.next_file_number", return_value=1), \
             patch("bot.media.MEDIA_DIR", "media"):
            result = await save_media_file(bot, "file_id", 1, "photo", "photo.jpg", "image/jpeg")
            assert result is not None
            assert "ticket_1" in result
            bot.download.assert_called_once()

    async def test_exception_returns_none(self):
        """Ошибка при скачивании возвращает None."""
        bot = MagicMock()
        bot.get_file = AsyncMock(return_value=MagicMock(file_path="photos/photo.jpg"))
        bot.download = AsyncMock(side_effect=Exception("download error"))

        with patch("bot.media.ensure_ticket_dir", return_value="/tmp/ticket_1"), \
             patch("bot.media.next_file_number", return_value=1):
            result = await save_media_file(bot, "file_id", 1, "photo")
            assert result is None
