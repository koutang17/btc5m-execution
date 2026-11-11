# Disclaimer

**This software can lose money. It can lose all of it, and it can lose more than you deposited
if you use leverage elsewhere on the same account.**

## Not financial advice

Nothing in this repository is investment, legal or tax advice. The author is not a registered
investment adviser. Historical probabilities, backtests and live PnL shown anywhere in the
project are **not** predictions.

## No warranty

The software is provided "as is", without warranty of any kind, express or implied, including
but not limited to the warranties of merchantability, fitness for a particular purpose and
non-infringement. See [LICENSE](LICENSE).

## You are responsible for

- every order the bot sends on your account, and for the API/wallet credentials it uses;
- checking that automated trading of prediction markets is legal where you live;
- tax reporting on your results;
- the security of the machine that runs it (the wallet CLI holds a live session).

## Before you enable live mode

1. Read `docs/risk-register.md`. Several items there are still open.
2. Run with `risk.dry_run = true` for at least one full session and read the log.
3. Start with the smallest stake the platform allows.
4. Set a hard stop you can afford, and verify the `PAUSE` file is written when it triggers.
5. Never run it on a machine you do not control, and never expose the dashboard to the internet
   without an additional authentication layer.

## No support guarantee

This is a personal project published for study. Issues may go unanswered.
