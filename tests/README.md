# Tests

```bash
python -m unittest discover -s tests -v
```

13 tests, no network, no wallet, no state. They run in a temporary `home`, so the repository
stays clean.

## What is covered

| Area | Cases |
|---|---|
| Settlement priority | explicit WIN/LOSS beats PnL; `isWinner` beats PnL; PnL sign; zero PnL falls back to `canClaim`; **ambiguous payload never becomes a fake LOSS** |
| Streak detection | 3 up -> DOWN, 3 down -> UP, doji breaks the run, short runs are not signals |
| Kelly sizing | never below `min_stake`, never above `balance x max_pct`, no-edge returns the floor |

## What is not covered yet

These require extracting pure functions out of the state machine (roadmap week 1-2):

- first leg unfilled -> SKIP, no second leg, loss streak unchanged
- later leg unfilled -> retried up to `1 + retry_count`, sizing step unchanged
- restart with a pending order -> resumes, never re-enters the same round
- atomic state write under crash injection
- single-instance lock rejects a second process
- partial fill cancels the residual order and records the real cost

The specification for those lives in [../docs/state-machine.md](../docs/state-machine.md) and
[../docs/risk-register.md](../docs/risk-register.md).
