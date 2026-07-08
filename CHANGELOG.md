# Changelog

## v2

- Honor environment-driven configuration for RPC URL and bounded indexing.
- Add `generate_report.py` to read `indexer_cache.db` in read-only mode and write `report/metrics_output.json`.
- Add markdown templates under `report/templates` so root docs can be regenerated from current metrics instead of shipping stale literals.
- Replace hardcoded report figures in `README.md`, `RESEARCH.md`, and `VALIDATION.md` with template placeholders.
- Preserve safe failure behavior: if `indexer_cache.db` is absent, report generation exits non-zero and does not modify root docs.
