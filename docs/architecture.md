# Architecture and operation

[Back to README](../README.md)

One container runs the dashboard and a worker in one Python process. The worker serializes account checks, orders and manual withdrawals. Configuration changes use the same account lock. Before first-run configuration is complete, the worker remains healthy but performs no exchange actions.

~~~mermaid
flowchart LR
    Browser <--> Web["Dashboard and reports"]
    Web <-->|Action queue| Worker["Account worker"]
    Worker <--> Adapter["Kraken or Bitvavo adapter"]
    Adapter <--> Exchange["Exchange API"]
    Worker <--> Data["bitcoin-dca-webui-data → /webui-data"]
    Web <--> Data
    Web --> Sources["Kraken prices / Frankfurter FX"]
~~~

- `app/bot.py`, `app/worker.py`, `app/withdrawals.py`: scheduling and account actions.
- `app/exchange.py`, `app/kraken.py`, `app/bitvavo.py`: exchange access.
- `app/web_ui.py`, `app/analytics.py`, `app/reporting.py`: dashboard and exports.
- `app/configuration.py`, `app/runtime.py`, `app/settings_ui.py`: private persistent configuration, runtime application and setup/settings pages.
- `app/activity.py`: SQLite activity, reporting data and persistence.
- `app/storage.py`: volume paths and one-time migration before account actions start.
The application source lives in `app/`; tests live in `tests/`. Docker copies `app/` into its `/app` working directory, so container startup and healthcheck commands are unchanged.

- `Dockerfile` builds the image; `docker-compose.yml` configures runtime, port and storage.

**One persistent volume:** `bitcoin-dca-webui-data`, mounted at `/webui-data`, holds private `config.json`, `state.json` (pending transactions and schedule), `activity.sqlite3`, FX cache, `dashboard.json`, the worker heartbeat and storage-layout marker. No host state directory is used during normal operation. Stop the bot for a consistent backup, or use a SQLite-aware backup. Legacy JSON activity is imported once; downgrading to a JSON-only release loses access to newer SQLite activity.

Back up the named volume. After updating the source, run `docker compose up -d --build`. **Do not run `docker compose down -v`: it deletes the volume.**

View recent logs with `docker compose logs --tail=200`. INFO logs show schedule inputs, next purchase times, orders, withdrawals and errors. Review logs before sharing: they can contain balances and transaction identifiers.

Docker checks health every five minutes; the saved heartbeat must be running and less than 15 minutes old. A supervisor exits the process if the worker stops or stalls for ten minutes, allowing `restart: unless-stopped` to restart it. Docker’s unhealthy status alone does not restart a container. Health monitors worker responsiveness, not exchange availability or trading success.

The [publish workflow](../.github/workflows/publish.yml) builds GHCR images on main pushes, version tags and manual runs; it does not run the test suite. Credentials belong on the Docker host and are not included in the image.


## Development checks

From the repository root, with Python and the `requests` dependency installed:

~~~sh
python tests/run_tests.py
node tests/test_dashboard.cjs
node tests/test_settings.cjs
~~~

## Log levels

- **DEBUG:** routine HTTP access, unchanged waiting states, below-threshold checks, request timings and diagnostic stack traces.
- **INFO:** startup and purchase planning, trades and successful submissions, withdrawal status changes, explicit UI actions and CSV exports. Expected validation rejections are INFO.
- **WARNING:** transient exchange/network failures, rejected orders and failed withdrawals. Recoverable network failures do not print stack traces at this level.
- **ERROR:** unexpected internal failures, uncertain financial outcomes requiring intervention, or a submitted withdrawal without a unique history match for at least one hour. Legacy unverified history remains DEBUG.
- **CRITICAL:** stopped or stalled account worker; the process exits for Docker restart.

Repeated waiting observations are DEBUG after their first state-change message. Purchase schedule and restart-alignment messages remain INFO. Kraken read retries remain WARNING and successful recovery remains INFO. Do not interpret a UI confirmation or accepted queue request as a completed exchange transaction.

## Configuration and migration

`/webui-data/config.json` in the named `bitcoin-dca-webui-data` volume is written atomically with owner-only permissions. It contains the account name, a salted scrypt password hash and application settings, including API secrets. Protect this file and backups as credentials. The browser receives only masked credential-presence flags; leaving API fields empty keeps their values. Changing account credentials invalidates dashboard sessions. Restarting the container also invalidates sessions and withdrawal previews.

A fresh installation opens account creation, then authenticated configuration. The first successful account creation closes anonymous registration. An incomplete setup cannot access account actions or reports. New settings default to dry run. The connection test reads balances only; it does not prove trading or withdrawal permission.

On first startup without a saved file, existing selected-exchange API credentials trigger one-time import of the former environment configuration and dashboard account. Imported weak passwords require an update in Settings. Saved application settings take precedence on later starts. The supplied Compose file passes only `WEB_UI_PORT` and `LOG_LEVEL`; mounts and restart policy remain Docker-managed. Display currency is saved with the application settings. Existing configuration files gain EUR as the default display currency without changing credentials.

Runtime edits and financial actions are serialized. Changes to exchange, currency, credentials or live mode are rejected while persistent transactions or queued actions are unresolved. Exchange/currency and dry-run/live switches archive resolved scheduling state, reset the account snapshot and select purchase history for the active exchange; activity records keep their original exchange. Settings affecting scheduling request a fresh check; toggling presentation/export features does not change the existing account-check deadline. Manual withdrawal previews and submissions do not recalculate purchase intervals.

Automatic withdrawals, manual withdrawals and Parqet export have separate settings. Manual withdrawals and exports are blocked at their API endpoints when disabled. Automatic withdrawal disabling does not stop reconciliation of an already submitted withdrawal. Notes follow the enabled features.

### Environment-only operation

With `WEB_UI_ENABLED=false`, the bot continues to use environment variables directly. Legacy names are `EXCHANGE`, `QUOTE_CURRENCY`, `BUY_AMOUNT`, `DEPOSIT_DAY`, `startup_delay_seconds`, `TIMEZONE`, `LIVE_TRADING`, `CHECK_INTERVAL_SECONDS`, `ORDER_STATUS_CHECK_SECONDS`, `WITHDRAWALS_ENABLED`, `WITHDRAW_THRESHOLD_QUOTE`, `KRAKEN_WITHDRAW_KEY`, `WITHDRAW_ADDRESS`, `WITHDRAW_NETWORK`, and each exchange’s `*_API_KEY` / `*_API_SECRET`. Dashboard deployments import these only when no saved configuration exists and legacy variables are explicitly passed (for example with the previous Compose file). The simplified Compose file no longer forwards credentials or bot settings. During a one-time migration, accessible legacy files are copied atomically into the named volume; source files are retained. An existing volume configuration always takes precedence, and an unreadable configuration stops startup instead of silently restarting setup. Reuse the same named volume on redeployment. For environment-only legacy installations, migrate with the previous Compose configuration before removing legacy environment values.

Setup and Settings log account creation, one-time environment import, configuration completion, changed field names, session invalidation, read-only connection tests and explicit dialog cancellations at INFO. Expected validation or pending-transaction rejections are INFO; exchange/network failures are WARNING and unexpected failures are ERROR. Stored configuration reads and sanitized failure locations are DEBUG. Passwords, API credential values, session/confirmation tokens and raw settings exception messages are never included in these logs.

## Settings and activity UI

First-run account creation is separate from bot configuration; the second step does not repeat Dashboard Credentials. Later, Settings opens in a dashboard dialog. Trading and dashboard groups the large live/withdrawal/export switches and display currency; connection-test results appear beside its button. Reporting currency changes do not reschedule purchases.

Only executed live fills are stored as buys; these display Confirmed. Simulations remain Simulated. Withdrawals display Confirmed only after exchange-history reconciliation, with pending, failed, cancelled and legacy-unverified outcomes kept distinct. This is exchange confirmation, not independent blockchain verification.

## Move existing state to the named volume

Do this once before removing the old `./state:/data` mount. The old directory cannot be accessed after that mount is removed.

In Portainer, first deploy the updated image with the **old mount still present**, alongside the named volume. Startup migrates scheduling state, pending requests, configuration and legacy JSON data before starting the worker. Wait for `Persistent storage ready: all runtime data uses the named web UI volume`, then remove the old bind mount and redeploy. The normal Compose file contains only the named volume.

Alternatively, with the bot stopped, run the migration from the repository on the Docker host (adjust the source path if needed):

~~~sh
docker compose stop
docker compose build
docker compose run --rm --no-deps --volume /data/compose/28/state:/legacy-state:ro bitcoin-dca python storage.py --from /legacy-state
docker compose up -d
~~~

The temporary migration container reads the old directory only once; the running bot uses only the named volume. Existing volume settings take precedence. Conflicting scheduling states, malformed files or an existing configured account without its scheduling state stop startup rather than risk repeating orders or withdrawals. The migration is retryable after a failed write. Once migration is complete, legacy files are not read again; retain the old folder as a backup until verified.
