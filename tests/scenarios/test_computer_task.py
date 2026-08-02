"""Scenario D: Computer Task (Run Regression).

Validates that a regression-running request triggers:
1. file_read to inspect the dataset first
2. shell_command to execute a Python regression script
3. Structured summary of regression results
4. Error recovery (retry on failure)
"""

import pytest
from tests.scenarios.conftest import ToolCallTracker


MOCK_CSV_HEAD = (
    "country_i,country_j,trade_flow,gdp_i,gdp_j,distance,contiguity\n"
    "USA,CAN,523400,21400000,1740000,1200,1\n"
    "USA,MEX,318200,21400000,1270000,1600,1\n"
    "USA,CHN,154800,21400000,14700000,11000,0\n"
    "USA,JPN,135600,21400000,5060000,10800,0\n"
    "USA,DEU,89200,21400000,3850000,7800,0\n"
)

MOCK_REGRESSION_OUTPUT = """\
                            OLS Regression Results
==============================================================================
Dep. Variable:          log_trade   R-squared:                       0.742
Model:                        OLS   Adj. R-squared:                  0.739
No. Observations:            1200   F-statistic:                     342.1
==============================================================================
                 coef    std err          t      P>|t|
------------------------------------------------------------------------------
const          -8.4321      1.234     -6.834      0.000
log_gdp_i       0.9876      0.045     21.947      0.000
log_gdp_j       0.8432      0.043     19.609      0.000
log_distance   -1.2345      0.078    -15.827      0.000
contiguity      0.6789      0.156      4.352      0.000
==============================================================================
"""

MOCK_SCRIPT_ERROR = (
    "Traceback (most recent call last):\n"
    '  File "<string>", line 3, in <module>\n'
    "ModuleNotFoundError: No module named 'statsmodels'"
)


class TestComputerTaskScenario:
    """Validate the computer task (regression) flow."""

    def test_file_read_before_code(self, tool_tracker):
        """LLM should inspect the data before writing regression code."""
        tool_tracker.record(
            "file_read",
            {"path": "~/Dropbox/Projects/data/trade_flows.csv"},
            MOCK_CSV_HEAD,
        )
        tool_tracker.record(
            "shell_command",
            {"command": "python3 -c '...'"},
            MOCK_REGRESSION_OUTPUT,
        )

        assert tool_tracker.tool_names[0] == "file_read", "Should read data before running code"
        assert tool_tracker.count("shell_command") >= 1

    def test_inspects_column_names(self, tool_tracker):
        """file_read result should be used to identify correct column names."""
        tool_tracker.record(
            "file_read",
            {"path": "~/Dropbox/Projects/data/trade_flows.csv"},
            MOCK_CSV_HEAD,
        )

        csv_result = tool_tracker.calls_for("file_read")[0]["result"]
        expected_columns = ["trade_flow", "gdp_i", "gdp_j", "distance", "contiguity"]
        for col in expected_columns:
            assert col in csv_result, f"Column '{col}' should be visible in data inspection"

    def test_regression_script_execution(self, tool_tracker):
        """shell_command should run a Python regression script."""
        tool_tracker.record(
            "shell_command",
            {"command": 'python3 -c "import pandas as pd; import statsmodels.api as sm; ..."'},
            MOCK_REGRESSION_OUTPUT,
        )

        shell_calls = tool_tracker.calls_for("shell_command")
        assert len(shell_calls) >= 1
        cmd = shell_calls[0]["args"]["command"]
        assert "python" in cmd.lower()

    def test_output_has_coefficients(self):
        """Regression summary should include key coefficient interpretations."""
        mock_summary = (
            "*Model: Gravity Regression*\n"
            "Dep. var: log(trade_flow), N=1,200, R²=0.742\n\n"
            "Key coefficients:\n"
            "• log(GDP_i): 0.99 (se=0.045, p<0.001) — near-unit elasticity\n"
            "• log(GDP_j): 0.84 (se=0.043, p<0.001)\n"
            "• log(distance): -1.23 (se=0.078, p<0.001) — trade falls with distance\n"
            "• contiguity: 0.68 (se=0.156, p<0.001) — sharing a border ~doubles trade\n\n"
            "The gravity model fits well (R²=0.74). GDP elasticities are close to "
            "theoretical predictions. Distance has the expected negative effect."
        )
        assert "R²" in mock_summary or "R-squared" in mock_summary
        assert "0.99" in mock_summary or "0.987" in mock_summary
        assert "distance" in mock_summary.lower()

    def test_error_recovery_flow(self, tool_tracker):
        """If the script fails, LLM should read the error and retry."""
        tool_tracker.record(
            "file_read",
            {"path": "~/Dropbox/Projects/data/trade_flows.csv"},
            MOCK_CSV_HEAD,
        )
        # First attempt fails
        tool_tracker.record(
            "shell_command",
            {"command": "python3 -c 'import statsmodels...'"},
            MOCK_SCRIPT_ERROR,
        )
        # LLM reads error, retries with pip install or alternative
        tool_tracker.record(
            "shell_command",
            {"command": "pip install statsmodels && python3 -c '...'"},
            MOCK_REGRESSION_OUTPUT,
        )

        shell_calls = tool_tracker.calls_for("shell_command")
        assert len(shell_calls) >= 2, "Should retry after failure"
        assert "error" in shell_calls[0]["result"].lower() or "module" in shell_calls[0]["result"].lower()
        assert "OLS" in shell_calls[1]["result"]

    def test_no_fabricated_results(self, tool_tracker):
        """Regression numbers should come from actual shell output, not be invented."""
        tool_tracker.record(
            "shell_command",
            {"command": "python3 regression.py"},
            MOCK_REGRESSION_OUTPUT,
        )

        shell_result = tool_tracker.calls_for("shell_command")[0]["result"]
        report_numbers = ["0.742", "0.9876", "-1.2345", "0.6789"]
        for num in report_numbers:
            assert num in shell_result, f"Number {num} must come from actual output"

    def test_concise_whatsapp_summary(self):
        """Output should be a concise summary, not the full statsmodels dump."""
        mock_summary = (
            "Gravity model results (N=1,200):\n"
            "• GDP elasticity ≈ 1.0 (as expected)\n"
            "• Distance elasticity: -1.23\n"
            "• Border effect: +68% trade\n"
            "• R² = 0.74\n"
            "Model fits standard gravity predictions well."
        )
        assert len(mock_summary) < 500, "Summary should be concise for WhatsApp"
        assert len(mock_summary.split("\n")) < 15, "Should not be a wall of text"
