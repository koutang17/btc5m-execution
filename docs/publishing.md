# Publishing checklist

Everything in the repository is already sanitized. This is the remaining work on the hosting side.

## 1. Create the repository

- Name: `btc5m-execution` (or similar).
- Description: *Automated execution system for Binance Predict 5-minute up/down markets — Python, wallet CLI, Windows deployment.*
- Topics: `trading-bot` `prediction-markets` `binance` `python` `execution-engine` `quant`
- Public, with **Issues enabled** and Discussions optional.

## 2. Before the first push

```bash
git status                 # must be clean
git ls-files | wc -l       # ~50 files
git grep -nE "qyapi\.weixin\.qq\.com/cgi-bin/webhook|BEGIN .*PRIVATE KEY" || echo "clean"
git ls-files | grep -E "^(config\.json|\.env)$" || echo "no local config tracked"
```

Confirm `config.json`, `.env`, `state/`, `logs/`, `data/` are **not** tracked.

## 3. Put the risk warning where it cannot be missed

- Keep the warning block at the top of `README.md`.
- Pin an issue titled "Read this before running on real funds" linking `DISCLAIMER.md`.
- In the repo *About* panel, do not imply profitability.

## 4. Set authorship

```bash
git config user.name  "Your Name"
git config user.email "you@example.com"
git commit --amend --reset-author --no-edit
```

`LICENSE` currently says *The BTC5M Authors*. Replace it with your name if you want the
attribution to be personal.

## 5. Verify CI

The workflow runs on Python 3.10 / 3.11 / 3.12:

- byte-compiles `src/` and `scripts/`
- runs the unit suite
- greps for known secret patterns and personal paths

A red CI must block the push.

## 6. What NOT to publish

- the live `kelly_p_table.json` (it is your calibration, not a default)
- any account name, balance, order id or PnL
- the dashboard tunnel hostname
- wallet session directories

## 7. After publishing

- Add a screenshot of the **dashboard with sample data only** if you want visual appeal.
- Write one paragraph in the README about what you would do next; reviewers read for judgment,
  not for polish.
