# Contributing

This is primarily a portfolio project, but issues and pull requests that improve correctness are
welcome.

## Ground rules

- **No strategy tuning in a bug-fix PR.** Engineering fixes and risk-parameter changes must not
  share a measurement window.
- **Tests first for anything touching settlement or order state.** Those are the paths that cost
  real money.
- **Never commit secrets.** No `.env`, no `config.json`, no wallet material, no webhook URLs.
  The CI runs a pattern check on every push.
- **Do not add a `print` of an API response that may contain a token.**

## Local checks

```bash
python -m compileall -q src scripts
python -m unittest discover -s tests -v
python scripts/doctor.py        # requires a filled config.json
```

## Commit style

`type(scope): summary` — `fix`, `feat`, `docs`, `test`, `refactor`, `chore`.
