"""Historical BTC forecasting comparison; no betting or trading execution."""
from datetime import datetime, timezone
import numpy as np
import pandas as pd
import requests
from fastapi import FastAPI, HTTPException
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, brier_score_loss
from sklearn.preprocessing import StandardScaler
from lightgbm import LGBMClassifier
from xgboost import XGBClassifier

app = FastAPI(title="BTC V2 AI Research")


def load_candles():
    endpoint = "https://api.exchange.coinbase.com/products/BTC-USD/candles"
    end = int(datetime.now(timezone.utc).timestamp()) // 900 * 900
    rows = {}
    with requests.Session() as session:
        session.headers["User-Agent"] = "btc-v2-research/1.0"
        for k in range(10):
            stop = end - k * 299 * 900
            start = stop - 299 * 900
            params = {"granularity": 900, "start": datetime.fromtimestamp(start, timezone.utc).isoformat(), "end": datetime.fromtimestamp(stop, timezone.utc).isoformat()}
            response = session.get(endpoint, params=params, timeout=20)
            response.raise_for_status()
            for item in response.json():
                if isinstance(item, list) and len(item) >= 6:
                    rows[int(item[0])] = item[:6]
    if not rows:
        raise ValueError("No historical candles returned")
    df = pd.DataFrame(rows.values(), columns=["time", "low", "high", "open", "close", "volume"])
    return df.sort_values("time").drop_duplicates("time").reset_index(drop=True)


def prepare(df):
    close = df["close"]
    log = np.log(close)
    features = pd.DataFrame(index=df.index)
    for lag in (1, 2, 4, 8, 16):
        features[f"return_{lag}"] = log.diff(lag)
    features["range"] = (df["high"] - df["low"]) / close
    features["body"] = (close - df["open"]) / close
    features["relative_volume"] = df["volume"] / df["volume"].rolling(16).mean()
    features["volatility"] = log.diff().rolling(16).std()
    feature_names = features.columns.tolist()
    features["label"] = (close.shift(-1) > close).astype(int)
    features["next_gap"] = df["time"].shift(-1) - df["time"]
    features["lookback_ok"] = df["time"].diff().eq(900).rolling(16).sum().eq(16)
    features = features.replace([np.inf, -np.inf], np.nan).dropna()
    features = features[(features["next_gap"] == 900) & features["lookback_ok"]]
    return features[feature_names].to_numpy(), features["label"].to_numpy(), feature_names


def evaluate_history():
    df = load_candles()
    x, y, names = prepare(df)
    if len(y) < 600:
        raise HTTPException(503, "Insufficient contiguous historical data")
    output = []
    for fold, (train_frac, end_frac) in enumerate(((0.5, 0.625), (0.625, 0.75), (0.75, 0.875)), 1):
        train_end, test_end = int(len(y) * train_frac), int(len(y) * end_frac)
        # Purge the boundary label, which observes the first test candle.
        x_train, y_train = x[:train_end-1], y[:train_end-1]
        x_test, y_test = x[train_end:test_end], y[train_end:test_end]
        scaler = StandardScaler().fit(x_train)
        a, b = scaler.transform(x_train), scaler.transform(x_test)
        models = {
            "logistic_regression": LogisticRegression(max_iter=400),
            "lightgbm": LGBMClassifier(n_estimators=100, max_depth=4, num_leaves=15, learning_rate=0.04, verbosity=-1, n_jobs=2),
            "xgboost": XGBClassifier(n_estimators=100, max_depth=4, learning_rate=0.04, eval_metric="logloss", n_jobs=2),
        }
        probabilities = {}
        for name, model in models.items():
            model.fit(a, y_train)
            probabilities[name] = model.predict_proba(b)[:, 1]
        probabilities["equal_weight_ensemble"] = np.mean(list(probabilities.values()), axis=0)
        probabilities["momentum_baseline"] = (x_test[:, names.index("return_4")] >= 0).astype(float)
        for name, p in probabilities.items():
            output.append({"fold": fold, "model": name, "training_samples": len(y_train), "test_samples": len(y_test), "accuracy": round(float(accuracy_score(y_test, p >= 0.5)), 4), "brier_score": round(float(brier_score_loss(y_test, p)), 4)})
    return {"purpose": "Historical Bitcoin research only", "candles": len(df), "examples": len(y), "walk_forward": output, "disclaimer": "Historical Coinbase spot returns are not Kalshi settlement outcomes; no betting execution."}


@app.get("/")
def home():
    return {"name": "BTC V2 AI Research", "models": ["LightGBM", "XGBoost", "Logistic regression", "Ensemble"], "results": "/api/evaluate"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/evaluate")
def evaluate():
    try:
        return evaluate_history()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, f"Historical evaluation unavailable: {type(exc).__name__}") from exc
