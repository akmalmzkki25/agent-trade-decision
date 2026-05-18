# ai-agent-trading

Bot trading otomatis untuk **MetaTrader 5** dengan arsitektur tiga lapis:

- **Execution plane** — Expert Advisor MQL5 (`ea/QlipTrendBreakout_XAUUSD.mq5`).
- **Decision plane** — Adapter FastAPI Python (`adapter/`). Phase 1: rule-based dummy. Phase 2: Claude Opus 4.7.
- **Research/replay plane** — menyusul di Phase 3.

Dokumen rancangan asli ada di [`knowledge/`](knowledge/).

## Status

**Phase 1 MVP** — `QlipTrendBreakout_XAUUSD.mq5` + endpoint `/v1/decision` (dummy decider, single order).
**Phase 2** — `QlipBulkLayer_XAUUSD.mq5` + endpoint `/v2/plan` (3 scenario TA → 3–5 layer pending orders, basket TP, invalidation).

Adapter melayani kedua endpoint dari proses yang sama. Phase 1 dan Phase 2 EA bisa attach bersamaan di simbol sama (magic terpisah: 250518 vs 250519+).

Out-of-scope (Phase 3+): integrasi Claude, news fetcher real, TradingView scraper, replay tape backtest, OpenClaw control plane.

## Quickstart

- Setup awal MT5 + adapter: [`docs/setup-mt5-exness.md`](docs/setup-mt5-exness.md)
- Phase 2 bulk layering EA: [`docs/bulk-layering-quickstart.md`](docs/bulk-layering-quickstart.md)
- Design spec Phase 2: [`docs/superpowers/specs/2026-05-18-bulk-layering-design.md`](docs/superpowers/specs/2026-05-18-bulk-layering-design.md)

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

| Phase | Konten | Status |
|---|---|---|
| 1 | Dummy decider, XAUUSD, baseline EA end-to-end demo | ✅ done |
| 2 | Bulk layering EA (M1+M5+M15+H1), 3 scenario TA, basket TP, news stub | ✅ done |
| 3 | News fetcher real (Investing.com/FXStreet); LLM reasoning di `/v2/plan` (Claude verifier) | next |
| 4 | Replay tape harness untuk backtest di Strategy Tester | later |
| 5 | TradingView ideas scraper, calibration confidence, walk-forward | later |
