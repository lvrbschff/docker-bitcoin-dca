# Changelog

## Unreleased

- Renew Kraken HTTP connections after idle periods and before retrying failed reads; log successful recovery and request durations.
- Share Kraken balance responses only within a bot check for up to five seconds, invalidating them after financial submissions and between checks.
- Add regression tests for connection renewal, fresh retry nonces, balance freshness, and single-attempt order and withdrawal submissions.

- Renamed the application and dashboard to Bitcoin DCA Bot.
- Fixed purchases and withdrawals to Bitcoin and removed the asset-selection setting.
- Excluded older non-Bitcoin activity from dashboard totals, scheduling history, and Parqet exports while preserving stored records.

## v0.0.2 — 2026-10-28

- Added configurable live-order verification for Kraken and Bitvavo via `ORDER_STATUS_CHECK_SECONDS` in Docker Compose. Open or unresolved orders block further purchases; final fills are recorded after the order ends.
- Added the estimated next purchase time and a live countdown to the dashboard.
- Improved Kraken read-request retries and endpoint logging without automatically resending orders or withdrawals.
- Improved mobile dashboard layouts and reduced date-range typography for clearer display on small screens.
- Combined the order-status check delay with the startup configuration log.
