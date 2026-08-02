# Real-World Test Scenarios

Validates the LLM-first chat architecture (Phase 18) with complex multi-turn tasks.

## Running

```bash
# Mock mode (no LLM/API calls, uses scripted responses)
pytest tests/scenarios/ -xvs

# Live smoke test (requires DAN_LLM_API_KEY + DAN_TAVILY_API_KEY)
pytest tests/scenarios/ -xvs --run-live
```

## Scenarios

| Scenario | File | Tools Exercised |
|----------|------|-----------------|
| A. Literature Review | test_literature_review.py | web_search, web_fetch, list_directory, pdf_read |
| B. Equity Report | test_equity_report.py | current_datetime, web_search, web_fetch |
| C. Deep Research | test_deep_research.py | web_search, web_fetch, current_datetime |
| D. Computer Task | test_computer_task.py | file_read, shell_command |
| E. Casual Utility | test_casual_utility.py | current_datetime, web_search, shell_command, clipboard, send_email, screenshot |
