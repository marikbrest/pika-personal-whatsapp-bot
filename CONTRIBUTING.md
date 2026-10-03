# Contributing

Thanks for helping make Pika better.

1. Fork, then `pip install -r requirements-dev.txt`.
2. Run the suite: `pytest -q` (no network or real credentials needed - external APIs are mocked).
   Also run `ruff check .` and `mypy` (CI runs both; `mypy` checks the modules listed in `pyproject.toml`, which must stay clean -
   to type another module, fix its errors and add it to `files`).
3. Add a test with every behaviour change; bug fixes should include a regression test.
4. Keep comments for the *why* (hidden constraints, past incidents), not the *what*.
5. Never include real phone numbers, names, tokens or emails in code, tests or fixtures -
   use `972500000001`-style fakes and `example.com`.

Look at the [roadmap](./ROADMAP.md) and issues labelled `good first issue` / `help wanted`. Open an issue before large changes. By contributing you agree your work is released under
the MIT license.

## Coverage

`pytest --cov` reports line+branch coverage of `src/`; CI prints the table on each run's summary page. Baseline when
this was added (2026-10-03): **74%**. There is no threshold gate yet - just don't let a change lower it noticeably, and cover
what you add.

Using Claude Code (or another coding agent) on this repo? [CLAUDE.md](./CLAUDE.md) tells it how to set the project up and how to change it.
