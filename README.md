# Bitcoin DCA Bot for Bitvavo and Kraken

A Docker service that buys a configurable base asset (BTC by default) with a configurable quote currency (EUR by default) on Bitvavo or Kraken. Select the exchange and use its separate API credentials. The bot reads the selected exchange market rules, detects completed fiat deposits, and schedules purchases until the next deposit day. A local web UI visualizes Bitcoin purchases, fees, and withdrawals, and generates Parqet-compatible CSV exports.

## Prerequisites

### Exchange API credentials

Copy `.env.example` to `.env`. Set EXCHANGE in docker-compose.yml to BITVAVO or KRAKEN, then fill in the matching API key and secret:

```sh
cp .env.example .env
```

Docker Compose reads .env for the credentials. Only credentials for the selected exchange are required. Do not commit .env or API credentials to this public repository.

### Create API keys

**Bitvavo:** Enable 2FA and create an API key under Settings → API. Grant View access and Trade digital assets. Add Withdraw digital assets only if you plan to use automatic withdrawals. Copy the key and secret into BITVAVO_API_KEY and BITVAVO_API_SECRET. See Bitvavo's [API key setup guide](https://docs.bitvavo.com/docs/get-started/).

**Kraken:** Create a Spot API key and copy it into KRAKEN_API_KEY and KRAKEN_API_SECRET. Grant permissions to query funds and ledger entries, modify orders, and query closed orders and trades. Add Withdraw Funds only for automatic withdrawals. See Kraken's [API key guide](https://support.kraken.com/in/articles/360000919966-how-to-create-an-api-key?mode=consumerapp). Kraken withdrawal support uses legacy funding endpoints that remain active but Kraken recommends migrating to its newer Funding API.

For withdrawals, Bitvavo uses a verified WITHDRAW_ADDRESS and WITHDRAW_NETWORK. Kraken uses an existing saved Bitcoin withdrawal method selected by KRAKEN_WITHDRAW_KEY; this is the method name, not a wallet address. Verify the destination and network at the exchange before enabling withdrawals.


## How the purchase schedule works

- The bot derives the cycle budget from all completed deposits in the configured quote currency since the current deposit cycle began. There is no fixed monthly budget setting.
- In live mode, the available Bitvavo balance also limits purchases. The bot keeps the configured fee buffer unspent.
- `BUY_AMOUNT` sets the desired amount of the base asset per purchase. `0` means the bot calculates the smallest valid amount from Bitvavo's market minimums. Configured amounts are rounded up to the market's allowed quantity precision.
- The next interval is recalculated from the remaining deposited funds, the minimum order value, the current price, and the time until the next deposit day. The first purchase in a cycle is due immediately once funds are available.
- Docker Compose manages the bot's internal state automatically through the `./state` mount; no manual state configuration is needed.
- If an order response is ambiguous, the bot checks the saved `clientOrderId` with the selected exchange before placing another order. If the order cannot be resolved, inspect it at the exchange before manually clearing `pending_order` from the state file.
- Live trading is disabled by default.

`DEPOSIT_DAY` defines the day of the month when a new cycle starts. `TIMEZONE` sets the time zone used for that boundary (default: `Europe/Berlin`). If a month does not contain the selected day, its last calendar day is used. The bot counts only completed deposits visible in Bitvavo's deposit history; it will not buy until a deposit is detected. In live mode, the quote balance must also be sufficient.

## Automatic BTC withdrawals

Automatic withdrawals are disabled by default. Enable WITHDRAWALS_ENABLED and LIVE_TRADING to use them.

At each check, the bot values the available BTC balance using BTC-<QUOTE_CURRENCY>. Once it reaches WITHDRAW_THRESHOLD_QUOTE, it requests the available BTC balance less the applicable withdrawal fee. The threshold uses QUOTE_CURRENCY.

For Bitvavo, configure the verified address and network with WITHDRAW_ADDRESS and WITHDRAW_NETWORK. For Kraken, add a Bitcoin withdrawal method in Kraken and set its saved method name as KRAKEN_WITHDRAW_KEY. Kraken withdrawal requests are not automatically retried after an ambiguous response; check the Kraken account before taking action.


## Portfolio web UI

The dashboard is enabled by default, uses the exchange selected in EXCHANGE, and is available at [http://localhost:8080](http://localhost:8080) on the Docker host by default. Set `WEB_UI_PORT` in `.env` to change both the local host port and the port used inside the container. Docker Compose binds the port to `127.0.0.1`, so it is not exposed to the home network or the public internet.

It shows the current available BTC balance and quote-currency value, plus purchases, average purchase price, fees, completed withdrawals, and net BTC purchased minus withdrawn for the selected period. Choose a period between two completed BTC withdrawals from the interval selector, or set custom start and end dates. The displayed period can be exported on demand as a Parqet-compatible CSV and downloaded directly from the page.

The UI is read-only. It uses the selected exchange's API credentials and does not expose any trading or withdrawal controls. Set `WEB_UI_ENABLED: "false"` to disable it.

## Parqet CSV exports

After the first startup, the bot watches the selected exchange for completed BTC withdrawals. Every later completed withdrawal creates a CSV file in `./exports` containing all BTC buy transactions since the previous completed withdrawal and the withdrawal itself. This includes buys made manually or by other tools on the same exchange account, not only orders placed by this bot.

The CSV uses Parqet's semicolon-separated activity format with `Buy` and `TransferOut` activities. Trade fees are included in the configured quote currency; BTC-denominated trade fees are converted using the trade's implied price. The BTC withdrawal fee is converted using the closing price of the one-minute candle containing the withdrawal timestamp. This is a valuation for Parqet's fee field; the exact fee remains denominated in BTC at the selected exchange.

On first startup, the latest already completed BTC withdrawal becomes the starting point, so only subsequent withdrawals create exports. If there is no prior BTC withdrawal, the first export includes BTC buys found in exchange history up to that first withdrawal. Import each generated `parqet_btc_*.csv` file into Parqet using its CSV activity import. See Parqet's [CSV import format](https://faq.parqet.com/de/articles/870989-csv-import-aktivitaten-aus-einer-eigenen-csv-datei-importieren).

Exports require read access for the selected exchange to transaction and withdrawal history. No withdrawal permission is needed. The host's `./exports` directory is mounted into the container and receives the generated files.

## Configuration

The non-secret settings are configured directly in `docker-compose.yml`. Change their values there before starting the service.

| Variable | Default | Description |
| --- | --- | --- |
| `EXCHANGE` | `BITVAVO` | Select `BITVAVO` or `KRAKEN`; determines which API credentials are required. |
| `QUOTE_CURRENCY` | `EUR` | Fiat or quote currency used for deposits and the market, for example `EUR` or `USD`. |
| `BASE_ASSET` | `BTC` | Asset to buy. The market is built as `<BASE_ASSET>-<QUOTE_CURRENCY>`. |
| `BUY_AMOUNT` | `0` | Desired base-asset amount per order. `0` selects the minimum valid order amount based on the selected exchange's market rules. |
| `DEPOSIT_DAY` | `1` | Deposit-cycle boundary day, from `1` to `31`. Missing calendar days resolve to that month's last day. |
| `TIMEZONE` | `Europe/Berlin` | Time zone used to determine the deposit-cycle boundary. |
| `LIVE_TRADING` | `false` | Set to `true` to submit real orders. Keep `false` for dry-run logging. |
| `CHECK_INTERVAL_SECONDS` | `300` | How often to check deposits, balances, and market data. Values below `60` are clamped to `60`. This is a polling interval; purchase timing is calculated separately. |
| `FEE_BUFFER_PERCENT` | `1` | Percentage of cycle deposits left aside for fees and rounding. |
| `WITHDRAWALS_ENABLED` | `false` | Set to `true` to enable automatic BTC withdrawals. Requires `LIVE_TRADING: "true"`. |
| `WITHDRAW_THRESHOLD_QUOTE` | `1000` | Withdraw when the available BTC balance reaches this value in `QUOTE_CURRENCY`. |
| `WITHDRAW_ADDRESS` | Empty | Bitvavo destination Bitcoin address; required for Bitvavo withdrawals. Add and verify it in Bitvavo first. |
| `WITHDRAW_NETWORK` | `BTC` | Bitvavo network for the withdrawal. |
| `WEB_UI_ENABLED` | `true` | Enables the local read-only portfolio dashboard. |
| `WEB_UI_PORT` | `8080` | Dashboard port on the host and inside the container. Set it in `.env` to override the default. |
| `BITVAVO_API_KEY` | Required for Bitvavo | Bitvavo API key. Keep it in the local, untracked `.env` file. |
| `BITVAVO_API_SECRET` | Required for Bitvavo | Bitvavo API secret in the local `.env` file. |
| `KRAKEN_API_KEY` | Required for Kraken | Kraken API key in the local `.env` file. |
| `KRAKEN_API_SECRET` | Required for Kraken | Kraken API secret in the local `.env` file. |
| `KRAKEN_WITHDRAW_KEY` | Kraken withdrawals | Name of an existing Kraken Bitcoin withdrawal method. |
| `LOG_LEVEL` | `INFO` | Python logging level, such as `INFO`, `WARNING`, or `DEBUG`. |


## Run with Docker Compose

Start with `LIVE_TRADING: "false"` and review the logs:

```sh
docker compose up -d --build
docker compose logs -f
```

To enable real orders, set `LIVE_TRADING: "true"` in `docker-compose.yml` and recreate the service:

```sh
docker compose up -d --build
```

## GitHub Container Registry

The workflow in `.github/workflows/publish.yml` builds and publishes the image to GHCR on pushes to `main`, version tags such as `v1.0.0`, and manual workflow dispatches. Runtime credentials belong on the host, never in the image or repository.

## Limitations

Market orders do not guarantee an execution price. With a fixed `BUY_AMOUNT`, the quote-currency cost is estimated from the latest price and can differ at execution; fees can also vary. Monitor the logs and keep the host's IP stable if you use IP whitelisting. This project is a technical example, not investment advice.
