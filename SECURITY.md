# Security

## Reporting

This is a personal portfolio repository with no deployed service. If you find a leaked
credential or a sensitive artifact, please open an issue **without pasting the value**.

## What is not in this repository

- wallet sessions, private keys, seed phrases
- API keys, webhook URLs, dashboard passwords
- account identifiers, balances, PnL, order history
- market data and runtime logs

## Known issues in the original deployment (being remediated)

| Issue | Severity | Status |
|---|---|---|
| Dashboard reachable via public tunnel with single-factor password | High | remediation planned |
| Master password stored in plaintext launcher scripts | High | **removed from this repo** |
| Empty password env silently granted master role | High | fix planned (fail-closed) |
| State file written non-atomically; force-kill on strategy switch | High | fix planned |
| No single-instance lock; two launch paths can double-start a bot | High | fix planned |
| Partial fills leave a resting residual order | Medium | fix planned |
| Mixed ledgers across two strategies | Medium | fix planned |

The remediation plan, ordering and rollback strategy are tracked outside this repository.
