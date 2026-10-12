"""Module 1 tests. Replace the smoke test below with your real suite.

Required by Q1.3:
- a CSV with dirty rows (accepted/rejected counts + per-row reasons)
- the happy-path lifecycle: upload -> stage 1 review -> stage 2 review
- at least three invalid transitions -> 409
"""


def test_app_imports():
    from app.main import app  # noqa: F401
