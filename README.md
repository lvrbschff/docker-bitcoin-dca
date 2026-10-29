# Bitcoin DCA Bot for Bitvavo and Kraken

A Docker bot that buys Bitcoin on Bitvavo or Kraken, with optional automatic and manual withdrawals. It distributes purchases until the next deposit day using the available fiat balance, current price and exchange order minimums. **Bitcoin DCA Dashboard** provides scheduling, activity and Parqet CSV exports.

![Bitcoin DCA Dashboard: Configuration and purchase schedule with fictional data](docs/web-ui-dashboard-screenshot.png)

*Browser screenshot with fictional data; the lower dashboard sections are outside this crop.*

## Setup

Requires Docker Engine with Compose, internet access and a Bitvavo or Kraken account.

### 1. Configure exchange access

Clone the repository and create your local configuration:

~~~sh
git clone https://github.com/oli792dev/docker-bitcoin-dca.git
cd docker-bitcoin-dca
cp .env.example .env
~~~

Create an API key for your selected exchange. Grant withdrawal permissions only if you will use withdrawals.

| Exchange | Required API permissions | Credentials in `.env` |
| --- | --- | --- |
| [Bitvavo](https://docs.bitvavo.com/docs/get-started/) | View access; Trade digital assets for live buys; Withdraw digital assets for withdrawals | `BITVAVO_API_KEY`, `BITVAVO_API_SECRET` |
| [Kraken](https://support.kraken.com/in/articles/360000919966-how-to-create-an-api-key?mode=consumerapp) | Query Funds, Query Open Orders & Trades, Query Closed Orders & Trades; Modify Orders for live buys; Withdraw Funds for withdrawals | `KRAKEN_API_KEY`, `KRAKEN_API_SECRET` |

Fill in only the selected exchange’s credentials. **Never commit `.env` or API credentials.**

For withdrawals, first save and verify the destination on the exchange, then configure:

| Exchange | Destination in `.env` |
| --- | --- |
| Bitvavo | `WITHDRAW_ADDRESS` and `WITHDRAW_NETWORK` |
| Kraken | `KRAKEN_WITHDRAW_KEY`: the exact label of the saved Bitcoin withdrawal address, not the address itself |

Set `WEB_UI_USERNAME` and **change `WEB_UI_PASSWORD`** in `.env` (defaults: `admin` / `admin`). `WEB_UI_PORT` defaults to `6890`. The port is published on all host interfaces; HTTP is unencrypted, so use an HTTPS reverse proxy on untrusted networks.

### 2. Configure the bot

Edit `docker-compose.yml`. Start with `LIVE_TRADING=false`.

| Setting | Default | Purpose |
| --- | --- | --- |
| `EXCHANGE` | `KRAKEN` | `KRAKEN` or `BITVAVO`. |
| `QUOTE_CURRENCY` | `EUR` | Currency used to buy BTC. Reporting supports EUR and USD; the exchange must support the market. |
| `BUY_AMOUNT` | `0` | BTC per purchase; `0` uses the exchange minimum. Amounts below the minimum are raised to it. |
| `DEPOSIT_DAY` | `1` | Monthly scheduling boundary; shortened months use their last day. Does not initiate deposits. |
| `startup_delay_seconds` | `60` | Wait after each startup before account checks and scheduling. |
| `TIMEZONE` | `Europe/Berlin` | Deposit-cycle timezone. |
| `LIVE_TRADING` | `false` | `false`: simulated buys; `true`: real orders. |
| `CHECK_INTERVAL_SECONDS` | `300` | Account-check interval while waiting; minimum 60 seconds. |
| `ORDER_STATUS_CHECK_SECONDS` | `30` | Delay before and between live-order status checks. |
| `WITHDRAWALS_ENABLED` | `false` | Automatic withdrawals; also requires live trading. |
| `WITHDRAW_THRESHOLD_QUOTE` | `1000` | Automatic withdrawal trigger, valued in the purchase currency. |
| `WEB_UI_ENABLED` | `true` | Enable the dashboard. |
| `LOG_LEVEL` | `INFO` | Optional logging verbosity. |

### 3. Start and review

~~~sh
docker compose up -d --build
docker compose logs -f
~~~

Open `http://<docker-host-ip>:6890` and sign in. Dry-run mode still requires API credentials and internet access. Simulated buys are marked in activity and excluded from CSV exports.

To start real purchases, set `LIVE_TRADING=true` and recreate the container with the same command.

## Purchases and withdrawals

### Purchase schedule

The bot refreshes the available fiat balance, BTC price and exchange order rules. Estimated order cost includes the trading fee and a **0.5% planning buffer**.

**Purchase interval = time from the last purchase to the next deposit day ÷ affordable purchases.**

The next purchase is the last purchase time plus that interval. At startup, the bot uses recorded real purchases, falling back to saved execution state. Without previous execution history, the first purchase is due after the startup delay. Unchanged planning inputs retain the saved due time; changed funds, costs or deposit boundaries cause recalculation.

Live orders are submitted once and verified before another purchase. Open orders and uncertain submissions remain pending across restarts; completed partial fills are recorded at their actual executed amount. Network requests and queued actions can delay execution.

### Withdrawals

Automatic withdrawals require `WITHDRAWALS_ENABLED=true`, `LIVE_TRADING=true`, withdrawal permissions and a configured destination. Account checks compare **all available exchange BTC × current exchange price** against `WITHDRAW_THRESHOLD_QUOTE`. This includes BTC acquired outside the bot. When reached, the bot withdraws the available balance, less withdrawal fees.

For a manual withdrawal, click **Withdraw BTC manually** in **Current BTC balance**, review the destination and amount, then confirm. Live trading and destination permissions are required; the automatic toggle and threshold do not apply.

Submitted withdrawals block further withdrawals until resolved. Uncertain Kraken withdrawals are not automatically resent; Bitvavo may retry the same idempotent request. Verify exchange status before intervening.

## Dashboard and Parqet export

- **Configuration:** exchange, purchase settings and withdrawal target.
- **Schedule:** exchange balances, purchase timing and estimated automatic withdrawal timing. **Expected monthly deposit** is a browser-saved forecast assumption; it adds funds on each deposit day and recalculates future intervals using actual calendar months. It does not initiate deposits or change trading.
- **Period Analysis:** date filters, summary, paginated activity and CSV export. Activities cannot be deleted through the UI.

Display currencies are EUR and USD. Reference BTC prices come from Kraken’s public [BTC/EUR](https://www.kraken.com/convert/btc/eur) and [BTC/USD](https://www.kraken.com/convert/btc/usd) pages, including when trading on Bitvavo. Saved or cached prices are labelled.

**Download Parqet CSV** exports one consolidated Buy row for the **last confirmed withdrawal in the selected range**:

| CSV field | Value |
| --- | --- |
| Cost | Real purchase costs plus trading fees since the preceding confirmed withdrawal, in EUR. USD costs use historical reference rates. |
| BTC quantity | Ending withdrawal’s net BTC after network fees. |
| Date | Ending withdrawal date. |

The complete withdrawal cycle is used even when the selected start falls inside it. For the first withdrawal, all preceding recorded real purchases are included. Later purchases, simulations and failed or unconfirmed withdrawals are excluded. At least one real purchase is required. Fees are already included in cost and net BTC; the CSV fee field is zero.

Import using [Parqet’s CSV importer](https://faq.parqet.com/de/articles/870989-csv-import-aktivitaten-aus-einer-eigenen-csv-datei-importieren). Re-exporting a withdrawal can create duplicates.

## Architecture and operation

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

After updating the source, rebuild with `docker compose up -d --build`. **Do not use `docker compose down -v` when updating.**

View recent logs with `docker compose logs --tail=200`. INFO logs show schedule inputs, next purchase times, orders, withdrawals and errors. Review logs before sharing: they can contain balances and transaction identifiers.

Docker checks health every five minutes; the saved heartbeat must be running and less than 15 minutes old. A supervisor exits the process if the worker stops or stalls for ten minutes, allowing `restart: unless-stopped` to restart it. Docker’s unhealthy status alone does not restart a container. Health monitors worker responsiveness, not exchange availability or trading success.

The [publish workflow](.github/workflows/publish.yml) builds GHCR images on main pushes, version tags and manual runs; it does not run the test suite. Credentials belong on the Docker host and are not included in the image.

## Limitations

- **Bitcoin spot market orders only.** Prices, fills and fees can differ from estimates. The planning buffer is not a price cap. Withdrawal forecasts assume unchanged prices and costs plus the entered monthly deposits.
- **Available funds drive scheduling.** The bot does not reserve funds for other uses or identify actual monthly deposits. Run one instance per account and persistent state; other trading tools can affect balances.
- **Local history is not a full account statement.** Unrelated trades and transfers are not imported. BTC totals use gross withdrawal debits; exported BTC uses the net amount received.
- **Unresolved transactions require care.** Orders with uncertain outcomes block new purchases. Withdrawals qualify for export only after a unique exchange-history confirmation; the bot does not independently verify the blockchain. Older records may remain unverified. In-memory sessions and confirmation previews are lost on restart; pending submissions persist.
- **Reporting depends on external services.** Kraken page changes or outages can affect reference prices; historical USD/EUR conversion uses Frankfurter. Unavailable rates can prevent exports. Reporting supports EUR and USD; displayed conversions use current reference rates.
- **Exports consolidate a time interval.** They do not match individual Bitcoin units or track previous exports. Review each CSV before importing.

## Disclaimer

Provided “as is” without warranty or investment, tax or legal advice. You are responsible for configuration, API credentials, monitoring and verifying transactions and exports. To the fullest extent permitted by law, the maintainer accepts no liability for financial losses, fees, outages, withdrawal errors or data loss. This does not exclude liability that cannot legally be excluded.

## Support

- Bitcoin: `bc1qg7crlvea9rsacfgnujhrjy6judvjnkjz9crvvq`
