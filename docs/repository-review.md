# Repository review

Updated 19 October 2026 after rebuilding the observation warehouse from bundled archives. This review does not independently verify source accuracy or current platform conditions.

The dashboard supports historical assortment, source labels, pack prices and explicitly recorded stock. It does not measure sales, demand, market share or current inventory. Ask displays reviewed SQL reports and fixed text; model-written factual prose never reaches the answer.

Remaining data-quality work:

- Preserve full source category hierarchies and introduce reviewed taxonomy mappings. Internal name-based classifications remain provisional; dashboard reports use raw labels.
- Make pack parsing unit-aware before using internal product matches for cross-platform comparisons. The current parser conflates mass and volume; dashboard prices stay per pack.
- Make file ingestion atomic and retryable. A failed load can leave a registered hash and partial writes. Add rejected-row and duplicate reconciliation.
- Introduce explicit observation-time metadata rather than identifying archive timestamps by platform and source type.
- Include city and geographic granularity in location keys to prevent same-named localities from colliding.
- Support reviewed corrections to listing metadata. Existing listing updates currently change first/last seen timestamps only.
- Keep source errors and sampling gaps distinct from business conclusions. Missing labels and stock remain unknown.

CI builds the warehouse and tests report semantics, read-only SQL, report selection, rejected model output, filters and dashboard views. Model responses are tested with scripted clients; no live API call is part of these checks.
