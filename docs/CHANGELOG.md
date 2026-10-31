# Changelog

Release history based on the published [GitHub Releases](https://github.com/oli792dev/docker-bitcoin-dca/releases). Entries are newest first; dates use Europe/Berlin. Releases without notes are marked explicitly.

## Unreleased

- Unified dashboard, login, setup and settings around an Apple HIG-inspired design system with automatic light/dark appearance.
- Added system typography, consistent grouped surfaces, accessible contrast, larger control targets, keyboard skip links and reduced-motion/transparency support.
- Replaced decorative button glow with restrained interaction feedback and applied shared styling to all dialogs and credential editors.

- Removed Adaptive DCA, its settings and dashboard statistics; preserved the reorganized application packages. Existing volumes ignore the retired settings.

- Organized application modules into webpage, trading, exchanges, data and core packages; updated imports, Docker startup and health checks.


- Bounded concurrent HTTPS requests and rechecked sessions before queued manual withdrawals execute.
- Followed paginated withdrawal histories and rejected incomplete or stalled responses safely.
- Shared private atomic JSON writes, restricted ledger permissions and reused exchange connections for cosmetic settings changes.
- Excluded failed foreign-currency withdrawals from monetary currency grouping.
- Removed redundant worker startup hooks, unused legacy path placeholders and an unreachable balance branch.
- Consolidated small Python suites by domain and added regression checks before container publishing.
- Shortened forecast and manual-withdrawal notes and removed cancellation feedback from the notes.

- Persisted the expected monthly deposit in the named volume, starting empty without importing browser values.
- Vertically aligned header buttons with the dashboard title.

- Locked background scrolling while settings, withdrawal and review dialogs are open, restoring the previous position on close.
- Placed exchange and purchase currency cards in one horizontal row on mobile.

- Replaced expected TLS-handshake tracebacks with concise certificate-trust guidance; repeated failures and disconnects remain DEBUG.

- Added native HTTPS with a persistent local CA, configurable certificate addresses and automatic renewal.
- Secured session cookies, reduced sessions to 30 minutes and limited sign-in/password-confirmation attempts.
- Required current-password confirmation for sensitive exchange, withdrawal and live-trading changes.

- Added responsive horizontal scrolling with two card rows to mobile settings, sharing dashboard scrollbar and edge-fade behavior.

- Removed unused activity deletion/query helpers, the obsolete worker bootstrap and superseded withdrawal estimator.
- Consolidated report tests around the active runtime and paginated SQLite queries; removed stale imports, flags and styles.
- Avoided unchanged state rewrites and excluded documentation/tests from the Docker build context.

- Fixed the restart loop when saved settings or history exist but scheduling state is missing.
- Replaced manual account recovery with automatic startup exchange checks; new purchases and withdrawals wait for open transactions to close.
- Preserved saved configuration and recorded activity while rebuilding the purchase schedule after startup checks.
- Added separate, independently saved pages for dashboard credentials and exchange API access.
- Aligned display currency with the timezone field and shortened its timezone help text.

## [v0.5.0](https://github.com/oli792dev/docker-bitcoin-dca/releases/tag/v0.5.0) — 2026-10-30

- Added first-run setup for creating dashboard credentials and configuring the bot.
- Added a Settings dialog for exchange access, purchase parameters, withdrawal targets and dashboard options.
- Added separate credential editors and a review showing highlighted before-and-after changes.
- Added read-only API connection tests with inline feedback.
- Added controls for live trading, automatic/manual withdrawals, Parqet exports, display currency and timezone.
- Added worldwide timezone presets and contextual setting descriptions.
- Moved all persistent data into `bitcoin-dca-webui-data`, preserving settings and transaction state across deployments.
- Removed the host state bind mount and migration code.
- Made the web UI mandatory; bot settings and credentials now come exclusively from the saved web configuration.
- Added confirmation statuses to Period Activity and automatic withdrawal targets to Notes.
- Reset the Period Analysis end time to the current time on each page load.
- Expanded setup and Settings logs without exposing credentials.
- Improved mobile layouts and simplified the deployment configuration and documentation.

## [v0.4.2](https://github.com/oli792dev/docker-bitcoin-dca/releases/tag/v0.4.2) — 2026-10-30

- improvements in configuration and settings page

## [v0.4.1](https://github.com/oli792dev/docker-bitcoin-dca/releases/tag/v0.4.1) — 2026-10-30

- improving configuration page

## [v0.4.0](https://github.com/oli792dev/docker-bitcoin-dca/releases/tag/v0.4.0) — 2026-10-30

- Adding settings menu and configuration assistant

## [v0.3.5](https://github.com/oli792dev/docker-bitcoin-dca/releases/tag/v0.3.5) — 2026-10-30

- Kept scheduled account checks unchanged after manual withdrawal previews and confirmations, avoiding unnecessary purchase schedule recalculations.
- Added compact dismiss buttons to manual withdrawal and Parqet CSV export messages.
- Unified automatic withdrawal forecast subtitles and subsection spacing.
- Kept activity table height stable by filling unused page rows.

## [v0.3.4](https://github.com/oli792dev/docker-bitcoin-dca/releases/tag/v0.3.4) — 2026-10-30

- Standardized log levels and reduced routine INFO output.
- Added logs for withdrawal status changes.
- Kept temporary network failures at WARNING without stack traces.
- Preserved INFO logs for purchase scheduling and user actions.

## [v0.3.3](https://github.com/oli792dev/docker-bitcoin-dca/releases/tag/v0.3.3) — 2026-10-30

- Improved the web UI appearance.

## [v0.3.2](https://github.com/oli792dev/docker-bitcoin-dca/releases/tag/v0.3.2) — 2026-10-30

- Reworked the application architecture, moving Python runtime modules into `app/`.
- Updated Docker source paths, test entry points and architecture documentation.

## [v0.3.1](https://github.com/oli792dev/docker-bitcoin-dca/releases/tag/v0.3.1) — 2026-10-30

- Improved visibility in the web UI.

## [v0.3.0](https://github.com/oli792dev/docker-bitcoin-dca/releases/tag/v0.3.0) — 2026-10-30

Improvements in Web UI
- Added responsive horizontal scrolling with clearer scrollbars and edge indicators.
- Improved loading feedback while preserving existing values during refreshes.
- Added a styled withdrawal confirmation dialog showing destination, gross BTC, fees and net BTC.
- Remembered panel states, date ranges and activity page sizes.
- Standardized numeric alignment and missing-value formatting.

## [v0.2.9](https://github.com/oli792dev/docker-bitcoin-dca/releases/tag/v0.2.9) — 2026-10-30

- Improved responsive layouts for mobile devices.

## [v0.2.8](https://github.com/oli792dev/docker-bitcoin-dca/releases/tag/v0.2.8) — 2026-10-29

- Improved the web UI appearance.

## [v0.2.7](https://github.com/oli792dev/docker-bitcoin-dca/releases/tag/v0.2.7) — 2026-10-29

- Improved the web UI appearance.

## [v0.2.6](https://github.com/oli792dev/docker-bitcoin-dca/releases/tag/v0.2.6) — 2026-10-29

- Improved the web UI appearance.

## [v0.2.5](https://github.com/oli792dev/docker-bitcoin-dca/releases/tag/v0.2.5) — 2026-10-29

- No release notes were published.

## [v0.2.4](https://github.com/oli792dev/docker-bitcoin-dca/releases/tag/v0.2.4) — 2026-10-29

- Improved the web UI appearance.

## [v0.2.3](https://github.com/oli792dev/docker-bitcoin-dca/releases/tag/v0.2.3) — 2026-10-29

- Improved the web UI appearance.

## [v0.2.2](https://github.com/oli792dev/docker-bitcoin-dca/releases/tag/v0.2.2) — 2026-10-29

- Improved the web UI appearance.

## [v0.2.1](https://github.com/oli792dev/docker-bitcoin-dca/releases/tag/v0.2.1) — 2026-10-29

- Improved the web UI appearance.

## [v0.2.0](https://github.com/oli792dev/docker-bitcoin-dca/releases/tag/v0.2.0) — 2026-10-29

- Reworked the web UI.

## [v0.1.32](https://github.com/oli792dev/docker-bitcoin-dca/releases/tag/v0.1.32) — 2026-10-29

- Improved the web UI appearance.

## [v0.1.31](https://github.com/oli792dev/docker-bitcoin-dca/releases/tag/v0.1.31) — 2026-10-29

- Improved the web UI appearance.

## [v0.1.30](https://github.com/oli792dev/docker-bitcoin-dca/releases/tag/v0.1.30) — 2026-10-29

- Improved the web UI appearance.

## [v0.1.29](https://github.com/oli792dev/docker-bitcoin-dca/releases/tag/v0.1.29) — 2026-10-29

- Improved the automatic withdrawal date estimate in the web UI.

## [v0.1.28](https://github.com/oli792dev/docker-bitcoin-dca/releases/tag/v0.1.28) — 2026-10-29

- Improved the web UI appearance.

## [v0.1.27](https://github.com/oli792dev/docker-bitcoin-dca/releases/tag/v0.1.27) — 2026-10-29

- Improved the web UI appearance.

## [v0.1.26](https://github.com/oli792dev/docker-bitcoin-dca/releases/tag/v0.1.26) — 2026-10-29

- Improved the web UI appearance.

## [v0.1.25](https://github.com/oli792dev/docker-bitcoin-dca/releases/tag/v0.1.25) — 2026-10-29

- Added an estimated automatic withdrawal countdown to the web UI when applicable.

## [v0.1.24](https://github.com/oli792dev/docker-bitcoin-dca/releases/tag/v0.1.24) — 2026-10-29

- Improved the web UI appearance.

## [v0.1.23](https://github.com/oli792dev/docker-bitcoin-dca/releases/tag/v0.1.23) — 2026-10-29

- Improved the web UI appearance.

## [v0.1.22](https://github.com/oli792dev/docker-bitcoin-dca/releases/tag/v0.1.22) — 2026-10-29

- Improved the web UI appearance.

## [v0.1.21](https://github.com/oli792dev/docker-bitcoin-dca/releases/tag/v0.1.21) — 2026-10-29

- No release notes were published.

## [v0.1.20](https://github.com/oli792dev/docker-bitcoin-dca/releases/tag/v0.1.20) — 2026-10-28

- Improved the web UI appearance.

## [v0.1.19](https://github.com/oli792dev/docker-bitcoin-dca/releases/tag/v0.1.19) — 2026-10-28

- Improved the web UI appearance.

## [v0.1.18](https://github.com/oli792dev/docker-bitcoin-dca/releases/tag/v0.1.18) — 2026-10-28

- Calculate purchase intervals from the last purchase to the next deposit date, using the current time only when no previous purchase exists.
- Recalculate saved schedules with the corrected rule and preserve the last purchase time across monthly resets.

## [v0.1.17](https://github.com/oli792dev/docker-bitcoin-dca/releases/tag/v0.1.17) — 2026-10-28

Changes in the Parqet CSV generation logic:
- Restricted Parqet CSV exports to the complete interval between the previous confirmed withdrawal and the last confirmed withdrawal in the selected date range.
- Excluded purchases made after the ending withdrawal, even when the selected range includes them.
- Included the full withdrawal interval when the selected start date falls inside it.
- For the first withdrawal, included all preceding recorded real purchases.
- Updated the README, UI guidance, and illustration.

## [v0.1.16](https://github.com/oli792dev/docker-bitcoin-dca/releases/tag/v0.1.16) — 2026-10-28

- Improved the web UI appearance.

## [v0.1.15](https://github.com/oli792dev/docker-bitcoin-dca/releases/tag/v0.1.15) — 2026-10-28

- Improved the web UI appearance.

## [v0.1.14](https://github.com/oli792dev/docker-bitcoin-dca/releases/tag/v0.1.14) — 2026-10-28

- Improved the web UI appearance.

## [v0.1.13](https://github.com/oli792dev/docker-bitcoin-dca/releases/tag/v0.1.13) — 2026-10-28

- Improved the web UI appearance.

## [v0.1.12](https://github.com/oli792dev/docker-bitcoin-dca/releases/tag/v0.1.12) — 2026-10-28

- Improved the web UI appearance.

## [v0.1.11](https://github.com/oli792dev/docker-bitcoin-dca/releases/tag/v0.1.11) — 2026-10-28

- Made all superscript footnote numbers consistently white.
- Fixed currency-rate parsing that incorrectly hid monetary totals in EUR-only reports.
- Improved the currency conversion error message.
- Restored startup INFO logs showing the last purchase, elapsed time, calculated interval, next purchase time, and remaining wait.
- Updated the README and web UI illustration.

## [v0.1.10](https://github.com/oli792dev/docker-bitcoin-dca/releases/tag/v0.1.10) — 2026-10-28

- Improved the web UI appearance.

## [v0.0.9](https://github.com/oli792dev/docker-bitcoin-dca/releases/tag/v0.0.9) — 2026-10-28

- Distinguished definitive exchange rejections from uncertain submission outcomes.
- Fixed BTC balance calculations to include network fees.
- Added withdrawal status tracking; only confirmed withdrawals are included in Parqet exports.
- Improved market-rule refreshes, fee-aware budgeting, and mixed-currency reports.
- Reduced API requests and added time-limited price caching.
- Migrated activity history to SQLite with server-side pagination.
- Added a task queue for manual withdrawals.
- Added periodic account checks, worker supervision, and a Docker healthcheck.
- Fixed date-range initialization and removed unused code.
- Updated the README, web UI preview, and documented limitations.

## [v0.0.8](https://github.com/oli792dev/docker-bitcoin-dca/releases/tag/v0.0.8) — 2026-10-28

- Improved the web UI appearance.

## [v0.0.7](https://github.com/oli792dev/docker-bitcoin-dca/releases/tag/v0.0.7) — 2026-10-28

- Repositioned the manual withdrawal button in the web UI.

## [v0.0.6](https://github.com/oli792dev/docker-bitcoin-dca/releases/tag/v0.0.6) — 2026-10-28

- Renamed the application and web UI to Bitcoin DCA Bot.
- Restricted purchases and withdrawals to Bitcoin (BTC).
- Removed BASE_ASSET and configurable cryptocurrency support.
- Limited dashboard activity and Parqet exports to BTC records.
- Updated the README and example screenshots.

## [v0.0.5](https://github.com/oli792dev/docker-bitcoin-dca/releases/tag/v0.0.5) — 2026-10-28

- Moved “Purchase schedule” into its own main panel alongside “Bot configuration”.
- Added a linked footnote explaining how purchase intervals and the next purchase time are calculated.

## [v0.0.4](https://github.com/oli792dev/docker-bitcoin-dca/releases/tag/v0.0.4) — 2026-10-28

- Simplified dashboard labels and renamed “Bot settings” to “Bot configuration”.
- Separated the purchase schedule from other configuration details.
- Moved date filters above the selected period summary and improved visual grouping.
- Moved explanatory text and the local-access notice into linked footnotes.
- Removed the exchange label above the title and updated the subtitle.
- Matched the withdrawal interval selector width to the date fields.
- Added clear CSV export messages when the selected period has no withdrawal or real purchase.

## [v0.0.3](https://github.com/oli792dev/docker-bitcoin-dca/releases/tag/v0.0.3) — 2026-10-28

- Simplified dashboard labels and renamed “Bot settings” to “Bot configuration”.
- Visually separated the purchase schedule from other configuration details.
- Moved date filters above “Selected period summary” and added a clearer visual separation between portfolio information and period analysis.

## [v0.0.2](https://github.com/oli792dev/docker-bitcoin-dca/releases/tag/v0.0.2) — 2026-10-28

- Added configurable live-order verification for Kraken and Bitvavo via `ORDER_STATUS_CHECK_SECONDS`. Open or unresolved orders block further purchases; final fills are recorded after the order ends.
- Added the estimated next purchase time and a live countdown to the dashboard.
- Improved Kraken read-request retries and endpoint logging without automatically resending orders or withdrawals.
- Improved mobile dashboard layouts and reduced date-range typography for clearer display on small screens.

## [v0.0.1](https://github.com/oli792dev/docker-bitcoin-dca/releases/tag/v0.0.1) — 2026-10-27

- No release notes were published.

