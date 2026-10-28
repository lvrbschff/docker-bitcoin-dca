# Changelog

## v0.0.2 — 2026-10-28

- Added configurable live-order verification for Kraken and Bitvavo via `ORDER_STATUS_CHECK_SECONDS` in Docker Compose. Open or unresolved orders block further purchases; final fills are recorded after the order ends.
- Added the estimated next purchase time and a live countdown to the dashboard.
- Improved Kraken read-request retries and endpoint logging without automatically resending orders or withdrawals.
- Improved mobile dashboard layouts and reduced date-range typography for clearer display on small screens.
- Combined the order-status check delay with the startup configuration log.
