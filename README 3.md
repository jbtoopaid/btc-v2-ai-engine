# BTC $250 first-touch research website

Forecasts whether BTC-USD will first touch **$250 above or below** its starting price within **15 minutes**. A forecast is issued at every quarter-hour boundary when Coinbase data is available. The forecast is scored as a **win** if the predicted barrier is touched first, a **loss** if the opposite barrier is touched first or neither is touched, and **ambiguous** if both barriers are reached inside the same 1-minute candle (order unknowable from OHLC).

## Deploy on Render

1. Upload all files to a private GitHub repository (keep `Dockerfile`, `render.yaml`, and `app.py` at the repository root).
2. In Render, choose **New > Blueprint**, connect the repository, and approve the proposed web service and 1GB persistent disk. This requires a paid instance with persistent disk support.
3. Wait for the build and open the Render URL. The first data collection, model fitting, and walk-forward backtest may take a few minutes.
4. Keep one server instance running. SQLite and the in-process scheduler are not designed for multiple replicas. No API keys are required for the public Coinbase endpoint.

## Local

```bash
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000
```

Then visit http://localhost:8000. Data and history are stored under `DATA_DIR` (defaults to `./data`).

## Model and methodology

- Public Coinbase BTC-USD 1-minute OHLCV candles, 72-hour lookback; 1-minute prices are not tick-level. Historical request endpoint is rate-limited and can have missing minutes.
- Gradient-boosted classifier using lagged returns, volatility, range, candle structure, and volume. Three outcomes: UP first, DOWN first, NEITHER. **Not a deep-learning model and not guaranteed to beat chance.**
- Historical walk-forward: expanding training sets, 15-minute embargo before each test fold, one prediction per 15 minutes. The current backtest is limited to ~72h of recent data and is not sufficient to establish long-term 70% accuracy. Historical report excludes ambiguous same-candle double touches and gaps. It is not a trade P&L simulation.
- Live predictions use the last completed 1-minute close at each quarter-hour boundary. Scoring uses subsequent 15 complete candles; forecasts are stored in SQLite. A missing market-data interval delays resolution rather than being guessed.
- Probability numbers are raw classifier outputs, **not calibrated probabilities**. Accuracy is measured on resolved live forecasts and includes no-touch losses; ambiguous results are displayed separately.
- The live predictor is trained on recent candles at each interval; there is no automatic hyperparameter search or guarantees of 70% performance.

## Known limitations

- Intraminute ordering is unknowable from 1-minute OHLC; for rigorous first-touch resolution upgrade to exchange trade/tick data and timestamped execution.
- The service checks Coinbase about once a minute; network delays can cause skipped forecasts. It does not backfill missing live forecasts, because backfilled forecasts would use future information.
- Forecast target is **$250 absolute**, not a percent; volatility regime changes may make the target more or less attainable.
- No trading integration; no profitability claims. A 70% win rate alone does not establish positive returns.
- The database is not encrypted and the dashboard has no login; do not deploy sensitive data to it.
