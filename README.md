# Bitvavo Bitcoin DCA Bot

A Docker service that buys a configurable base asset (BTC by default) with a configurable quote currency (EUR by default). It reads the current Bitvavo market rules, detects completed fiat deposits, and schedules purchases until the next configured deposit day.

## Prerequisites

### Bitvavo API credentials

Copy `.env.example` to `.env` and enter `BITVAVO_API_KEY` and `BITVAVO_API_SECRET` there:

```sh
cp .env.example .env
```

Docker Compose reads the `.env` file to fill the credential references in `docker-compose.yml`. Do not commit `.env` or API credentials to this public repository. Without a `.dockerignore`, files in the project directory are also included in the Docker build context.

### Create the API key in Bitvavo

1. Enable two-factor authentication (2FA) on your Bitvavo account.
2. In Bitvavo, open **Settings → API → Add new API key** and give the key a recognizable name.
3. If your server has a fixed public IP address, add it to the key's IP whitelist.
4. Enable **View access** and **Trade digital assets** for deposits, balances, transaction history, and orders.
5. Enable **Withdraw digital assets** only if you intend to use automatic withdrawals. Add and verify the destination wallet address in Bitvavo's address book before entering it in `WITHDRAW_ADDRESS` and enabling withdrawals.
6. Confirm with 2FA, then copy the API key and secret into your local `.env` file. Store the secret securely; Bitvavo says it cannot be retrieved later.

See Bitvavo's [API key setup guide](https://docs.bitvavo.com/docs/get-started/) for the current dashboard steps. API withdrawals do not require 2FA or email confirmation, and funds sent to an incorrect address cannot be recovered. Keep `WITHDRAWALS_ENABLED` set to `false` unless the address and network have been checked carefully. Automatic withdrawals require the additional **Withdraw digital assets** permission; the read-only dashboard and Parqet exports do not.


## How the purchase schedule works

- The bot derives the cycle budget from all completed deposits in the configured quote currency since the current deposit cycle began. There is no fixed monthly budget setting.
- In live mode, the available Bitvavo balance also limits purchases. The bot keeps the configured fee buffer unspent.
- `BUY_AMOUNT` sets the desired amount of the base asset per purchase. `0` means the bot calculates the smallest valid amount from Bitvavo's market minimums. Configured amounts are rounded up to the market's allowed quantity precision.
- The next interval is recalculated from the remaining deposited funds, the minimum order value, the current price, and the time until the next deposit day. The first purchase in a cycle is due immediately once funds are available.
- Docker Compose manages the bot's internal state automatically through the `./state` mount; no manual state configuration is needed.
- If an order response is ambiguous, the bot checks the saved `clientOrderId` with Bitvavo before placing another order. If the order cannot be resolved, inspect it in Bitvavo before manually clearing `pending_order` from the state file.
- Live trading is disabled by default.

`DEPOSIT_DAY` defines the day of the month when a new cycle starts. `TIMEZONE` sets the time zone used for that boundary (default: `Europe/Berlin`). If a month does not contain the selected day, its last calendar day is used. The bot counts only completed deposits visible in Bitvavo's deposit history; it will not buy until a deposit is detected. In live mode, the quote balance must also be sufficient.

## Automatic BTC withdrawals

Automatic withdrawals are disabled by default. To enable them, set `WITHDRAWALS_ENABLED: "true"`, set `LIVE_TRADING: "true"`, and enter a Bitcoin address and network in `docker-compose.yml`. The destination address must already be added and verified in your Bitvavo address book.

At each check, the bot values the available BTC balance using the current `BTC-<QUOTE_CURRENCY>` price. Once it reaches `WITHDRAW_THRESHOLD_QUOTE`, the bot requests a withdrawal of the entire available BTC balance. Bitvavo deducts the withdrawal fee from that amount, so the wallet receives the balance minus the fee. The threshold uses `QUOTE_CURRENCY`; for example, with `QUOTE_CURRENCY: EUR`, a value of `1000` means €1,000.

This feature needs the additional **Withdraw digital assets** API permission. Verify the address and network carefully before enabling it. See Bitvavo's [crypto withdrawal API](https://docs.bitvavo.com/docs/rest-api/withdraw-crypto-assets/) for required fields and behavior; the API withdrawal safety requirements are listed under Prerequisites.

## Portfolio web UI

The dashboard is enabled by default and is available at [http://localhost:8080](http://localhost:8080) on the Docker host. Docker Compose binds the port to `127.0.0.1`, so it is not exposed to the home network or the public internet.

It shows the current available BTC balance and quote-currency value, plus purchases, average purchase price, fees, completed withdrawals, and net BTC purchased minus withdrawn for the selected period. Choose a period between two completed BTC withdrawals from the interval selector, or set custom start and end dates. The displayed period can be exported on demand as a Parqet-compatible CSV and downloaded directly from the page.

The UI is read-only. It uses the existing Bitvavo read access and does not expose any trading or withdrawal controls. Set `WEB_UI_ENABLED: "false"` to disable it.

## Parqet CSV exports

After the first startup, the bot watches Bitvavo for completed BTC withdrawals. Every later completed withdrawal creates a CSV file in `./exports` containing all BTC buy transactions since the previous completed withdrawal and the withdrawal itself. This includes buys made manually or by other tools on the same Bitvavo account, not only orders placed by this bot.

The CSV uses Parqet's semicolon-separated activity format with `Buy` and `TransferOut` activities. Trade fees are included in the configured quote currency; BTC-denominated trade fees are converted using the trade's implied price. The BTC withdrawal fee is converted using the closing price of the one-minute candle containing the withdrawal timestamp. This is a valuation for Parqet's fee field; the exact fee remains denominated in BTC at Bitvavo.

On first startup, the latest already completed BTC withdrawal becomes the starting point, so only subsequent withdrawals create exports. If there is no prior BTC withdrawal, the first export includes BTC buys found in Bitvavo history up to that first withdrawal. Import each generated `parqet_btc_*.csv` file into Parqet using its CSV activity import. See Parqet's [CSV import format](https://faq.parqet.com/de/articles/870989-csv-import-aktivitaten-aus-einer-eigenen-csv-datei-importieren).

Exports require Bitvavo read access to transaction and withdrawal history. No withdrawal permission is needed. The host's `./exports` directory is mounted into the container and receives the generated files.

## Configuration

The non-secret settings are configured directly in `docker-compose.yml`. Change their values there before starting the service.

| Variable | Default | Description |
| --- | --- | --- |
| `QUOTE_CURRENCY` | `EUR` | Fiat or quote currency used for deposits and the market, for example `EUR` or `USD`. |
| `BASE_ASSET` | `BTC` | Asset to buy. The market is built as `<BASE_ASSET>-<QUOTE_CURRENCY>`. |
| `BUY_AMOUNT` | `0` | Desired base-asset amount per order. `0` selects the minimum valid order amount based on Bitvavo's market rules. |
| `DEPOSIT_DAY` | `1` | Deposit-cycle boundary day, from `1` to `31`. Missing calendar days resolve to that month's last day. |
| `TIMEZONE` | `Europe/Berlin` | Time zone used to determine the deposit-cycle boundary. |
| `LIVE_TRADING` | `false` | Set to `true` to submit real orders. Keep `false` for dry-run logging. |
| `CHECK_INTERVAL_SECONDS` | `300` | How often to check deposits, balances, and market data. Values below `60` are clamped to `60`. This is a polling interval; purchase timing is calculated separately. |
| `FEE_BUFFER_PERCENT` | `1` | Percentage of cycle deposits left aside for fees and rounding. |
| `WITHDRAWALS_ENABLED` | `false` | Set to `true` to enable automatic BTC withdrawals. Requires `LIVE_TRADING: "true"`. |
| `WITHDRAW_THRESHOLD_QUOTE` | `1000` | Withdraw when the available BTC balance reaches this value in `QUOTE_CURRENCY`. |
| `WITHDRAW_ADDRESS` | Empty | Destination Bitcoin address. Required when automatic withdrawals are enabled; add and verify it in Bitvavo first. |
| `WITHDRAW_NETWORK` | `BTC` | Bitvavo network for the withdrawal. |
| `WEB_UI_ENABLED` | `true` | Enables the local read-only portfolio dashboard. |
| `BITVAVO_API_KEY` | Required | Bitvavo API key. Keep it in the local, untracked `.env` file. |
| `BITVAVO_API_SECRET` | Required | Bitvavo API secret. Keep it in the local, untracked `.env` file. |
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
