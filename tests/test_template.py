import json
import pytest
from fsm import WorkflowData, WorkflowFormatter, _render


@pytest.fixture
def ctx() -> WorkflowData:
    c = WorkflowData()
    c["city"] = "London"
    c["lat"] = "51.5074"
    c["api_result"] = {
        "results": {
            "sunrise": "2026-06-18T04:43:09+00:00",
            "sunset": "2026-06-18T21:02:47+00:00",
        },
        "status": "OK",
    }
    c["weather"] = {
        "daily": {
            "temperature_2m_max": [22.5],
            "temperature_2m_min": [12.1],
            "precipitation_sum": [0.0],
        }
    }
    return c


class TestRenderTopLevel:
    def test_string_value(self, ctx):
        assert _render("{city}", ctx) == "London"

    def test_multiple_placeholders(self, ctx):
        assert _render("{city} at {lat}", ctx) == "London at 51.5074"

    def test_literal_passthrough(self, ctx):
        assert _render("no placeholders", ctx) == "no placeholders"


class TestRenderNestedDictAccess:
    def test_single_key(self, ctx):
        assert _render("{api_result[status]}", ctx) == "OK"

    def test_two_levels(self, ctx):
        assert _render("{api_result[results][sunrise]}", ctx) == "2026-06-18T04:43:09+00:00"

    def test_two_levels_second_key(self, ctx):
        assert _render("{api_result[results][sunset]}", ctx) == "2026-06-18T21:02:47+00:00"


class TestRenderListIndexAccess:
    def test_integer_index(self, ctx):
        assert _render("{weather[daily][temperature_2m_max][0]}", ctx) == "22.5"

    def test_integer_index_second_item(self, ctx):
        ctx["weather"]["daily"]["temperature_2m_max"].append(23.1)
        assert _render("{weather[daily][temperature_2m_max][1]}", ctx) == "23.1"


class TestRenderDictFallback:
    def test_dict_serialised_as_json(self, ctx):
        rendered = _render("{api_result[results]}", ctx)
        parsed = json.loads(rendered)
        assert parsed["sunrise"] == "2026-06-18T04:43:09+00:00"

    def test_list_serialised_as_json(self, ctx):
        rendered = _render("{weather[daily][temperature_2m_max]}", ctx)
        assert json.loads(rendered) == [22.5]
