# Risk register

Severity reflects **blast radius on capital or control**, not code smell.
Status: `open` = present in the snapshot, `planned` = fix designed, `done` = fixed.

| ID | Severity | Issue | Evidence | Status |
|---|---|---|---|---|
| R-01 | High | Dashboard exposed to the internet behind a single password; master role can market-close positions and change stake | tunnel config; `dashboard_server.py` command handler | planned |
| R-02 | High | State written with a truncating open, no temp-file + rename; force-kill on strategy switch can leave a truncated state that silently loads as "fresh" | `predict_live_bot_kelly.py` `save_state` / `load_state`; `botctl.py` | planned |
| R-03 | High | No single-instance lock. A legacy `.bat` starts the bot without `--account-id`, while the supervisor matches processes on that exact string -> a second instance can be launched against the same account | `start_bot_A.bat`; `watchdog.py` | planned |
| R-04 | High | Empty password environment is treated as master instead of refusing to start | `dashboard_server.py` auth helper | planned |
| R-05 | Medium | `filledShareQty > 0` is treated as a full fill; the residual resting order is never cancelled and is no longer tracked | `predict_live_bot_kelly.py` `settle` | planned |
| R-06 | Medium | Two strategies write to the same per-account ledger; cycle counters are seeded from that mixed file | `trade_recorder.py`; `init_cycle_stats` | planned |
| R-07 | Medium | Cycle numbers are reused when a cycle aborts before fill, so events cannot be joined one-to-one | `reset_cycle` | planned |
| R-08 | Medium | No freshness assertion on candles; a stale feed produces a signal for a round that no longer exists | `klines` / `market` | planned |
| R-09 | Medium | Settlement runs on the main thread and can block 5+ minutes; STOP/PAUSE are only checked between rounds | `settle` call sites | planned |
| R-10 | Medium | No automated tests, although the original spec lists ten mandatory acceptance cases | `tests/` empty | planned |
| R-11 | Low | Dead code paths (a disabled six-streak guard) still carry comments describing active behaviour | `update_six_block`, `six_block_skip` | planned |
| R-12 | Low | Order lookup scans only the last 20 orders; a busy account can miss its own order | `order_by_id` | planned |
| R-13 | Low | Config hot-reload mutates a global dict field by field; a bad value leaves the config half-applied | `load_cfg` | planned |
| R-14 | Low | No version control, dependency lock, or CI in the original working directory | — | done here (repo created) |

## Impact notes

**R-01 / R-04** are the only findings that can lose money without any strategy being wrong.
A single password over a public endpoint that can market-sell at 10 % slippage is a
control-plane compromise, not a trading risk.

**R-02 / R-03** are the reason "it worked for weeks" is not evidence of correctness: they only
fail on crash or double-start, and when they fail they corrupt the ledger and the position
tracking at the same time.

**R-05** is the subtlest. A partial fill leaves a live order that the state machine has already
forgotten. The position that eventually results is real money with no owning cycle.

## Deliberately not changed

Strategy parameters (`min_streak`, limit price, Kelly fraction, drawdown threshold) are
**out of scope**. Changing risk parameters and fixing engineering defects in the same window
makes it impossible to attribute a change in results to either one.
