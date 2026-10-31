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

The application is a Python package organized by responsibility:

~~~text
app/
├── __main__.py   # python -m app
├── webpage/      # HTTPS dashboard, settings, icons and browser assets
├── trading/      # Bot, worker, runtime and withdrawals
├── exchanges/    # Kraken/Bitvavo adapters and transport helpers
├── data/         # Volume storage, activity ledger, analytics and CSV exports
└── core/         # Configuration, access security, logging and health checks
~~~

Tests live in `tests/`. Docker copies the package to `/app/app` and starts it with `python -u -m app`. Its healthcheck runs `python -m app.core.healthcheck`; package imports use one shared set of worker locks and runtime modules.

- `Dockerfile` builds the image; `docker-compose.yml` configures runtime, port and storage.

**One persistent volume:** `bitcoin-dca-webui-data`, mounted at `/webui-data`, holds private `config.json`, `state.json` (pending transactions and schedule), `activity.sqlite3`, FX cache, `dashboard.json`, the worker heartbeat. No host state directory is used during normal operation. Stop the bot for a consistent backup, or use a SQLite-aware backup. Legacy JSON activity is imported once; downgrading to a JSON-only release loses access to newer SQLite activity.

Back up the named volume. After updating the source, run `docker compose up -d --build`. **Do not run `docker compose down -v`: it deletes the volume.**

View recent logs with `docker compose logs --tail=200`. INFO logs show schedule inputs, next purchase times, orders, withdrawals and errors. Review logs before sharing: they can contain balances and transaction identifiers.

Docker checks health every five minutes; the saved heartbeat must be running and less than 15 minutes old. A supervisor exits the process if the worker stops or stalls for ten minutes, allowing `restart: unless-stopped` to restart it. Docker’s unhealthy status alone does not restart a container. Health monitors worker responsiveness, not exchange availability or trading success.

The [publish workflow](../.github/workflows/publish.yml) builds GHCR images on main pushes, version tags and manual runs; it runs the Python and JavaScript regression suites before publishing. Credentials belong on the Docker host and are not included in the image.


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

## Configuration

`/webui-data/config.json` in the named `bitcoin-dca-webui-data` volume is written atomically with owner-only permissions. It contains the account name, a salted scrypt password hash and application settings, including API secrets. Protect this file and backups as credentials. The browser receives only masked credential-presence flags; leaving API fields empty keeps their values. Changing account credentials invalidates dashboard sessions. Restarting the container also invalidates sessions and withdrawal previews.

A fresh installation opens account creation, then authenticated configuration. The first successful account creation closes anonymous registration. An incomplete setup cannot access account actions or reports. New settings default to dry run. The connection test reads balances only; it does not prove trading or withdrawal permission.

The web UI always runs. All bot settings, exchange credentials and dashboard login credentials come exclusively from the saved web configuration. Without it, first-run setup is required and exchange actions remain paused; environment variables cannot skip setup or override saved trading settings. Only `WEB_UI_PORT`, `WEB_UI_TLS_HOSTS` and `LOG_LEVEL` remain Docker deployment parameters. Display currency is saved with the application settings. Existing configuration files gain EUR as the default display currency without changing credentials.

Runtime edits and financial actions are serialized. Changes to exchange, currency, credentials or live mode are rejected while persistent transactions or queued actions are unresolved. Exchange/currency and dry-run/live switches archive resolved scheduling state, reset the account snapshot and select purchase history for the active exchange; activity records keep their original exchange. Settings affecting scheduling request a fresh check; toggling presentation/export features does not change the existing account-check deadline. Manual withdrawal previews and submissions do not recalculate purchase intervals.

Automatic withdrawals, manual withdrawals and Parqet export have separate settings. Manual withdrawals and exports are blocked at their API endpoints when disabled. Automatic withdrawal disabling does not stop reconciliation of an already submitted withdrawal. Notes follow the enabled features.


Setup and Settings log account creation, configuration completion, changed field names, session invalidation, read-only connection tests and explicit dialog cancellations at INFO. Expected validation or pending-transaction rejections are INFO; exchange/network failures are WARNING and unexpected failures are ERROR. Stored configuration reads and sanitized failure locations are DEBUG. Passwords, API credential values, session/confirmation tokens and raw settings exception messages are never included in these logs.

## Settings and activity UI

First-run account creation is separate from bot configuration; the second step does not repeat Dashboard Credentials. Later, Settings opens in a dashboard dialog. Trading and dashboard groups the large live/withdrawal/export switches and display currency; connection-test results appear beside its button. Reporting currency changes do not reschedule purchases.

Only executed live fills are stored as buys; these display Confirmed. Simulations remain Simulated. Withdrawals display Confirmed only after exchange-history reconciliation, with pending, failed, cancelled and legacy-unverified outcomes kept distinct. This is exchange confirmation, not independent blockchain verification.

## Startup exchange checks

After setup, restart, or exchange/credential/trading-mode changes, the worker reads open account orders and BTC withdrawal status before allowing new transactions. Open or unverifiable outcomes keep purchases and withdrawals paused; checks retry at the account-check interval. HTTP polling and manual withdrawal clicks do not trigger these checks. The configured startup delay still applies.

Missing scheduling state is initialized without resetting saved configuration or activity. Recorded purchases anchor the rebuilt schedule; existing pending requests retain their normal reconciliation. Lost request identifiers and unrecorded executions cannot be reconstructed from this check; restore a consistent volume backup when historical data is missing. No host migration or manual recovery menu is used. Withdrawal history is paginated with a bounded request count. Missing or repeated Kraken cursors, saturated Bitvavo timestamp windows and exhausted page limits keep the startup gate closed.

## HTTPS and session security

The bot serves HTTPS directly on the configured port, using TLS 1.2 or newer. Set `WEB_UI_TLS_HOSTS` in Compose or `.env` to the address you use (comma-separated hostnames/IPs). The template leaves this variable empty. `localhost` and loopback IPs are always included; fill in the variable before accessing the bot through another IP or hostname. There is no plain-HTTP listener; update saved URLs to `https://`.

On first start, OpenSSL creates a private local CA and a 365-day server certificate in `/webui-data/tls`. Keys have owner-only permissions; protect this directory and volume backups. The server checks renewal daily and on startup. Host changes renew only the server certificate; keeping the volume preserves the trusted CA when relocating.

Copy the public CA certificate from your Docker host:

~~~sh
docker cp bitcoin-dca:/webui-data/tls/ca.pem ./bitcoin-dca-ca.pem
~~~

Import it as a trusted certificate authority on each device. On macOS use [Keychain Access certificate trust settings](https://support.apple.com/guide/keychain-access/change-the-trust-settings-of-a-certificate-kyca11871/mac). Import only `ca.pem`; never distribute either private key. A new volume creates a new CA that must be trusted again.

Session cookies use `Secure`, `HttpOnly` and `SameSite=Strict`, with a fixed 30-minute lifetime. Five failed sign-in attempts from one IP cause a pause until the 15-minute window expires; forwarded IP headers are not trusted. Sensitive settings require the current password: login credentials, exchange/API access, withdrawal destination/threshold, and enabling live trading or automatic withdrawals. Password confirmation attempts are also limited to five per 15 minutes. Cosmetic settings need no password confirmation. Limits are in memory and reset on process restart. These protections follow [OWASP session guidance](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html).

If a client reports `certificate unknown`, it rejected the certificate before an HTTP request was sent. Trust the current volume's `tls/ca.pem` on that device and ensure `WEB_UI_TLS_HOSTS` contains the server address used in the URL. The client IP in the log is not the server address. The first certificate rejection is WARNING; repetitions, disconnects and handshake timeouts are DEBUG. Unexpected internal request failures retain their traceback.

The expected monthly deposit used by the withdrawal forecast is stored with the saved configuration in the named volume. It starts empty; browser-stored values are not imported. Updating it does not recreate the exchange adapter, advance the account-check deadline, or change actual deposits/purchases.

## Resource and security boundaries

The HTTPS server permits at most 64 concurrent connections; excess connections close immediately. Queued manual withdrawals revalidate the dashboard session immediately before execution. Cosmetic settings reuse the existing exchange connection pool. Unexpected worker errors return a generic browser message; diagnostic details remain in container logs.

Persistent JSON files share a durable atomic writer with private temporary files and owner-only permissions. Existing scheduling state and the SQLite ledger are restricted to the owner; the process uses a private file-creation mask. API secrets remain plaintext in the protected volume: volume access or Docker-host administration still grants access to them. The container currently runs as root; changing this requires an explicit ownership plan for existing volumes.

Tests are grouped by behavior rather than individual historical bug reports. Financial uncertainty, duplicate prevention, credential access, session expiry, storage failure and schedule boundaries retain regression coverage. Exchange responses are mocked; these tests do not place real orders or prove live exchange permissions. Local TLS handshakes use in-memory sockets.

Kraken funding calls currently use its supported legacy endpoints. Track the [official funding API notice](https://docs.kraken.com/api-reference/funding/get-status-of-recent-withdrawals) before a future adapter migration; changing financial endpoints requires separate compatibility validation.
