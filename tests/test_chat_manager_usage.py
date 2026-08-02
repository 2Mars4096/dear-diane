from dan.server.chat_manager import _merge_usage_totals, _normalize_usage


def test_normalize_usage_preserves_prompt_aliases_and_totals() -> None:
    usage = _normalize_usage(
        {
            "prompt_tokens": 120,
            "completion_tokens": 45,
            "cached_input_tokens": 7,
            "cache_write_tokens": 3,
        }
    )

    assert usage == {
        "prompt": 120,
        "completion": 45,
        "prompt_tokens": 120,
        "completion_tokens": 45,
        "total_tokens": 165,
        "cached_input_tokens": 7,
        "cache_write_tokens": 3,
    }


def test_merge_usage_totals_accumulates_all_usage_keys() -> None:
    merged = _merge_usage_totals(
        {
            "prompt": 10,
            "completion": 4,
            "prompt_tokens": 10,
            "completion_tokens": 4,
            "total_tokens": 14,
            "cached_input_tokens": 2,
        },
        {
            "prompt_tokens": 6,
            "completion_tokens": 5,
            "cache_write_tokens": 9,
        },
    )

    assert merged == {
        "prompt": 16,
        "completion": 9,
        "prompt_tokens": 16,
        "completion_tokens": 9,
        "total_tokens": 25,
        "cached_input_tokens": 2,
        "cache_write_tokens": 9,
    }
