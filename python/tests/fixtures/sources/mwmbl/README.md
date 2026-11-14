# mwmbl fixture notes

Fixtures are hand-written against the upstream v2 schema verified at
mwmbl repository commit cce49fa (SearXNG-compatible SearchResponse
object; results[].title/content are plain strings; auth header is
X-API-Key; invalid keys get HTTP 401). No live response has been
captured; the user's S0 probe must confirm the shape before any
enablement decision (see docs/sources/mwmbl.md).
