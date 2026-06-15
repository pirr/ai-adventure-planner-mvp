"""Shared pytest setup.

The HTTP rate limiter is an abuse backstop for production; left on it would
make endpoint tests flaky (a handful of POSTs to the same in-process limiter
would trip the per-minute window). Disable it for the whole suite *before*
app.config is imported. test_rate_limit.py exercises the limiter in isolation
with its own enabled instance.
"""

import os

os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
