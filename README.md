# BTC 5m Prediction Market Execution System

An end-to-end automated trading system for Binance Predict's **BTC Up/Down 5-minute** markets.
Built and operated solo, 24/7, on a Windows host.

> ⚠️ **This bot can lose money — including all of it.** Read [DISCLAIMER.md](DISCLAIMER.md) first.
> It ships with `risk.dry_run = true` and will not send an order until you turn that off.
>
> **Status: reference implementation.** Sanitized snapshot of a system that ran live on a
> Windows host. No secrets, account data, order history or market data are included. Several
> items in the [risk register](docs/risk-register.md) are still open; the plan is in
> [docs/roadmap.md](docs/roadmap.md).

---

## What it does

Every 5 minutes the system:

1. fetches the last closed 5-minute candles (never the in-progress one),
2. detects a run of consecutive same-direction candles,
3. looks up the historical reversal probability for that run length,
4. sizes a fractional-Kelly position against the live token ask,
5. places a limit order on the current round's UP or DOWN token,
6. waits for the round to settle, resolves WIN/LOSS from the position payload,
7. redeems the claim, records the cycle, and pushes a notification.

Two accounts run in isolation (separate wallet sessions, state files and config).
A web dashboard exposes state, config and kill switches.

## Quick start (for subscribers)

```powershell
git clone <this repo> btc5m && cd btc5m
powershell -ExecutionPolicy Bypass -File .\install.ps1
# edit config.json (wallet CLI path + account), then:
python scripts\doctor.py
```

`doctor.py` is read-only and must print `ready` before you start anything. The system ships
with `risk.dry_run = true`: it will log decisions for a full round but never send an order.
Flip it only after a dry run.

Full field reference: [docs/configuration.md](docs/configuration.md).

## Architecture

```
 Binance spot REST                    Binance Predict (market / order / position)
   /api/v3/klines                                  ^
        |  closed candles only                     |  wallet CLI (node subprocess, via local proxy)
        v                                          |
  [ signal ] -> [ probability table ] -> [ sizing ] -> [ order ] -> [ settle ] -> [ record ]
                                                            |            |           |
                                                            v            v           v
                                                      state file    redemption   JSONL + CSV
                                                            |
                        dashboard (HTTP :8600) <--- heartbeat / health / watchdog status
                                    ^
                          Cloudflare tunnel (opt-in)
```

| Layer | Implementation |
|---|---|
| Runtime | Python 3.12, single-threaded state machine |
| Trading channel | Node CLI wallet, one subprocess per call |
| Market data | Binance public REST with fallback endpoints |
| Storage | JSON state + JSONL events + CSV ledgers (no database) |
| Dashboard | `http.server` + single-page vanilla JS |
| Deployment | Windows scheduled task + supervisor scripts |
| Alerts | WeCom webhook (provider-agnostic notifier) |

## Engineering problems this project actually deals with

- **Session-based wallet auth.** The trading channel is a wallet with a QR-code login, not an
  API key. Distinguishing *expired session* from *network failure* matters: treating the latter
  as the former logs the operator out and kills the bot. The login supervisor probes
  `wallet status` first and only classifies a failure as auth after ruling out transport errors.
- **Unfilled limit orders are information.** A first-leg order that does not fill usually means
  the price moved in your favour. The system treats that as a SKIP, not a loss, and does not
  chase the next round.
- **Settlement payloads are inconsistent.** `result` can be empty, `isWinner` can be absent.
  The resolver uses a strict priority: `WIN/LOSS` -> `isWinner` -> `pnl` sign -> `canClaim`,
  and never defaults to loss.
- **Partial fills.** `filledShareQty > 0` does not mean "fully filled". Handling the residual
  order is an open item (see risk register).
- **Recovery.** State is written after every transition so a restart can resume an in-flight
  round instead of double-ordering.

## What is verified, and what is not

**Verified in code:** closed-candle-only signal input, settlement priority chain, hard-stop
fail-closed behaviour, two-account isolation, backtest with explicit fill assumptions
(`always / adverse / random / hybrid`).

**Not verified:** that the strategy has a positive edge. The probability table is an in-sample
estimate over 90 days; the live sample is far too small; fee-adjusted break-even is close enough
to the estimate that a zero-edge hypothesis cannot be rejected. Exact PnL figures are
intentionally omitted from this repository.

See [docs/verification.md](docs/verification.md) for the reasoning.

## Repository layout

```
config.example.json   every tunable; copy to config.json
install.ps1           one-shot setup (config, .env with random passwords, dirs, deps)
scripts/doctor.py     read-only pre-flight check
scripts/fetch_klines.py  download historical candles for the backtester
src/                  bot, dashboard, supervisors, backtester
src/legacy/           previous doubling strategy, kept for comparison only
tests/                pure-logic unit tests (no network, no wallet)
docs/                 architecture, state machine, risk register, verification, postmortem,
                      roadmap, configuration, publishing checklist
docs/internal/        original specs and operating notes (Chinese, paths templated)
```

## Tests

```bash
python -m unittest discover -s tests -v
```

The suite covers the settlement priority chain, streak detection (including doji), and the
Kelly sizing bounds. It never touches the network or the wallet, so it is safe to run anywhere.

## Reading order

1. [DISCLAIMER.md](DISCLAIMER.md)
2. [docs/architecture.md](docs/architecture.md)
3. [docs/state-machine.md](docs/state-machine.md)
4. [docs/risk-register.md](docs/risk-register.md)
5. [docs/verification.md](docs/verification.md)
6. [docs/postmortem-2026-11-09.md](docs/postmortem-2026-11-09.md)
7. [docs/configuration.md](docs/configuration.md)
8. [docs/roadmap.md](docs/roadmap.md)

## Security

No credentials, webhooks, wallet material, account identifiers or order history are included.
The original deployment used plaintext credentials in launcher scripts; that is one of the
findings being remediated. See [SECURITY.md](SECURITY.md).

## License

MIT. See [LICENSE](LICENSE).
