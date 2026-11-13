# mwmbl_api offline fixtures

`search_ok.json` and `search_empty.json` encode the documented Mwmbl v2 search
response shape: a top-level JSON array of objects with `title`, `url` and
`extract` members (https://developer.mwmbl.org/). `rate_limited.txt` is a
placeholder 429 body; the adapter raises `rate_limited` from the HTTP status
before parsing, so the exact 429 body bytes do not matter.

These fixtures were hand-recorded from the documentation for the CL-06 offline
batch: no network access happened here, and no live response was captured. The
user's S0 probe (docs/S0_PROBE.md) must confirm the real response shape before
the source is enabled; if it differs, update these fixtures and
`Mwmbl.parse` together.
