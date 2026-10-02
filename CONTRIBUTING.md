# Contributing

Thanks for helping make Pika better.

1. Fork, then `pip install -r requirements-dev.txt`.
2. Run the suite: `pytest -q` (no network or real credentials needed - external APIs are mocked).
3. Add a test with every behaviour change; bug fixes should include a regression test.
4. Keep comments for the *why* (hidden constraints, past incidents), not the *what*.
5. Never include real phone numbers, names, tokens or emails in code, tests or fixtures -
   use `972500000001`-style fakes and `example.com`.

Look at the [roadmap](./ROADMAP.md) and issues labelled `good first issue` / `help wanted`. Open an issue before large changes. By contributing you agree your work is released under
the MIT license.
