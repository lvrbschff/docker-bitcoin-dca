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

Set `WEB_UI_TLS_HOSTS` in Compose or `.env` to the IP address/hostname you use, then open `https://<docker-host-ip>:6890`. Trust the generated local CA on your devices; see [HTTPS setup](docs/architecture.md#https-and-session-security). The setup wizard first creates your username and password (12+ characters), then configures the exchange, API access and purchases. The web UI is always enabled. Bot settings and credentials come only from the saved web configuration; new installations start in dry run.

Settings, beside Log out, opens a dialog with separate buttons for editing Login Credentials and API access/withdrawal targets. Bot Configuration groups purchase settings and the withdrawal threshold, each with a short explanation. Trading and dashboard groups the live-trading, automatic/manual withdrawal and Parqet switches with display currency. Disabled features also hide their controls and notes.

| Exchange | API permissions | Bitcoin withdrawal destination |
| --- | --- | --- |
| [Bitvavo](https://docs.bitvavo.com/docs/get-started/) | View access; Trade digital assets for live buys; Withdraw digital assets for withdrawals | Address and network saved and verified on Bitvavo |
| [Kraken](https://support.kraken.com/articles/360000919966-how-to-create-an-api-key) | Query Funds; Query Open/Closed Orders & Trades; Modify Orders for live buys; Withdraw Funds for withdrawals | Exact label of a saved Bitcoin withdrawal method, not the address itself |

**Test connection** reads balances without trading; progress and results appear beside the button. API access and internet are required even for dry runs. Exchange, credential, currency and trading-mode changes are blocked while transactions remain unresolved.

Settings persist as `config.json` in the named volume `bitcoin-dca-webui-data`; passwords are hashed and API credentials stay private. Empty API fields keep saved credentials. Protect this file and backups; never commit credentials or `.env`. All runtime state and activity also live in this volume; no host state directory is required. Reuse the same volume on redeployment. After setup and each restart, automatic API checks block new transactions until open exchange orders and Bitcoin withdrawals are closed. Unavailable status keeps the bot paused and is retried.

Compose manages the web server, volumes and restart policy. Docker settings are `WEB_UI_PORT` (6890), `LOG_LEVEL` (INFO) and `WEB_UI_TLS_HOSTS` (comma-separated IP addresses/hostnames). HTTPS is always enabled. Sessions expire after 30 minutes; sensitive changes require your current login password. See [Architecture and operation](docs/architecture.md) for storage and operation.

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
- **Period Analysis:** date filters, summary, activity status (confirmed, pending, failed or simulated) and CSV export. Activities cannot be deleted through the UI.

View preferences are saved in this browser; the To field resets to now on page load. Refreshes preserve existing values; withdrawal confirmations show the destination, gross BTC, fee and net BTC.

Display currency is set to EUR or USD in Settings. Reference BTC prices come from Kraken’s public [BTC/EUR](https://www.kraken.com/convert/btc/eur) and [BTC/USD](https://www.kraken.com/convert/btc/usd) pages, including when trading on Bitvavo. Saved or cached prices are labelled.

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
