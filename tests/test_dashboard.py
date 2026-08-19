"""
Tests for dashboard HTML generation.
"""
import json
import html as _html

from bot.dashboard import generate_dashboard


SAMPLE_DATA = {
    "total": 42,
    "open": 5,
    "in_progress": 10,
    "closed": 27,
    "reaction_min": 12,
    "repeat_pct": 30,
    "this_week": 12,
    "prev_week": 8,
    "status_flow": [5, 10, 20, 7],
    "equipment": [
        {"type": "Лазер CO2", "cnt": 20},
        {"type": "Фрезер", "cnt": 15},
    ],
    "ratings_dist": [2, 3, 5, 8, 12],
    "problems": [
        {"name": "Ошибка AL-01", "cnt": 8},
        {"name": "Люфт оси", "cnt": 5},
    ],
    "daily_labels": ["Пн", "Вт", "Ср"],
    "daily_counts": [3, 5, 2],
    "day_names": ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"],
    "dow_load": [10, 8, 12, 6, 9, 3, 1],
    "eng_names": ["Сергей", "Иван"],
    "eng_loads": [15, 10],
    "eng_avg_times": [2.5, 1.8],
    "eng_colors": ["#e94560", "#4361ee"],
    "ratings": [
        {"name": "Сергей", "rating": 4.5, "count": 20},
        {"name": "Иван", "rating": 3.8, "count": 15},
    ],
    "cities": [
        {"city": "Москва", "cnt": 25},
        {"city": "СПб", "cnt": 10},
    ],
}


class TestDashboardBasic:
    """Basic structure tests."""

    def test_generates_html(self):
        html = generate_dashboard(json.dumps(SAMPLE_DATA, ensure_ascii=False))
        assert "<!DOCTYPE html>" in html
        assert "<html lang=\"ru\">" in html
        assert "</html>" in html

    def test_contains_head(self):
        html = generate_dashboard(json.dumps(SAMPLE_DATA, ensure_ascii=False))
        assert "<head>" in html
        assert "Hotline Service - Дашборд" in html

    def test_contains_body(self):
        html = generate_dashboard(json.dumps(SAMPLE_DATA, ensure_ascii=False))
        assert "<body>" in html
        assert "Hotline Service" in html


class TestDashboardKPI:
    """KPI section tests."""

    def test_kpi_total(self):
        html = generate_dashboard(json.dumps(SAMPLE_DATA, ensure_ascii=False))
        assert "Всего" in html
        assert str(SAMPLE_DATA["total"]) in html

    def test_kpi_open(self):
        html = generate_dashboard(json.dumps(SAMPLE_DATA, ensure_ascii=False))
        assert "Открыто" in html
        assert str(SAMPLE_DATA["open"]) in html

    def test_kpi_closed(self):
        html = generate_dashboard(json.dumps(SAMPLE_DATA, ensure_ascii=False))
        assert "Закрыто" in html
        assert str(SAMPLE_DATA["closed"]) in html

    def test_kpi_reaction(self):
        html = generate_dashboard(json.dumps(SAMPLE_DATA, ensure_ascii=False))
        assert "Реакция" in html
        assert str(SAMPLE_DATA["reaction_min"]) in html

    def test_kpi_this_week(self):
        html = generate_dashboard(json.dumps(SAMPLE_DATA, ensure_ascii=False))
        assert "За неделю" in html
        assert str(SAMPLE_DATA["this_week"]) in html

    def test_kpi_repeat_pct(self):
        html = generate_dashboard(json.dumps(SAMPLE_DATA, ensure_ascii=False))
        assert "Повторных" in html
        assert str(SAMPLE_DATA["repeat_pct"]) in html


class TestDashboardStatusFlow:
    """Status flow bars."""

    def test_status_open_bar(self):
        html = generate_dashboard(json.dumps(SAMPLE_DATA, ensure_ascii=False))
        assert "Статусы" in html
        assert str(SAMPLE_DATA["status_flow"][0]) in html

    def test_status_completed_bar(self):
        html = generate_dashboard(json.dumps(SAMPLE_DATA, ensure_ascii=False))
        assert str(SAMPLE_DATA["status_flow"][2]) in html


class TestDashboardEquipment:
    """Equipment bars."""

    def test_equipment_section(self):
        html = generate_dashboard(json.dumps(SAMPLE_DATA, ensure_ascii=False))
        assert "Оборудование" in html
        escaped = _html.escape("Лазер CO2")
        assert escaped in html

    def test_equipment_count(self):
        html = generate_dashboard(json.dumps(SAMPLE_DATA, ensure_ascii=False))
        counts = [str(e["cnt"]) for e in SAMPLE_DATA["equipment"]]
        for c in counts:
            assert c in html


class TestDashboardRatings:
    """Ratings section."""

    def test_ratings_dist(self):
        html = generate_dashboard(json.dumps(SAMPLE_DATA, ensure_ascii=False))
        assert "Оценки" in html

    def test_ratings_table(self):
        html = generate_dashboard(json.dumps(SAMPLE_DATA, ensure_ascii=False))
        assert "Рейтинг" in html
        assert "Инженер" in html

    def test_engineer_names_in_rating(self):
        html = generate_dashboard(json.dumps(SAMPLE_DATA, ensure_ascii=False))
        escaped = _html.escape("Сергей")
        assert escaped in html


class TestDashboardDays:
    """Daily and weekly graphs."""

    def test_daily_labels(self):
        html = generate_dashboard(json.dumps(SAMPLE_DATA, ensure_ascii=False))
        assert "По дням" in html
        for lbl in SAMPLE_DATA["daily_labels"]:
            escaped = _html.escape(lbl)
            assert escaped in html

    def test_weekday_labels(self):
        html = generate_dashboard(json.dumps(SAMPLE_DATA, ensure_ascii=False))
        assert "Дни недели" in html


class TestDashboardCities:
    """Cities table."""

    def test_cities_section(self):
        html = generate_dashboard(json.dumps(SAMPLE_DATA, ensure_ascii=False))
        assert "Города" in html
        escaped = _html.escape("Москва")
        assert escaped in html

    def test_city_count(self):
        html = generate_dashboard(json.dumps(SAMPLE_DATA, ensure_ascii=False))
        for city in SAMPLE_DATA["cities"]:
            assert str(city["cnt"]) in html


class TestDashboardEngineerSection:
    """Engineer load and time."""

    def test_engineer_load(self):
        html = generate_dashboard(json.dumps(SAMPLE_DATA, ensure_ascii=False))
        assert "Загрузка" in html
        escaped = _html.escape("Сергей")
        assert escaped in html

    def test_engineer_time(self):
        html = generate_dashboard(json.dumps(SAMPLE_DATA, ensure_ascii=False))
        assert "Время" in html


class TestDashboardXSSProtection:
    """XSS protection: html.escape() must be applied."""

    def test_escapes_equipment_name(self):
        data = dict(SAMPLE_DATA)
        data["equipment"] = [{"type": '<script>alert(1)</script>Лазер', "cnt": 5}]
        html = generate_dashboard(json.dumps(data, ensure_ascii=False))
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_escapes_problem_name(self):
        data = dict(SAMPLE_DATA)
        data["problems"] = [{"name": '<img onerror=alert(1)>Проблема', "cnt": 3}]
        html = generate_dashboard(json.dumps(data, ensure_ascii=False))
        assert "<img" not in html
        assert "&lt;img" in html

    def test_escapes_engineer_name(self):
        data = dict(SAMPLE_DATA)
        data["eng_names"] = ['<b>bad</b>Сергей', "Иван"]
        html = generate_dashboard(json.dumps(data, ensure_ascii=False))
        assert "<b>bad</b>" not in html
        assert "&lt;b&gt;bad&lt;/b&gt;" in html

    def test_escapes_city_name(self):
        data = dict(SAMPLE_DATA)
        data["cities"] = [{"city": '<iframe src=evil>Мск', "cnt": 5}]
        html = generate_dashboard(json.dumps(data, ensure_ascii=False))
        assert "<iframe" not in html
        assert "&lt;iframe" in html

    def test_escapes_daily_label(self):
        data = dict(SAMPLE_DATA)
        data["daily_labels"] = ['<script>Пн</script>', "Вт", "Ср"]
        html = generate_dashboard(json.dumps(data, ensure_ascii=False))
        assert "<script>" not in html
        assert "&lt;script&gt;" in html


class TestDashboardEdgeCases:
    """Edge cases: empty data, zero values, etc."""

    def test_empty_equipment(self):
        data = dict(SAMPLE_DATA)
        data["equipment"] = []
        html = generate_dashboard(json.dumps(data, ensure_ascii=False))
        assert "Оборудование" in html
        assert "<!DOCTYPE html>" in html

    def test_empty_problems(self):
        data = dict(SAMPLE_DATA)
        data["problems"] = []
        html = generate_dashboard(json.dumps(data, ensure_ascii=False))
        assert "Топ проблем" in html

    def test_empty_cities(self):
        data = dict(SAMPLE_DATA)
        data["cities"] = []
        html = generate_dashboard(json.dumps(data, ensure_ascii=False))
        assert "Города" in html

    def test_empty_ratings(self):
        data = dict(SAMPLE_DATA)
        data["ratings"] = []
        html = generate_dashboard(json.dumps(data, ensure_ascii=False))
        assert "Рейтинг" in html

    def test_zero_prev_week(self):
        data = dict(SAMPLE_DATA)
        data["prev_week"] = 0
        html = generate_dashboard(json.dumps(data, ensure_ascii=False))
        assert "<!DOCTYPE html>" in html
        # Trend should not appear when prev_week = 0
        assert "% к прош." not in html

    def test_zero_all_values(self):
        data = dict(SAMPLE_DATA)
        data["total"] = 0
        data["open"] = 0
        data["this_week"] = 0
        html = generate_dashboard(json.dumps(data, ensure_ascii=False))
        assert "<!DOCTYPE html>" in html
        assert "0" in html

    def test_stars_formatting(self):
        html = generate_dashboard(json.dumps(SAMPLE_DATA, ensure_ascii=False))
        assert "★" in html


class TestDashboardStandalone:
    """Test the __main__ entry point."""

    def test_standalone_run(self, capsys):
        """Verify generate_dashboard doesn't raise on valid data (simulate CLI)."""
        result = generate_dashboard(json.dumps(SAMPLE_DATA, ensure_ascii=False))
        assert len(result) > 0
        assert result.startswith("<!DOCTYPE html>")
