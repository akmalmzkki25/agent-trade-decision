"""
Helpers of the V6 operator scripts (standard library only).

`scripts/v6_operator.py` and `scripts/v6_sync_ea_key.py` put the `scripts`
directory on `sys.path` and import this package from there. Nothing here imports
the adapter application: the scripts must run with a bare interpreter
(`python -S -I`), so the contracts they rely on are restated in named
constants and pinned by the tests in `tests/v6/test_v6_operator_*.py`.
"""
