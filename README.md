# actual-transaction-email-splitter

Receive forwarded order-confirmation emails, parse them, and post a split
transaction to [Actual Budget](https://actualbudget.org/) so each line item
lands as its own subtransaction. Built around CloudMailin's email→webhook
service so you don't have to run an MTA.

Designed for the case where one credit-card swipe (e.g. an Amazon order)
covers several different budget categories, and re-splitting the merged
transaction by hand in Actual is the part that hurts.

## How it works

```
sender → forward → CloudMailin (inbound MX) ──HTTPS POST (Basic Auth)──▶ this service
                                                                       │
                                                  parse → categorize ──┤
                                                                       ▼
                                                                  Actual Server
```

1. CloudMailin receives mail at an MX you control and POSTs the parsed
   email as JSON to `POST /webhook/cloudmailin` with HTTP Basic Auth.
2. The service verifies the credentials, allowlists the `From:` address,
   dedups by `Message-ID`, and archives the raw payload.
3. A vendor dispatcher picks a parser by `From:` domain + subject and
   produces a `ParsedOrder` (vendor, order_id, total, card_last4, line
   items).
4. `card_last4` is mapped to an Actual account ID via config.
5. For each line item, the categorizer queries Actual for recent
   transactions with a similar memo and copies the modal category. No
   match → uncategorized.
6. The service posts one parent transaction with N subtransactions to
   the resolved account, and pings ntfy (optional).

## Configuration

All config is environment-driven so the same image runs in your homelab
overlay and in someone else's deployment.

| Variable | Required | Default | Notes |
|---|---|---|---|
| `CLOUDMAILIN_BASIC_USER` | yes | — | Username configured in CloudMailin's target Basic Auth |
| `CLOUDMAILIN_BASIC_PASS` | yes | — | Password configured in CloudMailin's target Basic Auth |
| `ACTUAL_URL` | yes | — | e.g. `http://actualbudget:5006` |
| `ACTUAL_PASSWORD` | yes | — | Actual server password |
| `ACTUAL_BUDGET_SYNC_ID` | yes | — | Sync ID of the budget file |
| `ACTUAL_ENCRYPTION_PASSWORD` | no | — | Only if E2EE is enabled |
| `SENDERS_PATH` | no | `/config/senders.yml` | Allowlist of `From:` addresses |
| `CARD_ROUTING_PATH` | no | `/config/card-routing.yml` | last4 → account name/ID |
| `ARCHIVE_DIR` | no | `/data/archive` | Raw payload archive |
| `DB_PATH` | no | `/data/splitter.db` | SQLite dedup state |
| `PUSHGATEWAY_URL` | no | — | If set, pushes metrics per request |
| `NTFY_URL` | no | — | e.g. `https://ntfy.example.com/alerts` |
| `LOOKBACK_DAYS` | no | `180` | Category history window |

### `senders.yml`

```yaml
allowed:
  - you@example.com
  - spouse@example.com
```

### `card-routing.yml`

```yaml
# last 4 digits of the card → Actual account name (resolved via API at
# startup, cached). Use `default:` for the fallback.
cards:
  "1234": "Amazon Visa"
  "5678": "Joint Checking"
default: "Amazon Visa"
```

## Endpoints

- `POST /webhook/cloudmailin` — primary ingest
- `GET /healthz` — liveness
- `GET /metrics` — Prometheus (also pushed if `PUSHGATEWAY_URL` set)

## Adding a parser

Drop a module under `src/actual_tx_splitter/parsers/` exposing:

```python
def matches(email: dict) -> bool: ...
def parse(email: dict) -> ParsedOrder: ...
```

Register it in `parsers/__init__.py`. See `parsers/amazon.py`.

## Development

```sh
uv sync
uv run pytest
uv run uvicorn actual_tx_splitter.main:app --reload
```

## Deployment

Bring up with the bundled compose file:

```sh
cp .env.example .env  # fill it in
docker compose up -d --build
```

For a homelab-style overlay (Traefik, Kuma, network joins), see
[kjaymiller/homelab](https://github.com/kjaymiller/homelab) under
`compose/actual-transaction-email-splitter/`.

## License

MIT.
