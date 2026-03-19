"""Web capability handlers: web_search, web_fetch, http_request."""
from __future__ import annotations

import asyncio
import re as _re
import os
from typing import Any

import httpx

from dan.server.capability_registry import CapabilityContext, CapabilityResult
from dan.server.capabilities._helpers import (
    _FILE_READ_MAX,
    _classify_network_exception,
    _failure_result,
    _sanitize_web_content,
)
from dan.server.search_models import (
    SearchResult,
    SearchResultSet,
    canonicalize_search_url,
    domain_from_url,
)

_MAX_AUTO_FETCH_RESULTS = 2
_MAX_AUTO_FETCH_ATTEMPTS = 4
_DEFAULT_FETCH_EXCERPT_MAX = 2400
_DEFAULT_MAX_WEB_SEARCH_CALLS_PER_TURN = 4
_DEFAULT_MAX_WEB_FETCH_ATTEMPTS_PER_TURN = 8
_STOP_WORDS = frozenset({
    "a",
    "an",
    "and",
    "as",
    "at",
    "by",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "latest",
    "of",
    "on",
    "or",
    "the",
    "to",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
})
_CREDIBILITY_TIERS: dict[str, tuple[str, ...]] = {
    "A": (".gov", ".edu", "wikipedia.org", "arxiv.org", "scholar.google.com"),
    "B": (
        "docs.python.org",
        "developer.mozilla.org",
        "openai.com",
        "anthropic.com",
        "github.com",
        "reuters.com",
        "apnews.com",
    ),
}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return max(0, int(raw.strip()))
    except (TypeError, ValueError):
        return default


def _fetch_excerpt_max() -> int:
    return _env_int("DAN_WEB_FETCH_EXCERPT_MAX", _DEFAULT_FETCH_EXCERPT_MAX)


def _trim_note(text: str, limit: int = 180) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _summarize_web_exception(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code if exc.response is not None else None
        reason = exc.response.reason_phrase if exc.response is not None else ""
        summary = f"HTTP {status}" if status is not None else "HTTP error"
        if reason:
            summary += f" {reason}"
        return summary
    return _trim_note(str(exc) or type(exc).__name__)


def _format_provider_failures(failures: list[dict[str, Any]] | None) -> str:
    if not failures:
        return ""
    rendered: list[str] = []
    for failure in failures[:3]:
        provider = str(failure.get("provider", "") or "").strip()
        summary = str(
            failure.get("summary", "")
            or failure.get("message", "")
            or failure.get("error", "")
            or ""
        ).strip()
        summary = _trim_note(summary, limit=90)
        if provider and summary:
            rendered.append(f"{provider}: {summary}")
    note = "; ".join(rendered)
    remaining = len(failures) - len(rendered)
    if remaining > 0:
        note += f"; +{remaining} more"
    return note


def _format_fetch_header(result: dict[str, Any], *, requested_url: str) -> str:
    resolved_url = str(result.get("url", "") or "").strip() or requested_url
    parts: list[str] = []
    status_code = result.get("status_code")
    if isinstance(status_code, int) and status_code > 0:
        parts.append(f"HTTP {status_code}")
    content_type = str(result.get("content_type", "") or "").split(";", 1)[0].strip()
    if content_type:
        parts.append(content_type)
    if result.get("cache_hit"):
        parts.append("cache hit")

    header = f"Fetched {resolved_url}" if resolved_url else "Fetched content"
    if parts:
        header += f" ({'; '.join(parts)})"
    return header


def _query_terms(query: str) -> list[str]:
    terms: list[str] = []
    for token in _re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]+", str(query or "").lower()):
        if token in _STOP_WORDS:
            continue
        terms.append(token)
    return list(dict.fromkeys(terms))


def _extract_relevant_excerpt(content: str, query: str, budget: int | None = None) -> str:
    text = str(content or "").strip()
    if not text:
        return ""
    budget = budget or _fetch_excerpt_max()
    paragraphs = [paragraph.strip() for paragraph in _re.split(r"\n\s*\n", text) if paragraph.strip()]
    if not paragraphs:
        return text[:budget]

    terms = _query_terms(query)
    if not terms:
        return text[:budget] + ("\n[...truncated]" if len(text) > budget else "")

    scored: list[tuple[int, int]] = []
    for index, paragraph in enumerate(paragraphs):
        lowered = paragraph.lower()
        score = sum(lowered.count(term) for term in terms)
        scored.append((score, index))

    best = [index for score, index in scored if score > 0]
    if not best:
        excerpt = text[:budget]
        return excerpt + ("\n[...truncated]" if len(text) > budget else "")

    selected_indices = sorted(best[:3])
    parts: list[str] = []
    used = 0
    previous_index: int | None = None
    for index in selected_indices:
        paragraph = paragraphs[index]
        addition = paragraph if not parts else "\n\n" + paragraph
        gap = 0 if previous_index is None else index - previous_index - 1
        if gap > 0:
            marker = f"\n\n[...skipped {gap} paragraphs...]\n\n"
            if used + len(marker) <= budget:
                parts.append(marker)
                used += len(marker)
        if used + len(addition) > budget:
            remaining = max(budget - used, 0)
            if remaining > 0:
                parts.append(addition[:remaining].rstrip())
            break
        parts.append(addition)
        used += len(addition)
        previous_index = index
    return "".join(parts).strip()


def _credibility_tier(url: str) -> str | None:
    domain = domain_from_url(url)
    if domain.startswith("www."):
        domain = domain[4:]
    for tier, rules in _CREDIBILITY_TIERS.items():
        if any(
            domain == rule or domain.endswith(rule) or domain.endswith(rule.lstrip("."))
            for rule in rules
        ):
            return tier
    return "C"


def _tier_label(tier: str | None) -> str:
    if tier == "A":
        return "authoritative"
    if tier == "B":
        return "established"
    return ""


def _score_result(query: str, result: SearchResult) -> float:
    terms = _query_terms(query)
    if not terms:
        return 0.0
    title = result.title.lower()
    snippet = result.snippet.lower()
    score = 0.0
    for term in terms:
        if term in title:
            score += 2.0
        if term in snippet:
            score += 1.0
    return score / max(len(terms), 1)


def _format_search_result(result: SearchResult) -> str:
    title = result.title.strip() or "(untitled result)"
    snippet = result.snippet.strip()
    url = result.url.strip()
    tier_label = _tier_label(result.credibility_tier)
    title_line = f"[{result.index}] {title}"
    if tier_label:
        title_line += f" ({tier_label})"
    if result.seen_in_recent_searches:
        title_line += " (also appeared in previous search)"
    lines = [title_line]
    if result.source_query:
        lines.append(f"Source query: {result.source_query}")
    if snippet:
        lines.append(f"Snippet: {snippet}")
    if url:
        lines.append(f"URL: {url}")
    return "\n".join(lines)


def _normalize_domain_values(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        value = [item.strip() for item in value.split(",")]
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        domain = str(item or "").strip().lower()
        if not domain:
            continue
        if domain.startswith("http://") or domain.startswith("https://"):
            domain = domain_from_url(domain)
        domain = domain.lstrip(".")
        if domain.startswith("www."):
            domain = domain[4:]
        if domain and domain not in seen:
            seen.add(domain)
            normalized.append(domain)
    return normalized


def _normalize_location(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        value = {}
    payload: dict[str, str] = {}
    country = str(value.get("country") or "").strip()
    language = str(value.get("language") or "").strip()
    if country:
        payload["country"] = country
    if language:
        payload["language"] = language
    return payload


def _decompose_query(query: str) -> list[str]:
    text = str(query or "").strip()
    if not text:
        return [text]
    lowered = text.lower()
    for token in (" vs ", " versus ", " or "):
        if token in lowered:
            pattern = _re.compile(token, _re.IGNORECASE)
            parts = [segment.strip(" ,") for segment in pattern.split(text) if segment.strip(" ,")]
            if 1 < len(parts) <= 3:
                return parts[:3]
    multi_match = _re.match(r"(?P<prefix>.+?)\bfor\b\s+(?P<entities>.+)", text, flags=_re.IGNORECASE)
    if multi_match:
        entities = [
            item.strip(" ,")
            for item in _re.split(r",|\band\b", multi_match.group("entities"), flags=_re.IGNORECASE)
            if item.strip(" ,")
        ]
        prefix = multi_match.group("prefix").strip()
        if 1 < len(entities) <= 3 and prefix:
            return [f"{entity} {prefix}".strip() for entity in entities[:3]]
    return [text]


def _reformulate_query(original: str) -> str | None:
    candidate = _re.sub(r'"([^"]+)"', r"\1", str(original or "")).strip()
    candidate = _re.sub(r"\b(latest|best|current|recent)\b", "", candidate, flags=_re.IGNORECASE)
    candidate = _re.sub(r"\s{2,}", " ", candidate).strip(" ,")
    if candidate and candidate.lower() != str(original or "").strip().lower():
        return candidate
    return None


def _thin_results(results: list[SearchResult]) -> bool:
    if len(results) < 2:
        return True
    return all(len((result.snippet or "").strip()) < 30 for result in results)


def _append_recent_urls(state: dict[str, Any], urls: list[str], limit: int = 50) -> None:
    order = list(state.get("session_seen_order") or [])
    seen = set(order)
    for url in urls:
        if not url:
            continue
        if url in seen:
            order = [item for item in order if item != url]
        order.append(url)
        seen.add(url)
    state["session_seen_order"] = order[-limit:]


def _search_state(ctx: CapabilityContext | None) -> dict[str, Any]:
    if ctx is None or not isinstance(getattr(ctx, "search_state", None), dict):
        return {}
    return ctx.search_state


def _budget_state(ctx: CapabilityContext | None) -> dict[str, int]:
    if ctx is None or not isinstance(getattr(ctx, "web_budget_state", None), dict):
        return {}
    return ctx.web_budget_state


def _check_and_consume_budget(
    state: dict[str, int],
    *,
    used_key: str,
    limit_key: str,
    default_limit: int,
    amount: int = 1,
) -> tuple[bool, int]:
    limit = int(state.get(limit_key, default_limit) or default_limit)
    used = int(state.get(used_key, 0) or 0)
    if used + amount > limit:
        return False, limit
    state[used_key] = used + amount
    state.setdefault(limit_key, limit)
    return True, limit


def _budget_exhausted_message(kind: str, limit: int) -> str:
    if kind == "search":
        return (
            f"Web-search budget exhausted for this turn ({limit} search calls). "
            "Narrow the query or answer from the evidence already retrieved."
        )
    return (
        f"Web-fetch budget exhausted for this turn ({limit} fetch attempts). "
        "Answer from the evidence already retrieved or narrow the query."
    )


async def _fetch_grounding_excerpt(result: SearchResult, query: str) -> dict[str, Any]:
    try:
        from dan.tools.web_fetch import web_fetch

        fetched = await web_fetch(url=result.url)
        content = _sanitize_web_content(fetched.get("content", ""))
        content = _extract_relevant_excerpt(content, query)
        if not content:
            return {
                "index": result.index,
                "url": result.url,
                "success": False,
                "error": "Fetched page returned no usable text.",
                "fetched_content": "[Fetch failed: fetched page returned no usable text]",
            }
        return {
            "index": result.index,
            "url": result.url,
            "success": True,
            "content": content,
            "cache_hit": bool(fetched.get("cache_hit")),
            "fetched_content": content,
        }
    except Exception as exc:
        error = _summarize_web_exception(exc)[:240]
        return {
            "index": result.index,
            "url": result.url,
            "success": False,
            "error": error,
            "fetched_content": f"[Fetch failed: {error} — content unavailable from this source]",
        }


def _search_result_from_item(
    item: dict[str, Any],
    *,
    source_query: str,
    fallback_provider: str,
) -> SearchResult:
    url = str(item.get("url", "") or "").strip()
    return SearchResult(
        index=0,
        title=str(item.get("title", "") or "").strip(),
        url=url,
        snippet=str(item.get("snippet", "") or "").strip(),
        provider=str(item.get("provider", "") or fallback_provider).strip(),
        result_kind=str(item.get("result_kind", "") or "organic").strip() or "organic",
        page_age=str(item.get("page_age", "") or "").strip() or None,
        source_query=source_query,
        canonical_url=canonicalize_search_url(url) or url,
        credibility_tier=_credibility_tier(url),
    )


def _build_anthropic_search_blocks(result_set: SearchResultSet) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for result in result_set.results:
        excerpt = result.fetched_content or result.snippet or result.title or ""
        blocks.append({
            "type": "search_result",
            "title": result.title,
            "url": result.url,
            "source": {"type": "url", "url": result.url},
            "content": [{"type": "text", "text": excerpt}],
            "citations": {"enabled": True},
        })
    return blocks


async def handle_web_search(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    query = str(args.get("query", "") or "").strip()
    if not query:
        return CapabilityResult(success=False, message="No search query provided.")

    num = min(10, max(1, int(args.get("num_results") or 3)))
    grounding_required = bool(getattr(ctx, "grounding_required", False))
    fetch_content_explicit = "fetch_content" in args
    fetch_content = bool(args.get("fetch_content")) if fetch_content_explicit else False
    if grounding_required and not fetch_content_explicit:
        fetch_content = True
    search_depth = str(args.get("search_depth") or "").strip().lower()
    if search_depth not in {"quick", "thorough"}:
        search_depth = "thorough" if (grounding_required or fetch_content) else "quick"
    if fetch_content:
        search_depth = "thorough"
    if search_depth == "quick":
        num = min(num, 3)

    allowed_domains = _normalize_domain_values(args.get("allowed_domains"))
    blocked_domains = _normalize_domain_values(args.get("blocked_domains"))
    if allowed_domains and blocked_domains:
        return _failure_result(
            "allowed_domains and blocked_domains are mutually exclusive.",
            error_type="invalid_input",
        )
    location = _normalize_location(args.get("location"))

    search_state = _search_state(ctx)
    budget_state = _budget_state(ctx)
    sub_queries = _decompose_query(query)

    async def _run_single_query(sub_query: str) -> tuple[list[SearchResult], dict[str, Any], str | None]:
        search_allowed, search_limit = _check_and_consume_budget(
            budget_state,
            used_key="web_search_calls_made",
            limit_key="max_web_search_calls_per_turn",
            default_limit=_DEFAULT_MAX_WEB_SEARCH_CALLS_PER_TURN,
        )
        if not search_allowed:
            raise RuntimeError(_budget_exhausted_message("search", search_limit))
        from dan.tools.web_search import WebSearchProvidersExhaustedError, web_search

        try:
            raw = await web_search(
                query=sub_query,
                num_results=max(num, 3 if len(sub_queries) > 1 else num),
                search_depth=search_depth,
                allowed_domains=allowed_domains,
                blocked_domains=blocked_domains,
                location=location,
                max_provider_searches=max(1, 4 // max(len(sub_queries), 1)),
            )
        except WebSearchProvidersExhaustedError as exc:
            raise RuntimeError(
                f"Web search failed after fallback attempts: {_format_provider_failures(exc.provider_failures)}"
            ) from exc

        results = [
            _search_result_from_item(
                item,
                source_query=sub_query,
                fallback_provider=str(raw.get("provider", "") or ""),
            )
            for item in (raw.get("results") or [])
        ]
        results.sort(
            key=lambda result: (
                -_score_result(sub_query, result),
                result.credibility_tier != "A",
                result.credibility_tier != "B",
            )
        )

        reformulated: str | None = None
        if _thin_results(results):
            reformulated = _reformulate_query(sub_query)
            if reformulated:
                search_allowed, search_limit = _check_and_consume_budget(
                    budget_state,
                    used_key="web_search_calls_made",
                    limit_key="max_web_search_calls_per_turn",
                    default_limit=_DEFAULT_MAX_WEB_SEARCH_CALLS_PER_TURN,
                )
                if not search_allowed:
                    reformulated = None
                else:
                    retry_raw = await web_search(
                        query=reformulated,
                        num_results=max(num, 3),
                        search_depth=search_depth,
                        allowed_domains=allowed_domains,
                        blocked_domains=blocked_domains,
                        location=location,
                        max_provider_searches=max(1, 4 // max(len(sub_queries), 1)),
                    )
                    retry_results = [
                        _search_result_from_item(
                            item,
                            source_query=reformulated,
                            fallback_provider=str(retry_raw.get("provider", "") or ""),
                        )
                        for item in (retry_raw.get("results") or [])
                    ]
                    retry_results.sort(
                        key=lambda result: (
                            -_score_result(reformulated, result),
                            result.credibility_tier != "A",
                            result.credibility_tier != "B",
                        )
                    )
                    if retry_results:
                        raw = retry_raw
                        results = retry_results
        return results, raw, reformulated

    try:
        query_runs = await asyncio.gather(*[_run_single_query(sub_query) for sub_query in sub_queries])
    except ImportError:
        return _failure_result(
            "Web search is not available. Set DAN_TAVILY_API_KEY, DAN_SERPER_API_KEY, DAN_BRAVE_API_KEY, or install duckduckgo-search.",
            error_type="provider_unavailable",
        )
    except RuntimeError as exc:
        return _failure_result(str(exc), error_type="provider_error", retryable=True)
    except Exception as exc:
        error_type, retryable = _classify_network_exception(exc)
        return _failure_result(
            f"Web search failed: {exc}",
            error_type=error_type,
            retryable=retryable,
        )

    all_provider_failures: list[dict[str, Any]] = []
    provider_names: list[str] = []
    reformulations: list[str] = []
    grouped_results: list[list[SearchResult]] = []
    cache_hit = False
    for results, raw, reformulated in query_runs:
        grouped_results.append(results)
        provider = str(raw.get("provider", "") or "").strip()
        if provider and provider not in provider_names:
            provider_names.append(provider)
        for item in raw.get("providers") or []:
            provider_name = str(item or "").strip()
            if provider_name and provider_name not in provider_names:
                provider_names.append(provider_name)
        all_provider_failures.extend(list(raw.get("provider_failures") or []))
        cache_hit = cache_hit or bool(raw.get("cache_hit"))
        if reformulated:
            reformulations.append(reformulated)

    merged_results: list[SearchResult] = []
    canonical_seen: set[str] = set()
    offset = 0
    while len(merged_results) < num:
        emitted = False
        for group in grouped_results:
            if offset >= len(group):
                continue
            candidate = group[offset]
            key = candidate.canonical_url or candidate.url
            if key and key in canonical_seen:
                continue
            if key:
                canonical_seen.add(key)
            merged_results.append(candidate)
            emitted = True
            if len(merged_results) >= num:
                break
        if not emitted:
            break
        offset += 1

    if not merged_results:
        return CapabilityResult(success=True, message=f'No web results found for "{query}".')

    turn_seen_urls = set(search_state.get("turn_seen_urls") or [])
    session_seen_urls = set(search_state.get("session_seen_order") or [])
    novel_results: list[SearchResult] = []
    duplicate_results: list[SearchResult] = []
    for result in merged_results:
        key = result.canonical_url or result.url
        if key and key in turn_seen_urls:
            duplicate_results.append(result)
        else:
            novel_results.append(result)
        result.seen_in_recent_searches = bool(key and key in session_seen_urls)
    if novel_results:
        filtered_results = (novel_results + duplicate_results)[:num]
        duplicate_filtered_count = max(len(merged_results) - len(filtered_results), 0)
    else:
        filtered_results = merged_results[:num]
        duplicate_filtered_count = 0
    for index, result in enumerate(filtered_results, start=1):
        result.index = index
    filtered_canonical_urls = [result.canonical_url or result.url for result in filtered_results]
    search_state["turn_seen_urls"] = list(dict.fromkeys(list(turn_seen_urls) + filtered_canonical_urls))
    _append_recent_urls(search_state, filtered_canonical_urls)

    fetch_records: list[dict[str, Any]] = []
    grounded_count = 0
    fetch_attempts_made = 0
    fetch_target_count = min(_MAX_AUTO_FETCH_RESULTS, len(filtered_results))
    if fetch_content:
        for result in filtered_results:
            if grounded_count >= fetch_target_count:
                break
            if fetch_attempts_made >= _MAX_AUTO_FETCH_ATTEMPTS:
                break
            allowed, fetch_limit = _check_and_consume_budget(
                budget_state,
                used_key="web_fetch_attempts_made",
                limit_key="max_web_fetch_attempts_per_turn",
                default_limit=_DEFAULT_MAX_WEB_FETCH_ATTEMPTS_PER_TURN,
            )
            if not allowed:
                fetch_records.append({
                    "index": result.index,
                    "url": result.url,
                    "success": False,
                    "error": _budget_exhausted_message("fetch", fetch_limit),
                    "fetched_content": f"[Fetch failed: {_budget_exhausted_message('fetch', fetch_limit)}]",
                })
                break
            fetch_attempts_made += 1
            fetch_record = await _fetch_grounding_excerpt(result, query)
            result.fetched_content = str(fetch_record.get("fetched_content") or "").strip() or None
            fetch_records.append(fetch_record)
            if fetch_record.get("success"):
                grounded_count += 1

    result_set = SearchResultSet(
        query=query,
        results=filtered_results,
        provider=provider_names[0] if provider_names else "",
        providers=provider_names,
        cache_hit=cache_hit,
        provider_failures=all_provider_failures,
        grounded_result_count=grounded_count,
        sub_queries=sub_queries if len(sub_queries) > 1 else None,
        fetch_attempts_made=fetch_attempts_made,
        fetch_target_count=fetch_target_count,
    )

    lines = [f'Web search results for "{query}"']
    header_parts: list[str] = []
    if len(result_set.providers) > 1:
        header_parts.append(f"providers: {', '.join(result_set.providers)}")
    elif result_set.provider:
        header_parts.append(f"provider: {result_set.provider}")
    if result_set.cache_hit:
        header_parts.append("cache hit")
    if header_parts:
        lines[0] += f" ({'; '.join(header_parts)})"
    fallback_note = _format_provider_failures(result_set.provider_failures)
    if fallback_note:
        lines.append(f"Fallbacks: {fallback_note}")
    if result_set.sub_queries:
        for idx, sub_query in enumerate(result_set.sub_queries, start=1):
            lines.append(f"Sub-query {idx}: {sub_query}")
    if reformulations:
        lines.append(f"Reformulated thin-result search as: {', '.join(reformulations)}")
    if duplicate_filtered_count:
        lines.append(f"({duplicate_filtered_count} duplicate URLs filtered from previous search)")
    lines.append("Results are numbered for citation; fetched excerpts are the strongest evidence.")
    lines.append("")
    for result in result_set.results:
        lines.append(_format_search_result(result))
        lines.append("")

    if fetch_content:
        lines.append(
            f"Fetched {result_set.grounded_result_count}/{result_set.fetch_target_count} target pages; "
            f"{result_set.fetch_attempts_made} attempts made."
        )
        if fetch_records:
            lines.append("")
            lines.append("Fetched page excerpts (top results fetched in parallel):")
            for record in fetch_records:
                if record.get("success"):
                    lines.append(
                        f"\n[{record['index']}] Fetched content from {record['url']}\n{record.get('content', '')}"
                    )
                else:
                    lines.append(
                        f"\n[{record['index']}] Fetch failed for {record['url']}: {record.get('error', 'unknown error')}"
                    )

    payload: dict[str, Any] = {
        "query": query,
        "results": [result.model_dump(mode="python") for result in result_set.results],
        "count": len(result_set.results),
        "provider": result_set.provider,
        "providers": result_set.providers,
        "cache_hit": result_set.cache_hit,
        "provider_failures": result_set.provider_failures,
        "fetch_content_requested": fetch_content,
        "grounded_result_count": result_set.grounded_result_count,
        "fetched_results": fetch_records,
        "search_result_set": result_set.model_dump(mode="python"),
    }
    if os.environ.get("DAN_NATIVE_CITATIONS", "0").strip() == "1":
        payload["anthropic_tool_result_content"] = _build_anthropic_search_blocks(result_set)

    return CapabilityResult(success=True, message="\n".join(lines).strip(), data=payload)


async def handle_web_fetch(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    url = args.get("url", "").strip()
    if not url:
        return CapabilityResult(success=False, message="No URL provided.")
    budget_state = _budget_state(ctx)
    allowed, fetch_limit = _check_and_consume_budget(
        budget_state,
        used_key="web_fetch_attempts_made",
        limit_key="max_web_fetch_attempts_per_turn",
        default_limit=_DEFAULT_MAX_WEB_FETCH_ATTEMPTS_PER_TURN,
    )
    if not allowed:
        return _failure_result(
            _budget_exhausted_message("fetch", fetch_limit),
            error_type="resource_exhausted",
        )
    extract_only = (args.get("extract_only") or "").strip()
    try:
        from dan.tools.web_fetch import web_fetch
        result = await web_fetch(url=url)
        content = result.get("content", "")
        content = _sanitize_web_content(content)

        if extract_only:
            pat = _re.compile(_re.escape(extract_only), _re.IGNORECASE)
            lines = content.splitlines()
            kept: list[str] = []
            for i, line in enumerate(lines):
                if pat.search(line):
                    lo = max(0, i - 2)
                    hi = min(len(lines) - 1, i + 2)
                    for j in range(lo, hi + 1):
                        if lines[j] not in kept[-5:]:
                            kept.append(lines[j])
                    kept.append("")
            if kept:
                content = f"Extracted lines matching '{extract_only}':\n\n" + "\n".join(kept)
            else:
                content = f"No content matching '{extract_only}' found on this page."

        if len(content) > _FILE_READ_MAX:
            content = content[:_FILE_READ_MAX] + "\n\n[truncated]"
        header = _format_fetch_header(result, requested_url=url)
        message = header if not content else f"{header}\n\n{content}"
        return CapabilityResult(success=True, message=message, data=result)
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code if exc.response is not None else 0
        content_type = exc.response.headers.get("content-type", "") if exc.response is not None else ""
        requested = str(exc.request.url) if exc.request is not None else url
        retryable = status_code == 429 or status_code >= 500
        error_type = "provider_error" if retryable or status_code >= 500 else "http_error"
        return _failure_result(
            f"Failed to fetch URL (HTTP {status_code}) from {requested}",
            error_type=error_type,
            retryable=retryable,
            data={
                "url": requested,
                "status_code": status_code,
                "content_type": content_type,
            },
        )
    except Exception as exc:
        error_type, retryable = _classify_network_exception(exc)
        return _failure_result(
            f"Failed to fetch URL: {exc}",
            error_type=error_type,
            retryable=retryable,
        )


async def handle_http_request(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    url = args.get("url", "").strip()
    if not url:
        return CapabilityResult(success=False, message="No URL provided.")
    try:
        from dan.tools.http_request import http_request
        result = await http_request(
            url=url,
            method=args.get("method", "GET"),
            headers=args.get("headers", {}),
            body=args.get("body", ""),
        )
        body = result.get("body", "")
        if len(body) > _FILE_READ_MAX:
            body = body[:_FILE_READ_MAX] + "\n\n[truncated]"
        status = result.get("status_code", 0)
        error_type: str | None = None
        retryable = False
        if status == 429 or status >= 500:
            error_type = "provider_error"
            retryable = True
        elif status >= 400:
            error_type = "http_error"
        return CapabilityResult(
            success=200 <= status < 400,
            message=f"HTTP {status}\n\n{body}",
            data=result,
            retryable=retryable,
            error_type=error_type,
        )
    except Exception as exc:
        error_type, retryable = _classify_network_exception(exc)
        return _failure_result(
            f"HTTP request failed: {exc}",
            error_type=error_type,
            retryable=retryable,
        )
