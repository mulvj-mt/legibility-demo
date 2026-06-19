"""Tests for the two FSM engine additions introduced for workflow_3:
  - currency_is_gbp guard
  - final: true on non-last steps
"""
import pytest
from unittest.mock import patch, MagicMock
from fsm import WorkflowRunner, WorkflowError


def mock_api(data):
    m = MagicMock()
    m.json.return_value = data
    m.raise_for_status.return_value = None
    return m


# ── currency_is_gbp guard ──────────────────────────────────────────────────────

CURRENCY_WORKFLOW = {
    "steps": [
        {"name": "country_name", "type": "input", "prompt": "Country: "},
        {
            "name": "search_country",
            "type": "api",
            "method": "get",
            "url": "http://localhost/countries/search",
            "params": {"q": "{country_name}"},
            "transitions": [
                {"target": "gbp_output", "cond": "single_result", "on": "store_first_as_location"},
                {"target": "disambiguate_country"},
            ],
        },
        {
            "name": "disambiguate_country",
            "type": "disambiguate",
            "source": "search_country",
            "label_template": "{name}",
            "prompt": "Choose:",
        },
        {
            "name": "check_currency",
            "type": "api",
            "method": "get",
            "url": "http://localhost/fx",
            "params": {"from": "GBP", "to": "{location[currency]}"},
            "transitions": [
                {"target": "gbp_output", "cond": "currency_is_gbp"},
                {"target": "fx_output"},
            ],
        },
        {"name": "gbp_output", "type": "output", "final": True, "template": "GBP: {location[name]}"},
        {"name": "fx_output", "type": "output", "template": "FX: {location[name]}"},
    ]
}

GBP_COUNTRY = [{"name": "United Kingdom", "capital": "London", "currency": "GBP", "region": "Europe"}]
JPY_COUNTRY = [{"name": "Japan", "capital": "Tokyo", "currency": "JPY", "region": "Asia"}]
FX_RESPONSE = {"rates": {"JPY": 213.0}}


class TestCurrencyIsGbpGuard:
    def test_gbp_country_routes_to_gbp_output(self):
        with patch("requests.request", side_effect=[mock_api(GBP_COUNTRY), mock_api(FX_RESPONSE)]):
            runner = WorkflowRunner(CURRENCY_WORKFLOW)
            runner.provide_input("United Kingdom")
            assert runner.is_finished()

    def test_non_gbp_country_routes_to_fx_output(self):
        with patch("requests.request", side_effect=[mock_api(JPY_COUNTRY), mock_api(FX_RESPONSE)]):
            runner = WorkflowRunner(CURRENCY_WORKFLOW)
            runner.provide_input("Japan")
            assert runner.is_finished()

    def test_guard_false_when_location_is_not_gbp(self):
        with patch("requests.request", side_effect=[mock_api(JPY_COUNTRY), mock_api(FX_RESPONSE)]):
            runner = WorkflowRunner(CURRENCY_WORKFLOW)
            runner.provide_input("Japan")
            # fx_output is the final step reached; gbp_output was skipped
            assert runner._context["location"]["currency"] == "JPY"

    def test_guard_true_when_location_currency_is_gbp(self):
        with patch("requests.request", side_effect=[mock_api(GBP_COUNTRY)]):
            runner = WorkflowRunner(CURRENCY_WORKFLOW)
            runner.provide_input("United Kingdom")
            assert runner._context["location"]["currency"] == "GBP"

    def test_unknown_cond_raises(self):
        bad_workflow = {
            "steps": [
                {"name": "start", "type": "input", "prompt": "x: "},
                {
                    "name": "check",
                    "type": "api",
                    "method": "get",
                    "url": "http://localhost/x",
                    "params": {},
                    "transitions": [{"target": "end", "cond": "no_such_guard"}],
                },
                {"name": "end", "type": "output", "template": "done"},
            ]
        }
        with pytest.raises(WorkflowError, match="Unknown condition"):
            WorkflowRunner(bad_workflow)


# ── final: true on non-last step ───────────────────────────────────────────────

BRANCHING_WORKFLOW = {
    "steps": [
        {"name": "start", "type": "input", "prompt": "go: "},
        {
            "name": "branch",
            "type": "api",
            "method": "get",
            "url": "http://localhost/data",
            "params": {"q": "{start}"},
            "transitions": [
                {"target": "early_exit", "cond": "single_result", "on": "store_first_as_location"},
                {"target": "normal_exit"},
            ],
        },
        {"name": "early_exit", "type": "output", "final": True, "template": "early"},
        {"name": "normal_exit", "type": "output", "template": "normal"},
    ]
}


class TestFinalTrueOnNonLastStep:
    def test_single_result_reaches_early_exit_and_finishes(self):
        data = [{"display_name": "only one", "lat": "1", "lon": "2"}]
        with patch("requests.request", return_value=mock_api(data)):
            runner = WorkflowRunner(BRANCHING_WORKFLOW)
            runner.provide_input("x")
            assert runner.is_finished()

    def test_multiple_results_reaches_normal_exit_and_finishes(self):
        data = [
            {"display_name": "first", "lat": "1", "lon": "2"},
            {"display_name": "second", "lat": "3", "lon": "4"},
        ]
        with patch("requests.request", return_value=mock_api(data)):
            runner = WorkflowRunner(BRANCHING_WORKFLOW)
            runner.provide_input("x")
            # multiple results → default transition → normal_exit (last step, final by default)
            assert runner.is_finished()
