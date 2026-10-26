# Bitvavo Bitcoin DCA Bot

A Docker service that buys a configurable base asset (BTC by default) with a configurable quote currency (EUR by default). It reads the current Bitvavo market rules, detects completed fiat deposits, and schedules purchases until the next configured deposit day.

## How the purchase schedule works

- The bot derives the cycle budget from all completed deposits in the configured quote currency since the current deposit cycle began. There is no fixed monthly budget setting.
- In live mode, the available Bitvavo balance also limits purchases. The bot keeps the configured fee buffer unspent.
- `BUY_AMOUNT` sets the desired amount of the base asset per purchase. `0` means the bot calculates the smallest valid amount from Bitvavo's market minimums. Configured amounts are rounded up to the market's allowed quantity precision.
- The next interval is recalculated from the remaining deposited funds, the minimum order value, the current price, and the time until the next deposit day. The first purchase in a cycle is due immediately once funds are available.
- The bot stores cycle spending and order state in `/data/state.json`. Keep the mounted `./state` directory persistent.
- If an order response is ambiguous, the bot checks the saved `clientOrderId` with Bitvavo before placing another order. If the order cannot be resolved, inspect it in Bitvavo before manually clearing `pending_order` from the state file.
- Live trading is disabled by default.

`DEPOSIT_DAY` defines the day of the month when a new cycle starts. The boundary uses the fixed time zone `Europe/Berlin`. If a month does not contain the selected day, its last calendar day is used. The bot counts only completed deposits visible in Bitvavo's deposit history; it will not buy until a deposit is detected. In live mode, the quote balance must also be sufficient.

## Configuration

The non-secret settings are configured directly in `docker-compose.yml`. Change their values there before starting the service.

| Variable | Default | Description |
| --- | --- | --- |
| `QUOTE_CURRENCY` | `EUR` | Fiat or quote currency used for deposits and the market, for example `EUR` or `USD`. |
| `BASE_ASSET` | `BTC` | Asset to buy. The market is built as `<BASE_ASSET>-<QUOTE_CURRENCY>`. |
| `BUY_AMOUNT` | `0` | Desired base-asset amount per order. `0` selects the minimum valid order amount based on Bitvavo's market rules. |
| `DEPOSIT_DAY` | `1` | Deposit-cycle boundary day, from `1` to `31`. Missing calendar days resolve to that month's last day. |
| `LIVE_TRADING` | `false` | Set to `true` to submit real orders. Keep `false` for dry-run logging. |
| `CHECK_INTERVAL_SECONDS` | `300` | How often to check deposits, balances, and market data. Values below `60` are clamped to `60`. This is a polling interval; purchase timing is calculated separately. |
| `FEE_BUFFER_PERCENT` | `1` | Percentage of cycle deposits left aside for fees and rounding. |
| `BITVAVO_API_KEY` | Required | Bitvavo API key. Keep it in the local, untracked `.env` file. |
| `BITVAVO_API_SECRET` | Required | Bitvavo API secret. Keep it in the local, untracked `.env` file. |
| `LOG_LEVEL` | `INFO` | Python logging level, such as `INFO`, `WARNING`, or `DEBUG`. |

The time zone (`Europe/Berlin`), state-file path (`/data/state.json`), and order `operatorId` (`1`) are fixed in the code and are not configurable.

`MONTHLY_BUDGET` is not used. The bot derives its budget from completed deposits recorded by Bitvavo.

### API credentials

Copy `.env.example` to `.env` and enter `BITVAVO_API_KEY` and `BITVAVO_API_SECRET` there:

```sh
cp .env.example .env
```

Docker Compose reads the `.env` file to fill the credential references in `docker-compose.yml`. `.env` is ignored by Git. Do not put API credentials in the versioned Compose file or commit them to this public repository.

Create a Bitvavo API key with **View access** and **Trade digital assets**. Do not enable **Withdraw digital assets**. If possible, restrict the key to the host's fixed IP address.

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
