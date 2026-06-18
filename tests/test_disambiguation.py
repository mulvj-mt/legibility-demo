import pytest
from unittest.mock import patch, MagicMock
from fsm import WorkflowRunner, WorkflowError, InputRequest, DisambiguateOption


# Minimal workflow that exercises the guarded branch without needing sunset/weather APIs.
WORKFLOW = {
    "steps": [
        {"name": "place_name", "type": "input", "prompt": "Place: "},
        {
            "name": "geocode",
            "type": "api",
            "method": "get",
            "url": "https://nominatim.example/search",
            "headers": {"User-Agent": "test/0.1"},
            "params": {"q": "{place_name}, UK", "format": "json", "limit": "10"},
            "transitions": [
                {"target": "done", "cond": "single_result", "on": "store_first_as_location"},
                {"target": "disambiguate_location"},
            ],
        },
        {
            "name": "disambiguate_location",
            "type": "disambiguate",
            "source": "geocode",
            "label_template": "{display_name}",
            "prompt": "Multiple locations found — please choose one:",
        },
        {"name": "done", "type": "output", "template": "Location: {location[display_name]}"},
    ]
}

SINGLE = [{"display_name": "London, Greater London, England, UK", "lat": "51.5074", "lon": "-0.1278"}]
MULTIPLE = [
    {"display_name": "Birkenhead, Merseyside, England, UK", "lat": "53.3934", "lon": "-3.0143"},
    {"display_name": "Birkenhead, Auckland, New Zealand", "lat": "-36.8105", "lon": "174.6975"},
]


def mock_response(data):
    m = MagicMock()
    m.json.return_value = data
    m.raise_for_status.return_value = None
    return m


class TestSingleResultBypass:
    def test_skips_disambiguation_and_finishes(self):
        with patch("requests.request", return_value=mock_response(SINGLE)):
            runner = WorkflowRunner(WORKFLOW)
            runner.provide_input("London")
            assert runner.is_finished()

    def test_no_pending_after_finish(self):
        with patch("requests.request", return_value=mock_response(SINGLE)):
            runner = WorkflowRunner(WORKFLOW)
            runner.provide_input("London")
            assert runner.pending_request() is None

    def test_location_stored_from_first_result(self):
        with patch("requests.request", return_value=mock_response(SINGLE)):
            runner = WorkflowRunner(WORKFLOW)
            runner.provide_input("London")
            assert runner._context["location"] == SINGLE[0]


class TestMultipleResultsDisambiguation:
    def test_pauses_at_disambiguate_step(self):
        with patch("requests.request", return_value=mock_response(MULTIPLE)):
            runner = WorkflowRunner(WORKFLOW)
            runner.provide_input("Birkenhead")
            req = runner.pending_request()
            assert req is not None
            assert req.kind == "disambiguate"
            assert req.step_name == "disambiguate_location"

    def test_options_match_geocode_results(self):
        with patch("requests.request", return_value=mock_response(MULTIPLE)):
            runner = WorkflowRunner(WORKFLOW)
            runner.provide_input("Birkenhead")
            opts = runner.pending_request().options
            assert len(opts) == 2
            assert opts[0].label == MULTIPLE[0]["display_name"]
            assert opts[1].label == MULTIPLE[1]["display_name"]

    def test_options_carry_full_candidate_dict(self):
        with patch("requests.request", return_value=mock_response(MULTIPLE)):
            runner = WorkflowRunner(WORKFLOW)
            runner.provide_input("Birkenhead")
            opts = runner.pending_request().options
            assert opts[0].value == MULTIPLE[0]
            assert opts[1].value == MULTIPLE[1]

    def test_selecting_first_option_finishes(self):
        with patch("requests.request", return_value=mock_response(MULTIPLE)):
            runner = WorkflowRunner(WORKFLOW)
            runner.provide_input("Birkenhead")
            runner.provide_input("0")
            assert runner.is_finished()

    def test_selected_location_stored(self):
        with patch("requests.request", return_value=mock_response(MULTIPLE)):
            runner = WorkflowRunner(WORKFLOW)
            runner.provide_input("Birkenhead")
            runner.provide_input("1")
            assert runner._context["location"] == MULTIPLE[1]

    def test_not_finished_while_disambiguating(self):
        with patch("requests.request", return_value=mock_response(MULTIPLE)):
            runner = WorkflowRunner(WORKFLOW)
            runner.provide_input("Birkenhead")
            assert not runner.is_finished()


class TestZeroResults:
    def test_raises_workflow_error(self):
        with patch("requests.request", return_value=mock_response([])):
            runner = WorkflowRunner(WORKFLOW)
            with pytest.raises(WorkflowError, match="no results"):
                runner.provide_input("Xyzzy")
