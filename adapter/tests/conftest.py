import os
import tempfile

# Isolate DB and disable HMAC for tests BEFORE app imports.
_tmp = tempfile.mkdtemp(prefix="adapter-test-")
os.environ["DB_PATH"] = os.path.join(_tmp, "test_ledger.db")
os.environ["REPLAY_DIR"] = _tmp
os.environ["HMAC_REQUIRED"] = "false"
os.environ.setdefault("DECIDER", "dummy_trend_breakout")
