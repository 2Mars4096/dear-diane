"""Helpers and concrete providers for staged worker-core context acquisition."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote

from dan.worker.core.capabilities import CapabilityManifest, capability_manifest_from_descriptor
from dan.worker.core.contracts import (
    AcquisitionFamily,
    AcquisitionPolicy,
    AcquisitionSelection,
    AcquisitionSource,
    DiscoveryCatalog,
    DiscoveryItem,
    ExpandedContext,
    MemoryRecord,
    TrustLabel,
)
from dan.worker.core.interfaces import DiscoveryRequest, DiscoveryResponse, ExpansionRequest, ExpansionResponse

_STOP_WORDS = {
    "a",
    "an",
    "and",
    "at",
    "for",
    "from",
    "if",
    "in",
    "into",
    "of",
    "on",
    "or",
    "the",
    "to",
    "use",
    "with",
}


def family_name(family: AcquisitionFamily | str) -> str:
    """Return the canonical string name for a source family."""

    return family.value if isinstance(family, AcquisitionFamily) else str(family)


def build_ref_id(family: AcquisitionFamily | str, source_id: str, item_id: str) -> str:
    """Build a stable ref for catalog items and expanded context."""

    return f"{family_name(family)}:{quote(source_id, safe='')}:{quote(item_id, safe='')}"


def merge_acquisition_policy(
    worker_policy: AcquisitionPolicy | None,
    request_policy: AcquisitionPolicy | None,
) -> AcquisitionPolicy:
    """Merge worker defaults with request-scoped overrides."""

    resolved = AcquisitionPolicy()
    if worker_policy is not None:
        resolved = resolved.model_copy(update=worker_policy.model_dump(exclude_none=True))
    if request_policy is not None:
        resolved = resolved.model_copy(update=request_policy.model_dump(exclude_none=True))
    return resolved


def selection_limit(source: AcquisitionSource, policy: AcquisitionPolicy) -> int:
    """Return the effective selection budget for a source."""

    if source.max_selected_items is not None:
        return source.max_selected_items
    return policy.max_selected_items_per_source


def expansion_limit(source: AcquisitionSource, policy: AcquisitionPolicy) -> int:
    """Return the effective expansion budget for a source."""

    if source.max_expanded_items is not None:
        return source.max_expanded_items
    return policy.max_expanded_items_per_source


def ensure_default_sources(worker_id: str, tool_ids: list[str], sources: list[AcquisitionSource]) -> list[AcquisitionSource]:
    """Ensure tool-enabled workers still expose a discoverable tool catalog."""

    if not tool_ids:
        return list(sources)
    resolved = list(sources)
    if any(family_name(source.family) == AcquisitionFamily.TOOL_CATALOG.value for source in resolved):
        return resolved
    resolved.append(
        AcquisitionSource(
            source_id=f"worker-tools::{worker_id}",
            family=AcquisitionFamily.TOOL_CATALOG,
            label="Worker tool catalog",
            selectors=list(tool_ids),
            metadata={"tool_ids": list(tool_ids)},
        )
    )
    return resolved


def select_catalog_items(
    *,
    task: str,
    source: AcquisitionSource,
    catalog: DiscoveryCatalog,
    policy: AcquisitionPolicy,
    preferred_item_ids: set[str] | None = None,
    excluded_refs: set[str] | None = None,
) -> list[AcquisitionSelection]:
    """Choose a compact subset of catalog items for expansion."""

    preferred_item_ids = preferred_item_ids or set()
    excluded_refs = excluded_refs or set()
    limit = selection_limit(source, policy)
    if limit <= 0:
        return []

    query_terms = _tokenize(" ".join(part for part in [task, source.query or "", *source.selectors] if part))
    scored: list[tuple[int, DiscoveryItem, str]] = []
    for item in catalog.items:
        if item.ref_id in excluded_refs:
            continue
        score, reason = _score_item(item=item, query_terms=query_terms, preferred_item_ids=preferred_item_ids)
        if score > 0:
            scored.append((score, item, reason))

    scored.sort(key=lambda entry: (-entry[0], entry[1].title.lower(), entry[1].ref_id))
    return [
        AcquisitionSelection(
            ref_id=item.ref_id,
            source_id=item.source_id,
            family=item.family,
            reason=reason,
            selected_by="policy",
        )
        for _, item, reason in scored[:limit]
    ]


class LocalAcquisitionProvider:
    """Standalone acquisition provider for tool catalogs and local file inventories."""

    def __init__(self, *, tool_catalogs: dict[str, object] | None = None) -> None:
        self._tool_catalogs = dict(tool_catalogs or {})

    async def discover(self, request: DiscoveryRequest) -> DiscoveryResponse:
        family = family_name(request.source.family)
        if family == AcquisitionFamily.TOOL_CATALOG.value:
            return DiscoveryResponse(catalog=self._discover_tools(request))
        if family == AcquisitionFamily.FILE_INVENTORY.value:
            return DiscoveryResponse(catalog=self._discover_files(request))
        if family == AcquisitionFamily.MEMORY_CATALOG.value:
            return DiscoveryResponse(catalog=self._discover_memory(request))
        raise ValueError(f"Unsupported acquisition family: {family}")

    async def expand(self, request: ExpansionRequest) -> ExpansionResponse:
        family = family_name(request.source.family)
        if family == AcquisitionFamily.TOOL_CATALOG.value:
            return ExpansionResponse(expanded_context=self._expand_tools(request))
        if family == AcquisitionFamily.FILE_INVENTORY.value:
            return ExpansionResponse(expanded_context=self._expand_files(request))
        if family == AcquisitionFamily.MEMORY_CATALOG.value:
            return ExpansionResponse(expanded_context=self._expand_memory(request))
        raise ValueError(f"Unsupported acquisition family: {family}")

    def _discover_tools(self, request: DiscoveryRequest) -> DiscoveryCatalog:
        manifests = self._resolve_tool_manifests(request.source)
        limit = request.source.discover_limit or request.policy.discover_limit
        items = [
            DiscoveryItem(
                ref_id=build_ref_id(AcquisitionFamily.TOOL_CATALOG, request.source.source_id, manifest.capability_id),
                source_id=request.source.source_id,
                family=AcquisitionFamily.TOOL_CATALOG.value,
                item_id=manifest.capability_id,
                title=manifest.title,
                summary=manifest.purpose,
                locator=manifest.capability_id,
                metadata=manifest.compact().model_dump(mode="json"),
            )
            for manifest in manifests[:limit]
        ]
        truncated = len(manifests) > limit
        cursor = f"offset:{limit}" if truncated else None
        return DiscoveryCatalog(
            source_id=request.source.source_id,
            family=AcquisitionFamily.TOOL_CATALOG.value,
            items=items,
            summary=f"{len(items)} tool catalog entries discovered",
            cursor=cursor,
            total_available=len(manifests),
            truncated=truncated,
        )

    def _expand_tools(self, request: ExpansionRequest) -> list[ExpandedContext]:
        manifests_by_id = {
            manifest.capability_id: manifest for manifest in self._resolve_tool_manifests(request.source)
        }
        items_by_ref = {item.ref_id: item for item in (request.catalog.items if request.catalog is not None else [])}
        expanded: list[ExpandedContext] = []
        for selection in request.selections[: expansion_limit(request.source, request.policy)]:
            item = items_by_ref.get(selection.ref_id)
            tool_id = item.item_id if item is not None else selection.ref_id.rsplit(":", 1)[-1]
            manifest = manifests_by_id.get(tool_id)
            if manifest is None:
                manifest = capability_manifest_from_descriptor(tool_id)
            expanded.append(
                ExpandedContext(
                    ref_id=selection.ref_id,
                    source_id=selection.source_id,
                    family=selection.family,
                    title=manifest.title,
                    content=manifest.model_dump(mode="json"),
                    summary=manifest.purpose or manifest.title,
                    trust_label=TrustLabel.AUTHORITATIVE,
                    source=request.source.label or request.source.source_id,
                    metadata={"tool_id": tool_id, "capability_family": manifest.family},
                )
            )
        return expanded

    def _discover_files(self, request: DiscoveryRequest) -> DiscoveryCatalog:
        roots = _resolve_roots(request.source)
        files = list(_iter_files(request.source))
        query_text = " ".join(part for part in [request.request.task, request.source.query or "", *request.source.selectors] if part)
        query_terms = _tokenize(query_text)
        ranked = sorted(
            files,
            key=lambda path: (
                -_path_score(_display_path(path, roots), query_terms, request.source.selectors),
                _display_path(path, roots).lower(),
            ),
        )
        limit = request.source.discover_limit or request.policy.discover_limit
        items = [
            DiscoveryItem(
                ref_id=build_ref_id(AcquisitionFamily.FILE_INVENTORY, request.source.source_id, str(path)),
                source_id=request.source.source_id,
                family=AcquisitionFamily.FILE_INVENTORY.value,
                item_id=str(path),
                title=_display_path(path, roots),
                summary=f"{path.suffix or 'file'} file",
                locator=str(path),
                metadata={"size_bytes": path.stat().st_size},
            )
            for path in ranked[:limit]
        ]
        truncated = len(ranked) > limit
        cursor = f"offset:{limit}" if truncated else None
        return DiscoveryCatalog(
            source_id=request.source.source_id,
            family=AcquisitionFamily.FILE_INVENTORY.value,
            items=items,
            summary=f"{len(items)} file inventory entries discovered",
            cursor=cursor,
            total_available=len(ranked),
            truncated=truncated,
            metadata={"roots": [str(root) for root in roots]},
        )

    def _expand_files(self, request: ExpansionRequest) -> list[ExpandedContext]:
        items_by_ref = {item.ref_id: item for item in (request.catalog.items if request.catalog is not None else [])}
        max_chars = request.source.metadata.get("max_content_chars", request.policy.max_expanded_content_chars)
        expanded: list[ExpandedContext] = []
        for selection in request.selections[: expansion_limit(request.source, request.policy)]:
            item = items_by_ref.get(selection.ref_id)
            if item is None or not item.locator:
                continue
            path = Path(item.locator)
            content = path.read_text(encoding="utf-8", errors="replace")
            truncated = False
            if max_chars is not None and len(content) > max_chars:
                content = f"{content[:max_chars]}\n...[truncated]"
                truncated = True
            expanded.append(
                ExpandedContext(
                    ref_id=selection.ref_id,
                    source_id=selection.source_id,
                    family=selection.family,
                    title=item.title,
                    content=content,
                    summary=item.title,
                    trust_label=TrustLabel.AUTHORITATIVE,
                    source=str(path),
                    metadata={"path": str(path), "truncated": truncated},
                )
            )
        return expanded

    def _discover_memory(self, request: DiscoveryRequest) -> DiscoveryCatalog:
        records = self._resolve_memory_records(request.source)
        limit = request.source.discover_limit or request.policy.discover_limit
        items = [record.as_discovery_item(request.source.source_id) for record in records[:limit]]
        truncated = len(records) > limit
        cursor = f"offset:{limit}" if truncated else None
        layer = str(request.source.metadata.get("memory_layer") or "memory")
        return DiscoveryCatalog(
            source_id=request.source.source_id,
            family=AcquisitionFamily.MEMORY_CATALOG.value,
            items=items,
            summary=f"{len(items)} {layer} memory entries discovered",
            cursor=cursor,
            total_available=len(records),
            truncated=truncated,
        )

    def _expand_memory(self, request: ExpansionRequest) -> list[ExpandedContext]:
        records_by_ref = {
            record.ref_id: record for record in self._resolve_memory_records(request.source)
        }
        expanded: list[ExpandedContext] = []
        for selection in request.selections[: expansion_limit(request.source, request.policy)]:
            record = records_by_ref.get(selection.ref_id)
            if record is None:
                continue
            expanded.append(record.as_expanded_context(request.source.source_id))
        return expanded

    def _resolve_tool_manifests(self, source: AcquisitionSource) -> list[CapabilityManifest]:
        descriptors = self._resolve_tool_descriptors(source)
        manifests = [
            capability_manifest_from_descriptor(
                str(descriptor.get("id") or descriptor.get("name") or ""),
                descriptor,
            )
            for descriptor in descriptors
            if str(descriptor.get("id") or descriptor.get("name") or "").strip()
        ]
        allowed_families = {str(item).strip().lower() for item in source.metadata.get("capability_families", []) if str(item).strip()}
        if allowed_families:
            manifests = [manifest for manifest in manifests if manifest.family.lower() in allowed_families]
        return manifests

    def _resolve_tool_descriptors(self, source: AcquisitionSource) -> list[dict[str, object]]:
        raw_catalog = source.metadata.get("tools")
        if raw_catalog is None:
            raw_catalog = self._tool_catalogs.get(source.source_id)
        if raw_catalog is None:
            from dan.tools import get_all_tools

            available = get_all_tools()
            tool_ids = list(source.metadata.get("tool_ids", []) or available.keys())
            raw_catalog = [
                {"id": tool_id, **dict(available.get(tool_id, (None, {}))[1] or {})}
                for tool_id in tool_ids
            ]
        if isinstance(raw_catalog, dict):
            return [
                {"id": tool_id, **(payload if isinstance(payload, dict) else {"summary": str(payload)})}
                for tool_id, payload in raw_catalog.items()
            ]
        descriptors: list[dict[str, object]] = []
        for entry in raw_catalog:
            if isinstance(entry, str):
                descriptors.append({"id": entry})
            elif isinstance(entry, CapabilityManifest):
                descriptors.append({"id": entry.capability_id, **entry.model_dump(mode="json")})
            elif isinstance(entry, dict):
                descriptors.append(dict(entry))
        return descriptors

    def _resolve_memory_records(self, source: AcquisitionSource) -> list[MemoryRecord]:
        raw_records = list(source.metadata.get("records") or [])
        records: list[MemoryRecord] = []
        for entry in raw_records:
            if isinstance(entry, MemoryRecord):
                records.append(entry)
            elif isinstance(entry, dict):
                records.append(MemoryRecord.model_validate(entry))
        return records


def _tokenize(text: str) -> list[str]:
    tokens = []
    for raw in re.findall(r"[a-z0-9_./-]+", text.lower()):
        if len(raw) <= 1 or raw in _STOP_WORDS:
            continue
        tokens.append(raw)
    return tokens


def _score_item(
    *,
    item: DiscoveryItem,
    query_terms: list[str],
    preferred_item_ids: set[str],
) -> tuple[int, str]:
    score = 0
    reasons: list[str] = []
    haystacks = [
        item.item_id.lower(),
        item.title.lower(),
        item.summary.lower(),
        (item.locator or "").lower(),
    ]
    if item.item_id in preferred_item_ids or (item.locator and item.locator in preferred_item_ids):
        score += 100
        reasons.append(f"configured item '{item.item_id}'")

    matched_terms = sorted({term for term in query_terms if any(term in haystack for haystack in haystacks)})
    if matched_terms:
        score += len(matched_terms)
        reasons.append("matched terms: " + ", ".join(matched_terms[:4]))

    return score, "; ".join(reasons) if reasons else "policy-selected top catalog match"


def _resolve_roots(source: AcquisitionSource) -> list[Path]:
    roots = []
    raw_roots = list(source.metadata.get("roots") or [])
    if not raw_roots and source.metadata.get("root"):
        raw_roots = [source.metadata["root"]]
    for raw_root in raw_roots:
        path = Path(str(raw_root))
        if path.exists():
            roots.append(path)
    return roots


def _iter_files(source: AcquisitionSource) -> list[Path]:
    resolved: dict[str, Path] = {}
    raw_paths = list(source.metadata.get("paths") or [])
    for raw_path in raw_paths:
        path = Path(str(raw_path))
        if path.is_file():
            resolved[str(path)] = path
        elif path.is_dir():
            for child in path.rglob("*"):
                if child.is_file():
                    resolved[str(child)] = child
    for root in _resolve_roots(source):
        if root.is_file():
            resolved[str(root)] = root
            continue
        for child in root.rglob("*"):
            if child.is_file():
                resolved[str(child)] = child
    suffixes = {str(value).lower() for value in source.metadata.get("suffixes", [])}
    files = list(resolved.values())
    if not suffixes:
        return files
    return [path for path in files if path.suffix.lower() in suffixes]


def _display_path(path: Path, roots: list[Path]) -> str:
    for root in roots:
        try:
            return str(path.relative_to(root))
        except ValueError:
            continue
    return str(path)


def _path_score(display_path: str, query_terms: list[str], selectors: list[str]) -> int:
    lower_path = display_path.lower()
    score = 0
    for selector in selectors:
        selector_lower = selector.lower()
        if selector_lower and selector_lower in lower_path:
            score += 20
    for term in query_terms:
        if term in lower_path:
            score += 1
    return score
