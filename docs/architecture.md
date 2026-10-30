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

- `bot.py`, `worker.py`, `withdrawals.py`: scheduling and account actions.
- `exchange.py`, `kraken.py`, `bitvavo.py`: exchange access.
- `web_ui.py`, `analytics.py`, `reporting.py`: dashboard and exports.
- `activity.py`: SQLite activity, reporting data and persistence.
- `Dockerfile` builds the image; `docker-compose.yml` configures runtime, port and storage.

**Keep and back up both mounts:** `./state` holds pending requests and scheduling state; `bitcoin-dca-webui-data` holds `activity.sqlite3`, FX cache and `dashboard.json`. Stop the bot for a consistent backup, or use a SQLite-aware backup. Legacy JSON activity is imported once; downgrading to a JSON-only release loses access to newer SQLite activity.

Back up both `./state` and `bitcoin-dca-webui-data`. After updating the source, run `docker compose up -d --build`. **Do not run `docker compose down -v`: it deletes the activity volume.**

View recent logs with `docker compose logs --tail=200`. INFO logs show schedule inputs, next purchase times, orders, withdrawals and errors. Review logs before sharing: they can contain balances and transaction identifiers.

Docker checks health every five minutes; the saved heartbeat must be running and less than 15 minutes old. A supervisor exits the process if the worker stops or stalls for ten minutes, allowing `restart: unless-stopped` to restart it. Docker’s unhealthy status alone does not restart a container. Health monitors worker responsiveness, not exchange availability or trading success.

The [publish workflow](../.github/workflows/publish.yml) builds GHCR images on main pushes, version tags and manual runs; it does not run the test suite. Credentials belong on the Docker host and are not included in the image.

