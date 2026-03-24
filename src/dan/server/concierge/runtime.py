"""Legacy filesystem stub for the concierge runtime package.

The canonical `dan.server.concierge.runtime` module lives in the sibling
package directory at `src/dan/server/concierge/runtime/__init__.py`.
Python resolves that package for normal imports, so this file stays
intentionally import-free to avoid reintroducing the old
`server <-> server.concierge` boundary cycle during static scans.
"""
