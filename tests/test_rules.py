#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pure-logic tests. No network, no orders, no wallet.

    python -m unittest discover -s tests -v
"""
import json, os, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
TMP = Path(tempfile.mkdtemp(prefix="btc5m-test-"))
(TMP / "state").mkdir()
(TMP / "logs").mkdir()
(TMP / "data").mkdir()
(TMP / "config.json").write_text(json.dumps({
    "home": str(TMP),
    "runtime": {"baw_js": str(TMP / "wallet.js")},
    "paths": {"state": "state", "logs": "logs", "data": "data"},
    "accounts": [{"id": "A", "name": "test", "asset": "BTC", "base_stake": 1.0,
                  "wallet_profile": str(TMP / ".baw")}],
    "strategy": {"min_streak": 3, "price": 0.51, "carry_price": 0.51},
    "sizing": {"mode": "kelly", "p": 0.52, "frac": 0.25, "max_pct": 0.05,
               "min_edge": 0.0, "min_stake": 1.0},
    "risk": {"dry_run": True},
}), encoding="utf-8")
os.environ["BTC5M_CONFIG"] = str(TMP / "config.json")
sys.path.insert(0, str(SRC))

import predict_live_bot_kelly as bot  # noqa: E402


def bar(o, c):
    return {"ot": 0, "o": o, "c": c, "ct": 0}


class TestResolveOutcome(unittest.TestCase):
    def test_explicit_win_beats_negative_pnl(self):
        self.assertTrue(bot.resolve_outcome("WIN", None, -5.0, False))

    def test_explicit_loss_beats_positive_pnl(self):
        self.assertFalse(bot.resolve_outcome("LOSS", True, 5.0, True))

    def test_is_winner_used_when_result_empty(self):
        self.assertTrue(bot.resolve_outcome("", True, -1.0, False))
        self.assertFalse(bot.resolve_outcome(None, False, 1.0, True))

    def test_pnl_sign_when_no_result(self):
        self.assertTrue(bot.resolve_outcome("", None, 0.02, False))
        self.assertFalse(bot.resolve_outcome("", None, -0.02, True))

    def test_zero_pnl_falls_back_to_can_claim(self):
        self.assertTrue(bot.resolve_outcome("", None, 0.0, True))
        self.assertFalse(bot.resolve_outcome("", None, 0.0, False))

    def test_never_defaults_to_loss_on_ambiguous_payload(self):
        # canClaim True must be enough to avoid a false LOSS
        self.assertTrue(bot.resolve_outcome("", None, 0.0, True))


class TestStreak(unittest.TestCase):
    def test_three_up_bets_down(self):
        bars = [bar(100, 101), bar(101, 102), bar(102, 103)]
        self.assertEqual(bot.streak_run(bars), ("DOWN", 3))

    def test_three_down_bets_up(self):
        bars = [bar(103, 102), bar(102, 101), bar(101, 100)]
        self.assertEqual(bot.streak_run(bars), ("UP", 3))

    def test_doji_breaks_the_run(self):
        bars = [bar(100, 101), bar(101, 101), bar(101, 102)]
        self.assertEqual(bot.streak_run(bars), (None, 0))

    def test_short_run_is_not_a_signal(self):
        bars = [bar(100, 101), bar(101, 100), bar(100, 101)]
        self.assertEqual(bot.streak_run(bars), (None, 0))


class TestSizing(unittest.TestCase):
    def setUp(self):
        self._cfg = dict(bot.CFG)
        bot.KELLY_FRACS.clear()

    def tearDown(self):
        bot.CFG.clear()
        bot.CFG.update(self._cfg)

    def test_respects_balance_cap(self):
        st = {"kelly_p": 0.60, "limit": 0.50, "bankroll": 100.0, "level": 2}
        stake = bot.next_stake(st)
        self.assertLessEqual(stake, 100.0 * bot.CFG["kelly_max_pct"] + 1e-9)

    def test_never_below_min_stake(self):
        st = {"kelly_p": 0.52, "limit": 0.51, "bankroll": 5.0, "level": 2}
        self.assertGreaterEqual(bot.next_stake(st), bot.CFG["kelly_min_stake"])

    def test_no_edge_yields_floor(self):
        st = {"kelly_p": 0.40, "limit": 0.51, "bankroll": 1000.0, "level": 2}
        self.assertEqual(bot.next_stake(st), bot.CFG["kelly_min_stake"])


if __name__ == "__main__":
    unittest.main()
