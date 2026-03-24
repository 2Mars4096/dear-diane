"""Compatibility facade for the neutral domain-taxonomy seam."""

from dan.domain_taxonomy import (
    format_domain_label,
    normalize_domain_keyword_map,
    normalize_domain_list,
    normalize_domain_name,
    preserve_domain_labels,
)

__all__ = [
    "format_domain_label",
    "normalize_domain_keyword_map",
    "normalize_domain_list",
    "normalize_domain_name",
    "preserve_domain_labels",
]
