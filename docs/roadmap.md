# Roadmap

Ordered by risk, not by interest. Each week has a single verifiable deliverable.

| Week | Theme | Deliverable | Acceptance |
|---|---|---|---|
| 1 | Repo hygiene + test baseline | `git`, `.gitignore`, settlement/streak/sizing extracted and tested | **done**: 13 tests green, clean history, no secret in repo |
| 2 | State integrity | atomic write, fail-closed load, single-instance lock, idempotent submit | kill -9 during save x100 -> state always parses; second instance exits |
| 3 | Observability | structured run log, heartbeat age, no-order alert, log rotation | simulated stall raises an alert within 10 min |
| 4 | Statistical honesty | walk-forward split, fee-adjusted EV with CI | a written verdict on whether edge > 0 is supported |
| 5 | Data layer | SQLite schema for orders/cycles/events, migration, reconciliation script | reconciles the known historical gaps automatically |
| 6 | Service boundary | persistent trading client with typed errors, timeout, circuit breaker; FastAPI dashboard | 50 % timeout injection -> clean error, no silent `None` |
| 7 | Chain fundamentals | RPC reads, ERC-20 log decoding, small indexer, Foundry contract + tests | `forge test` green; can explain msg.sender under a proxy |
| 8 | Portfolio + narrative | README, C4 diagram, incident write-up, 8-min demo | a stranger can name the three weakest points |

## Non-goals

- Changing strategy parameters. Engineering fixes and risk-parameter changes must not share a
  measurement window.
- Making the system faster before making it correct. Latency work is week 6, after state and
  ledger integrity.
