# Recorded source fixtures

Captured from public first-party sources on 2026-10-05. `aliases.json` maps readable
test names to exact HTTP requests. `raw/requests/<request hash>.json` records URLs,
POST/query parameters, timestamps, response encoding and SHA-256. Corresponding
`raw/bodies/<content hash>` files retain the exact response bytes.

These are immutable inputs for ordinary offline tests. Raw bodies are explicitly
excluded from Git text conversion; changing line endings invalidates their hashes.
The network is disabled in ordinary pytest tests. Expected fields/counts are
specified in source tests and `docs/implementation.md`, independently of parsers.

Three earlier empty-string detail requests are retained alongside the corrected
nullable requests as evidence of the registry's differing response surfaces.
Named detail aliases select the actual client-equivalent nullable requests.

`tools/capture_fixtures.py` captures missing responses. Existing named aliases are
kept frozen; fixture replacement requires separate source inspection and review of
the affected expectations. This utility is not part of scheduled production runs.
