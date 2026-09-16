# ai-agent-trading

Bot trading otomatis untuk **MetaTrader 5** dengan arsitektur tiga lapis:

- **Execution plane** — Expert Advisor MQL5 (`ea/QlipTrendBreakout_XAUUSD.mq5`).
- **Decision plane** — Adapter FastAPI Python (`adapter/`). Phase 1: rule-based dummy. Phase 2: Claude Opus 4.7.
- **Research/replay plane** — menyusul di Phase 3.

Dokumen rancangan asli ada di [`knowledge/`](knowledge/).

## Status

**Phase 1** — `QlipTrendBreakout_XAUUSD.mq5` + `/v1/decision` (dummy, single order, M15).
**Phase 2** — `QlipBulkLayer_XAUUSD.mq5` + `/v2/plan` (M1, 3 scenario, 3–5 pending layers, basket TP).
**Phase 3 (V3 Aggressive)** — `QlipV3_XAUUSD.mq5` + `/v3/plan` (M1, 5 scenario incl. AGGRESSIVE_BIAS, mixed market+limit+stop ladder, 2 parallel baskets, DXY/VIX bias).
**Phase 4 (V4 Liquidity Zone)** — `QlipV4_XAUUSD.mq5` + `/v4/plan` (M1, single quality scenario, 2 in-zone limits, partial TP @+30pip → SL ke BE, runner cap +100pip).
**Phase 5 (V5 Scalping Burst)** — `QlipV5_XAUUSD.mq5` + `/v5/burst` (tick-driven, 3 market orders per burst, up to 3 averaging bursts per basket, basket TP $5/0.05%, basket SL $30/0.30%, max 2-min lifetime).

Semua EA bisa attach paralel. Magic terpisah:
- V1: 250518 · V2: 250519..250524 · V3: 250530..250544 · V4: 250551..250552 · V5: 250560..250569

**Performance metrics** — setiap basket yang close di-POST ke `/v1/events/basket-result` dan diagregasi jadi profit factor, win rate, expected value, max floating drawdown, p95 latency, dan rata-rata slippage/spread. Lihat card "Strategy performance" di `/dashboard`, atau `GET /api/dashboard/metrics?version=v5`. Dashboard menandai sample < 1000 basket sebagai *"too early to trust"*.

Out-of-scope: Claude integration, news fetcher real, TradingView scraper, replay tape backtest, OpenClaw control plane.

## Quickstart

- Setup awal MT5 + adapter: [`docs/setup-mt5-exness.md`](docs/setup-mt5-exness.md)
- Phase 2 bulk layering EA: [`docs/bulk-layering-quickstart.md`](docs/bulk-layering-quickstart.md)
- Phase 3 V3 aggressive EA: [`docs/v3-aggressive-quickstart.md`](docs/v3-aggressive-quickstart.md)
- Phase 4 V4 liquidity zone EA: [`docs/v4-liquidity-zone-quickstart.md`](docs/v4-liquidity-zone-quickstart.md)
- Phase 5 V5 scalping burst EA: [`docs/v5-scalping-quickstart.md`](docs/v5-scalping-quickstart.md)
- Design specs:
  - Phase 2: [`docs/superpowers/specs/2026-05-18-bulk-layering-design.md`](docs/superpowers/specs/2026-05-18-bulk-layering-design.md)
  - Phase 3: [`docs/superpowers/specs/2026-05-18-v3-aggressive-design.md`](docs/superpowers/specs/2026-05-18-v3-aggressive-design.md)

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
