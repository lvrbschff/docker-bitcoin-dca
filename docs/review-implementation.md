# Code review implementation — 2026-10-28

Implemented against main `3b6e415`, covering review points 1–12 and 14. Point 13 (publication workflow changes) is excluded.

| Point | Result | Main implementation |
| --- | --- | --- |
| 1 | Explicit, documented exchange rejections clear pending submissions; transport ambiguity retains pending state. Financial submissions are never blindly replayed. | `exchange_errors.py`, adapters, `bot.py` |
| 2 | Remaining BTC subtracts gross debits including network fees; wallet/export quantities remain net. Failed withdrawals debit zero. | `activity.py`, `analytics.py` |
| 3 | Submitted withdrawals appear immediately, then reconcile against BTC history. Only uniquely matched completed withdrawals enter CSV exports. | `withdrawals.py`, `reporting.py` |
| 4 | Selected-market metadata has a bounded cache, refreshes before purchase decisions and invalidates after rule rejections. | Both adapters |
| 5 | Planning includes estimated taker fees and a 0.5% buffer; simulated spending includes actual simulated fees. | `bot.py` |
| 6 | Reports aggregate original currencies separately and convert them separately for display. New records preserve exchange provenance. | `analytics.py`, `web_ui.py` |
| 7 | Initial and changed interval selections populate both dates through the same function. | `web_ui.py` |
| 8 | Balance and price reads share a short cycle scope, invalidated on writes. Simulation recomputes scheduling from existing data. | `read_cache.py`, adapters, `bot.py` |
| 9 | Only required public price pages load, concurrently when two are needed. Cache lifetime is 30 seconds; visibly marked fallback expires at 300 seconds. | `price_cache.py`, `web_ui.py` |
| 10 | SQLite migration retains JSON sources and metadata. Indexed queries transfer one requested page; exact Decimal aggregates and page data share a read transaction. | `activity.py` |
| 11 | A bounded single-worker queue serializes account actions. HTTP returns a job ID promptly, with session-bound status polling. | `worker.py`, `web_ui.py` |
| 12 | Periodic account checks remain independent of purchase due time. Stable inputs retain due time; completed purchases reset the planning anchor. Heartbeat, supervisor and Docker healthcheck detect worker loss. | `bot.py`, `worker.py`, `healthcheck.py`, `Dockerfile` |
| 14 | Unused account/deposit/history paths removed; withdrawal history retained for verification. Daily FX cache and indexed date lookup reduce repeated work. One canonical fictional preview has a reproducible generator. | Adapters, `reporting.py`, `docs/generate_preview.py` |

## Migration and verification

Back up the named web UI volume and `./state` before updating. The first ledger access imports legacy JSON once into `activity.sqlite3`, leaving the JSON untouched. Older withdrawals without a confirmed status become `legacy_unverified`: they remain visible, but are excluded from CSV until uniquely verified. Unknown historical exchange provenance is preserved as unknown; a configuration change cannot relabel it. Pending submissions remain durable in the existing state file.

Verification uses synthetic data and mocked exchange/network responses. The Python regression suite covers migration, status transitions, definitive versus ambiguous failures, gross/net accounting, budgets, market refreshes, mixed currencies, caching, worker startup and withdrawal replay after interrupted persistence. A Node regression test executes the actual dashboard JavaScript with a stubbed DOM, covering initial intervals, server pagination, currency conversion and queued action polling.

A local 50,001-record measurement returned 20 rows (4,080 bytes of activity JSON), appended one row in 0.98 ms and read the page plus exact period/global aggregates in 170.47 ms. These are single-machine measurements, not host performance guarantees. The full aggregates still scan the selected records; pagination bounds transferred rows and SQLite avoids rewriting the full ledger on append.

No real orders or withdrawals were sent. Docker build and a real browser/mobile rendering check were unavailable in this environment. The README image is explicitly an illustration, not a browser screenshot. The existing Parqet period-consolidation policy is unchanged.
