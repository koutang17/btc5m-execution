# Legacy

`martingale_bot.py` is the previous strategy: after N consecutive candles it bets the
reversal and doubles the stake on every loss (1 / 2 / 4 / 8 / 16 ...), capped by
`strategy.max_losses`.

It is kept **for historical comparison only**. A doubling ladder has an unbounded tail risk and
was the reason the live system moved to fractional Kelly. It is not started by default
(`account_modes.json` defaults to `none`), and `botctl.py` needs an explicit
`switch martingale` to run it.

Do not enable it on real funds.
