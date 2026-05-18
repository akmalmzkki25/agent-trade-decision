# ai-agent-trading

Bot trading otomatis untuk **MetaTrader 5** dengan arsitektur tiga lapis:

- **Execution plane** — Expert Advisor MQL5 (`ea/QlipTrendBreakout_XAUUSD.mq5`).
- **Decision plane** — Adapter FastAPI Python (`adapter/`). Phase 1: rule-based dummy. Phase 2: Claude Opus 4.7.
- **Research/replay plane** — menyusul di Phase 3.

Dokumen rancangan asli ada di [`knowledge/`](knowledge/).

## Status

**Phase 1 MVP** — dummy trend-breakout decider, XAUUSD (H1 context + M15 decision), Exness demo.

In-scope sekarang: jalur end-to-end EA → Adapter → Decision JSON → OrderCheck/OrderSend → OnTradeTransaction → SQLite ledger.

Out-of-scope (sampai Phase 2+): Claude integration, mean-reversion stack, replay tape backtest, OpenClaw control plane.

## Quickstart

Lihat panduan lengkap di [`docs/setup-mt5-exness.md`](docs/setup-mt5-exness.md).

Singkat:

```powershell
cd adapter
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
copy .env.example .env
uvicorn app.main:app --host 127.0.0.1 --port 8765
```

Compile `ea/QlipTrendBreakout_XAUUSD.mq5` di MetaEditor → drag ke chart XAUUSD M15. Allowlist `http://127.0.0.1:8765` di Tools → Options → Expert Advisors.

## Tests

```powershell
cd adapter
pip install -e ".[dev]"
pytest
```

## Roadmap

| Phase | Konten |
|---|---|
| 1 (sekarang) | Dummy decider, XAUUSD, baseline EA end-to-end demo |
| 2 | `deciders/claude.py` (Claude Opus 4.7, structured outputs, prompt caching) |
| 3 | Replay tape harness (Python `MetaTrader5` lib → batch decisions → tester replay) |
| 4 | Strategi mean-reversion + swing continuation, walk-forward, calibration confidence |
