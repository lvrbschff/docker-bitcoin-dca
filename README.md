# Bitcoin DCA Bot for Bitvavo and Kraken

A Docker bot that buys Bitcoin on Bitvavo or Kraken, with optional automatic and manual withdrawals. It distributes purchases until the next deposit day using the available fiat balance, current price and exchange order minimums. Bitcoin DCA Dashboard provides scheduling, activity and Parqet CSV exports.

![Bitcoin DCA Dashboard: full-page screenshot with fictional data](docs/web-ui-dashboard-screenshot.png)

*Full-page browser screenshot with fictional data, including Configuration, Schedule, Period Analysis and Notes.*

## Setup

Requires Docker Engine with Compose, internet access and a Bitvavo or Kraken account.

~~~sh
git clone https://github.com/oli792dev/docker-bitcoin-dca.git
cd docker-bitcoin-dca
docker compose up -d --build
~~~

Open `http://<docker-host-ip>:6890`. On a fresh installation, create your username and password first (at least 12 characters), then configure your exchange and API credentials. Review and save to open the dashboard. There is no default account. New installations start in dry-run mode; API access and internet are required even for simulations.

**Settings**, next to **Log out**, lets you change the account, exchange, API credentials, purchase amount, deposit day, timezone, startup/check intervals and trading mode. It also controls automatic withdrawals and their threshold, the destination, manual withdrawals and Parqet export. Disabled features hide their controls and notes and are blocked by the server. Empty API fields retain saved credentials; secrets are never displayed again.

| Exchange | API permissions | Bitcoin withdrawal destination |
| --- | --- | --- |
| [Bitvavo](https://docs.bitvavo.com/docs/get-started/) | View access; Trade digital assets for live buys; Withdraw digital assets for withdrawals | Address and network saved and verified on Bitvavo |
| [Kraken](https://support.kraken.com/articles/360000919966-how-to-create-an-api-key) | Query Funds; Query Open/Closed Orders & Trades; Modify Orders for live buys; Withdraw Funds for withdrawals | Exact label of a saved Bitcoin withdrawal method, not the address itself |

**Test connection** checks account balance access without placing an order or withdrawal. Enable live trading only after reviewing the dry-run schedule. Exchange, currency, credential and trading-mode changes are blocked while orders, withdrawals or queued actions remain unresolved.

Application settings persist in `./state/config.json`; passwords are hashed, API credentials are stored in this private file. Protect it and its backups. Never commit `.env`, `state/` or API credentials. Existing installations import their environment settings once; saved settings then take precedence. A weak initial dashboard password must be changed in Settings.

Docker still controls ports, volumes and restart policy. To change the default port, copy `.env.example` to `.env` and set `WEB_UI_PORT`. The port is published on all host interfaces; HTTP is unencrypted, so use an HTTPS reverse proxy on untrusted networks. Environment-only/headless operation remains available; see [Architecture and operation](docs/architecture.md).

## Purchases and withdrawals

### Purchase schedule

The bot refreshes the available fiat balance, BTC price and exchange order rules. Estimated order cost includes the trading fee and a 0.5% planning buffer.

**Purchase interval = time from the last purchase to the next deposit day ÷ affordable purchases.**

The next purchase is the last purchase time plus that interval. At startup, the bot uses recorded real purchases, falling back to saved execution state. Without previous execution history, the first purchase is due after the startup delay. Unchanged planning inputs retain the saved due time; changed funds, costs or deposit boundaries cause recalculation.

Live orders are submitted once and verified before another purchase. Open orders and uncertain submissions remain pending across restarts; completed partial fills are recorded at their actual executed amount. Network requests and queued actions can delay execution.

### Withdrawals

Enable automatic withdrawals and live trading in Settings, with withdrawal permissions and a configured destination. Account checks compare all available exchange BTC × current exchange price against the configured withdrawal threshold. This includes BTC acquired outside the bot. When reached, the bot withdraws the available balance, less withdrawal fees.

For a manual withdrawal, click **Withdraw BTC manually** in Current BTC balance, review the destination and amount, then confirm. Live trading and destination permissions are required; the automatic toggle and threshold do not apply.

Submitted withdrawals block further withdrawals until resolved. Uncertain Kraken withdrawals are not automatically resent; Bitvavo may retry the same idempotent request. Verify exchange status before intervening.

## Dashboard and Parqet export

The web UI is fully responsive on desktop, tablet and mobile. Card rows and tables scroll horizontally only when needed.

- **Configuration:** exchange, purchase settings and withdrawal target.
- **Schedule:** exchange balances, purchase timing and estimated automatic withdrawal timing. **Expected monthly deposit** is a browser-saved forecast assumption; it adds funds on each deposit day and recalculates future intervals using actual calendar months. It does not initiate deposits or change trading.
- **Period Analysis:** date filters, summary, paginated activity and CSV export. Activities cannot be deleted through the UI.

View preferences are saved in this browser. Refreshes preserve existing values; withdrawal confirmations show the destination, gross BTC, fee and net BTC.

Display currencies are EUR and USD. Reference BTC prices come from Kraken’s public [BTC/EUR](https://www.kraken.com/convert/btc/eur) and [BTC/USD](https://www.kraken.com/convert/btc/usd) pages, including when trading on Bitvavo. Saved or cached prices are labelled.

**Download Parqet CSV** exports one consolidated Buy row for the last confirmed withdrawal in the selected range:

The row uses real purchase costs plus trading fees in EUR, the ending withdrawal’s net BTC after network fees, and its date. USD costs use historical reference rates.

The complete withdrawal cycle is used even when the selected start falls inside it. For the first withdrawal, all preceding recorded real purchases are included. Later purchases, simulations and failed or unconfirmed withdrawals are excluded. At least one real purchase is required. Fees are already included in cost and net BTC; the CSV fee field is zero.

Import using [Parqet’s CSV importer](https://faq.parqet.com/de/articles/870989-csv-import-aktivitaten-aus-einer-eigenen-csv-datei-importieren). Re-exporting a withdrawal can create duplicates.

## Container architecture

See [Architecture and operation](docs/architecture.md) for container components, storage, backups, updates, health checks and logs. See the [Changelog](docs/CHANGELOG.md) for release history.

## Limitations

- **Bitcoin spot market orders only.** Prices, fills and fees can differ from estimates. The planning buffer is not a price cap. Withdrawal forecasts assume unchanged prices and costs plus the entered monthly deposits.
- **Available funds drive scheduling.** The bot does not reserve funds for other uses or identify actual monthly deposits. Run one instance per account and persistent state; other trading tools can affect balances.
- **Local history is not a full account statement.** Unrelated trades and transfers are not imported. BTC totals use gross withdrawal debits; exported BTC uses the net amount received.
- **Unresolved transactions require care.** Orders with uncertain outcomes block new purchases. Withdrawals qualify for export only after a unique exchange-history confirmation; the bot does not independently verify the blockchain. Older records may remain unverified. In-memory sessions and confirmation previews are lost on restart; pending submissions persist.
- **Reporting depends on external services.** Kraken page changes or outages can affect reference prices; historical USD/EUR conversion uses Frankfurter. Unavailable rates can prevent exports. Reporting supports EUR and USD; displayed conversions use current reference rates.
- **Exports consolidate a time interval.** They do not match individual Bitcoin units or track previous exports. Review each CSV before importing.

## Disclaimer

Provided “as is” without warranty or investment, tax or legal advice. You are responsible for configuration, API credentials, monitoring and verifying transactions and exports. To the fullest extent permitted by law, the maintainer accepts no liability for financial losses, fees, outages, withdrawal errors or data loss. This does not exclude liability that cannot legally be excluded.
