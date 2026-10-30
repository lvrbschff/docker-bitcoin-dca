# Architecture and operation

[Back to README](../README.md)

One container runs the dashboard and a worker in one Python process. The worker serializes account checks, orders and manual withdrawals.

~~~mermaid
flowchart LR
    Browser <--> Web["Dashboard and reports"]
    Web <-->|Action queue| Worker["Account worker"]
    Worker <--> Adapter["Kraken or Bitvavo adapter"]
    Adapter <--> Exchange["Exchange API"]
    Worker <--> State["./state → /data"]
    Worker <--> Data["Named volume → /webui-data"]
    Web <--> Data
    Web --> Sources["Kraken prices / Frankfurter FX"]
~~~

- `app/bot.py`, `app/worker.py`, `app/withdrawals.py`: scheduling and account actions.
- `app/exchange.py`, `app/kraken.py`, `app/bitvavo.py`: exchange access.
- `app/web_ui.py`, `app/analytics.py`, `app/reporting.py`: dashboard and exports.
- `app/activity.py`: SQLite activity, reporting data and persistence.
The application source lives in `app/`; tests live in `tests/`. Docker copies `app/` into its `/app` working directory, so container startup and healthcheck commands are unchanged.

- `Dockerfile` builds the image; `docker-compose.yml` configures runtime, port and storage.

**Keep and back up both mounts:** `./state` holds pending requests and scheduling state; `bitcoin-dca-webui-data` holds `activity.sqlite3`, FX cache and `dashboard.json`. Stop the bot for a consistent backup, or use a SQLite-aware backup. Legacy JSON activity is imported once; downgrading to a JSON-only release loses access to newer SQLite activity.

Back up both `./state` and `bitcoin-dca-webui-data`. After updating the source, run `docker compose up -d --build`. **Do not run `docker compose down -v`: it deletes the activity volume.**

View recent logs with `docker compose logs --tail=200`. INFO logs show schedule inputs, next purchase times, orders, withdrawals and errors. Review logs before sharing: they can contain balances and transaction identifiers.

Docker checks health every five minutes; the saved heartbeat must be running and less than 15 minutes old. A supervisor exits the process if the worker stops or stalls for ten minutes, allowing `restart: unless-stopped` to restart it. Docker’s unhealthy status alone does not restart a container. Health monitors worker responsiveness, not exchange availability or trading success.

The [publish workflow](../.github/workflows/publish.yml) builds GHCR images on main pushes, version tags and manual runs; it does not run the test suite. Credentials belong on the Docker host and are not included in the image.


## Development checks

From the repository root, with Python and the `requests` dependency installed:

~~~sh
python tests/run_tests.py
node tests/test_dashboard.cjs
~~~

## Log levels

- **DEBUG:** routine HTTP access, unchanged waiting states, below-threshold checks, request timings and diagnostic stack traces.
- **INFO:** startup and purchase planning, trades and successful submissions, withdrawal status changes, explicit UI actions and CSV exports. Expected validation rejections are INFO.
- **WARNING:** transient exchange/network failures, rejected orders and failed withdrawals. Recoverable network failures do not print stack traces at this level.
- **ERROR:** unexpected internal failures, uncertain financial outcomes requiring intervention, or a submitted withdrawal without a unique history match for at least one hour. Legacy unverified history remains DEBUG.
- **CRITICAL:** stopped or stalled account worker; the process exits for Docker restart.

Repeated waiting observations are DEBUG after their first state-change message. Purchase schedule and restart-alignment messages remain INFO. Kraken read retries remain WARNING and successful recovery remains INFO. Do not interpret a UI confirmation or accepted queue request as a completed exchange transaction.
