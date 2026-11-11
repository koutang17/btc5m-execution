# Postmortem — 2026-11-09 — "no market to trade"

## Summary

For a period during the day the bot could not resolve the current 5-minute round and therefore
placed no orders. It was not a balance problem, not a limit-price problem and not an auth
problem. Separately, a reconciliation pass found that several completed cycles had never been
written to the cycle ledger, so dashboard statistics understated losses.

## Timeline (as reconstructed from the audit note)

| Time | Event |
|---|---|
| — | Market catalog query returns **zero** markets for the crypto/up-down category |
| — | Symbol search returns only *future* rounds, never the current one |
| — | `market(target)` returns `None`; the bot retries and eventually reports "no market" |
| ~23:05 | A fallback query shape starts returning the current round; one fill is recorded |
| 23:11 | Manual audit confirms the account is connected and trading; the incident is closed |

## Root causes

1. **A single query shape was assumed to be stable.** The catalog endpoint changed behaviour
   (returned an empty array instead of an error), which is the worst failure mode: it looks
   like "no data" rather than "call failed".
2. **The fallback was added after the fact** and only covers one of the two shapes.
3. **Nothing alerted on "N consecutive rounds with no order."** The supervisor only checks that
   the *process* is alive. A bot that is running but structurally unable to trade is invisible.

## Contributing factors

- The cycle ledger had gaps for several historical cycles. Root cause: code paths that reset a
  cycle (six-streak guard, manual cycle termination, manual stop/restart) did not write a cycle
  row. The state file was correct; the ledger was not.
- Statistics were computed from the ledger, so the gap silently biased the reported numbers.

## Actions taken

- Added a symbol search fallback ahead of the category list.
- Documented that the state file's cumulative PnL and the settlement events are authoritative;
  the cycle ledger is a detail view, not a source of truth.
- Wrote the audit procedure now in [verification.md](verification.md) §3.

## Actions still open

- Write a cycle row on **every** exit path (R-06/R-07).
- Alert on "no order in N rounds" and on stale candle data (R-08, R-09).
- Move the market lookup behind a client with typed errors so "empty" and "failed" cannot be
  confused (roadmap week 6).

## Lesson

The most expensive failures in this system were not wrong predictions. They were **silent
success paths**: a query that returns `[]`, a state file that loads as blank, a process that is
alive but idle. Liveness is not correctness.
