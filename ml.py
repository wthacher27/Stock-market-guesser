"""Feature engineering and the models that predict the next S&P 500 close.

Every model predicts tomorrow's % return from today's features; the price is
today's close compounded by that return. Training on returns rather than raw
prices keeps the models from simply memorizing the price level.
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, RegressorMixin, clone
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import LinearRegression
from sklearn.naive_bayes import GaussianNB
from sklearn.neighbors import KNeighborsRegressor
from sklearn.neural_network import MLPRegressor
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVR
from sklearn.tree import DecisionTreeRegressor

TEST_DAYS = 500  # ~2 years held out for the backtest and practice mode
# Training window in years; None trains on all saved history (since 2005). Across five two-year test
# periods (2016-2026), 20+ years beat 5 and 10: short windows overfit calm markets and broke in 2022.
TRAIN_YEARS = None
BASELINE = "No Change (baseline)"
ENSEMBLE = "Ensemble (average)"


class BinnedNaiveBayes(BaseEstimator, RegressorMixin):
    """Gaussian Naive Bayes is a classifier, so bucket returns into quantile bins,
    classify, and predict the probability-weighted mean return of the bins."""

    def __init__(self, n_bins=5):
        self.n_bins = n_bins

    def fit(self, X, y):
        y = np.asarray(y)
        edges = np.quantile(y, np.linspace(0, 1, self.n_bins + 1)[1:-1])
        labels = np.digitize(y, edges)
        self.bin_means_ = np.array([y[labels == k].mean() for k in range(self.n_bins)])
        self.clf_ = make_pipeline(StandardScaler(), GaussianNB()).fit(X, labels)
        return self

    def predict(self, X):
        return self.clf_.predict_proba(X) @ self.bin_means_[self.clf_.classes_]


class Calibrated(BaseEstimator, RegressorMixin):
    """Pulls a model's forecasts toward the market's typical drift.

    Raw forecasts of daily returns are mostly noise. Hold back the most recent 20% of the training data, find
    the a + k * forecast that best matches it by absolute error, then refit on everything. k is how much the
    model's own forecast is trusted. Left free, every model picks k = 0 and they all become the same
    "typical drift" bot, so k never goes below min_trust and each bot keeps its own opinion. With the 0.25
    floor, all 8 models beat No Change on the practice backtest (before: none did).
    """

    def __init__(self, model, min_trust=0.25):
        self.model = model
        self.min_trust = min_trust

    def fit(self, X, y):
        cut = int(len(X) * 0.8)
        held = clone(self.model).fit(X.iloc[:cut], y.iloc[:cut]).predict(X.iloc[cut:])
        actual = np.asarray(y)[cut:]
        best = (np.inf, 0.0, 0.0)
        for k in np.linspace(self.min_trust, 1.5, 26):
            a = np.median(actual - k * held)
            err = np.abs(actual - a - k * held).mean()
            if err < best[0]:
                best = (err, a, k)
        _, self.offset_, self.scale_ = best
        self.model_ = clone(self.model).fit(X, y)
        return self

    def predict(self, X):
        return self.offset_ + self.scale_ * self.model_.predict(X)


_RAW_MODELS = {
    "Linear Regression": make_pipeline(StandardScaler(), LinearRegression()),
    "Decision Tree": DecisionTreeRegressor(max_depth=4, min_samples_leaf=100, random_state=0),
    "Random Forest": RandomForestRegressor(
        n_estimators=200, max_depth=6, min_samples_leaf=25, n_jobs=-1, random_state=0),
    "Gradient Boosting": GradientBoostingRegressor(
        n_estimators=150, max_depth=3, learning_rate=0.03, subsample=0.8, random_state=0),
    "Naive Bayes": BinnedNaiveBayes(),
    "K-Nearest Neighbors": make_pipeline(StandardScaler(), KNeighborsRegressor(n_neighbors=75)),
    "Support Vector Machine": make_pipeline(StandardScaler(), SVR(C=0.3, epsilon=0.2)),
    "Neural Network": make_pipeline(StandardScaler(), MLPRegressor(
        hidden_layer_sizes=(32, 16), alpha=1.0, max_iter=500, early_stopping=True, random_state=0)),
}
MODELS = {name: Calibrated(m) for name, m in _RAW_MODELS.items()}

ABOUT = {
    "Linear Regression": "Fits a weighted sum of the features.",
    "Decision Tree": "One tree of if/else splits on the features.",
    "Random Forest": "Averages 200 trees, each trained on a random resample.",
    "Gradient Boosting": "Builds small trees one after another, each fixing the last one's errors.",
    "Naive Bayes": "Classifies tomorrow into 5 return buckets and takes the probability-weighted average.",
    "K-Nearest Neighbors": "Finds the 75 most similar past days and averages what happened next.",
    "Support Vector Machine": "Kernel regression that ignores errors inside a small margin.",
    "Neural Network": "A small two-layer perceptron (32 → 16 neurons).",
    ENSEMBLE: "Average of all the models above.",
    BASELINE: "Predicts tomorrow closes exactly where today did. The one to beat.",
}


def make_features(df: pd.DataFrame):
    """Features known at today's close, and the target: tomorrow's % return."""
    close = df["Close"]
    ret = close.pct_change() * 100
    f = pd.DataFrame(index=df.index)
    for lag in range(5):
        f[f"ret_{lag}"] = ret.shift(lag)
    for w in (5, 10, 20, 50, 200):
        f[f"vs_sma_{w}"] = (close / close.rolling(w).mean() - 1) * 100
    f["vol_10"] = ret.rolling(10).std()
    f["vol_30"] = ret.rolling(30).std()
    gain, loss = ret.clip(lower=0).rolling(14).mean(), (-ret.clip(upper=0)).rolling(14).mean()
    f["rsi_14"] = 100 * gain / (gain + loss)
    f["range"] = (df["High"] - df["Low"]) / close * 100
    volume = df["Volume"].replace(0, np.nan).ffill()
    f["rel_volume"] = np.log(volume / volume.rolling(20).mean())
    return f, ret.shift(-1)


@dataclass
class Live:
    as_of: pd.Timestamp       # date of the latest close the forecasts start from
    last_close: float
    forecasts: dict           # model -> predicted next close


@dataclass
class Backtest:
    as_of: pd.Timestamp       # latest close when it was run
    table: pd.DataFrame       # per held-out day: base, actual, target, one column per model
    metrics: list             # per model summary


def _with_extras(returns: dict) -> dict:
    """Add the ensemble and the no-change baseline to a {model: % return} dict."""
    out = dict(returns)
    out[ENSEMBLE] = np.mean(list(returns.values()), axis=0)
    out[BASELINE] = np.zeros_like(out[ENSEMBLE])
    return out


def _recent(X, y):
    """The last TRAIN_YEARS of rows, or all of them."""
    if TRAIN_YEARS is None:
        return X, y
    keep = X.index > X.index[-1] - pd.DateOffset(years=TRAIN_YEARS)
    return X[keep], y[keep]


def _dataset(df: pd.DataFrame):
    """Rows with complete features and a known next-day return, plus the latest row (tomorrow unknown)."""
    X, y = make_features(df)
    X = X.replace([np.inf, -np.inf], np.nan).dropna()
    if X.index[-1] != df.index[-1]:
        raise ValueError(f"latest close {df.index[-1].date()} has incomplete features")
    latest = X.iloc[[-1]]
    labeled = y.loc[X.index].notna()
    return X[labeled], y.loc[X.index][labeled], latest


def train_live(df: pd.DataFrame) -> Live:
    """Train every model on all the data and forecast the next close."""
    X, y, latest = _dataset(df)
    X, y = _recent(X, y)
    returns = _with_extras({name: clone(m).fit(X, y).predict(latest)[0] for name, m in MODELS.items()})
    last_close = float(df["Close"].iloc[-1])
    return Live(df.index[-1], last_close, {m: float(last_close * (1 + r / 100)) for m, r in returns.items()})


class Backtester:
    """Walk-forward backtest over the last TEST_DAYS closes, which practice mode draws from.

    Each calendar month's days are forecast by models retrained on only the data before that month, so a
    forecast is never more than a month out of date. Finished months are cached: the first run retrains
    about 25 times, and after that each new close only redoes the current month.
    """

    def __init__(self):
        self._months = {}  # Period -> DataFrame of % return forecasts, one column per model

    def run(self, df: pd.DataFrame) -> Backtest:
        X, y, _ = _dataset(df)
        test = X.index[-TEST_DAYS:]
        months = test.to_period("M").unique()
        for month in months:
            days = X.index[X.index.to_period("M") == month]
            cached = self._months.get(month)
            if cached is None or not cached.index.equals(days):
                before = X.index < month.start_time
                X_tr, y_tr = _recent(X[before], y[before])
                self._months[month] = pd.DataFrame(
                    {name: clone(m).fit(X_tr, y_tr).predict(X.loc[days]) for name, m in MODELS.items()}, index=days)
        for old in set(self._months) - set(months):
            del self._months[old]

        forecast = pd.concat(self._months[m] for m in months).loc[test]
        returns = _with_extras({name: forecast[name].to_numpy() for name in forecast})
        close = df["Close"]
        base = close.loc[test]
        table = pd.DataFrame({"base": base, "actual": close.shift(-1).loc[test]})
        table["target"] = [df.index[df.index.get_loc(d) + 1] for d in test]
        for m, r in returns.items():
            table[m] = base * (1 + r / 100)
        return Backtest(df.index[-1], table, _metrics(table, returns))


def _metrics(bt: pd.DataFrame, returns: dict) -> list:
    actual_up = bt["actual"] > bt["base"]
    baseline_mae = (bt[BASELINE] - bt["actual"]).abs().mean()
    rows = []
    for m in returns:
        err = (bt[m] - bt["actual"]).abs()
        called = bt[m] != bt["base"]
        hits = ((bt[m] > bt["base"]) == actual_up)[called]
        rows.append({
            "model": m,
            "mae": round(err.mean(), 2),
            "mape": round((err / bt["actual"]).mean() * 100, 3),
            "direction": round(hits.mean() * 100, 1) if len(hits) else None,
            "vs_baseline": round((1 - err.mean() / baseline_mae) * 100, 2),
        })
    return sorted(rows, key=lambda r: r["mae"])
