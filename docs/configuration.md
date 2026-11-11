# Configuration

Two files, two purposes:

| File | Committed? | Contains | Purpose |
|---|---|---|---|
| `config.json` | no (gitignored) | paths, accounts, strategy numbers | shareable, no secrets |
| `.env` | no | passwords, webhook URLs | secrets only |

Both are created by `install.ps1`. `config.example.json` is the template.

**Precedence:** CLI flag > environment variable > `config.json` > built-in default.
Secrets are read from the environment or `.env` **only** — never from `config.json`.

---

## `runtime`

| Key | Default | Meaning |
|---|---|---|
| `python` | `python` | interpreter used to spawn helper processes |
| `node` | `node` | Node.js binary that runs the wallet CLI |
| `baw_js` | `""` | **required**: absolute path to `@binance/agentic-wallet/dist/index.js` |
| `baw_profile` | `~/.baw` | default wallet session directory |
| `proxy_url` | `http://127.0.0.1:7897` | local proxy the wallet CLI must use; empty = direct |
| `timezone_offset` | `8` | hours from UTC for logs and cycle boundaries |
| `cloudflared` | `""` | optional path to `cloudflared.exe` |
| `cloudflared_config` | `""` | optional tunnel config path |
| `tunnel_name` | `""` | optional tunnel name; all three empty = no tunnel |

## `paths`

`state`, `logs`, `data` — relative to `home` (the repo root by default). Created on demand.

## `accounts`

An array; add one entry per account. Each account gets its own wallet session, state file and ledger.

| Key | Meaning |
|---|---|
| `id` | `A`, `B`, ... used in file names and CLI flags |
| `name` | display label |
| `asset` | `BTC` or `ETH` |
| `base_stake` | first-leg stake in USDT |
| `wallet_profile` | wallet session directory for this account |
| `baw_js` | optional per-account CLI path (otherwise `runtime.baw_js`) |
| `enabled` | set `false` to keep the config but not start it |

## `exchange`

| Key | Default | Meaning |
|---|---|---|
| `chain_id` | `56` | chain used by the wallet CLI (`--binanceChainId`) |
| `symbol_suffix` | `USDT` | appended to `accounts[].asset` |
| `category` | `["crypto","up-down"]` | market catalog filter |
| `timeframe` | `5 min` | market duration |
| `api_base` / `api_fallback` | Binance public REST | candle source |

## `strategy`

`min_streak`, `max_losses`, `retry_count`, `price`, `carry_price`, `min_ask`,
`profit_boost_pct`, `profit_boost_base`, `profit_boost_add`, `profit_boost_mode`.

`price` is the limit price per share. At `0.51` with a 2 % taker fee the break-even
probability is about `0.52` — anything below that loses money even at 50/50.

## `sizing`

| Key | Meaning |
|---|---|
| `mode` | `kelly` or `martingale` |
| `p` | assumed win probability (overridden per level by `state/kelly_p_table.json` if present) |
| `frac` | fraction of full Kelly |
| `max_pct` | hard cap per order as a fraction of balance |
| `min_edge` | skip the round if `p - price` is below this |
| `min_stake` | floor in USDT |

## `risk`

| Key | Default | Meaning |
|---|---|---|
| `dry_run` | `true` | **true = never send an order.** Set to `false` only after a full dry run |
| `hard_stop_mode` | `drawdown` | `none`, `drawdown` or `loss_cycles` |
| `hard_stop_drawdown` | `20.0` | pause when cumulative PnL falls this far from its peak |
| `hard_stop_loss_cycles` | `0` | pause after N consecutive losing cycles |

A hard stop writes a `PAUSE` file and never resumes by itself.

## `dashboard`

`bind` (default `127.0.0.1` — keep it that way), `port`, `user`, `refresh_sec`,
`slow_refresh_sec`. If `DASH_PASS` is empty the dashboard refuses to start.

## `notify`

`enabled` and `provider` (`wecom`, `dingtalk`, `pushplus`, `bark`, `serverchan`, `ntfy`).
The webhook/token comes from `.env`:

```
NOTIFY_WEBHOOK=https://...
NOTIFY_TOKEN=...
NOTIFY_TOPIC=...
```

## `network.proxy`

Only used by `vpn_watchdog.py`. Leave empty to disable it entirely (it will idle, not act).

`host`, `port`, `clash_api`, `selector`, `nodes[]`, `app_exe`, `kill_names[]`, `helper_exe`.

## `backtest`

`data` (CSV path under `data/`), `fill_model`, `fee_bps`.

---

## Minimal working example

```json
{
  "runtime": { "baw_js": "C:/Users/me/AppData/Roaming/npm/node_modules/@binance/agentic-wallet/dist/index.js" },
  "accounts": [
    { "id": "A", "name": "main", "asset": "BTC", "base_stake": 1.0,
      "wallet_profile": "C:/Users/me/.baw", "enabled": true }
  ]
}
```

Everything else falls back to the defaults above. Then:

```
python scripts/doctor.py     # must print "ready"
python src/predict_live_bot_kelly.py --account-id A
```

With `risk.dry_run = true` the bot logs `[DRY-RUN] would place ...` every time a signal
fires and never calls `place-order`.
