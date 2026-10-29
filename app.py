"""Stock Market Guesser: everyone's guesses vs a panel of ML models on the next S&P 500 close."""
import json
import random
import secrets
import threading
import time
import warnings

import pandas as pd
from flask import Flask, g, jsonify, request

import game
import market
import ml

warnings.filterwarnings("ignore", module="yfinance")

app = Flask(__name__, static_folder="static", static_url_path="")
REFRESH_SECONDS = 600
PRACTICE_HISTORY = 60
COOKIE = "player"
_lock = threading.Lock()
_cache = {"fetched": 0.0, "df": None, "live": None, "backtest": None}
_backtester = ml.Backtester()
_backtest_lock = threading.Lock()


def current():
    """Price history (refreshed every 10 min) and the live models (retrained on each new close)."""
    with _lock:
        if time.time() - _cache["fetched"] > REFRESH_SECONDS:
            df = market.load_history(market.now_et())
            if _cache["live"] is None or _cache["live"].as_of != df.index[-1]:
                print(f"Training models on closes through {df.index[-1].date()}...", flush=True)
                _cache["live"] = ml.train_live(df)
                threading.Thread(target=refresh_backtest, args=(df,), daemon=True).start()
            _cache.update(df=df, fetched=time.time())
            game.resolve(df)
        return _cache["df"], _cache["live"]


def refresh_backtest(df):
    """Runs in the background. The first run retrains month by month through two years (a minute or two);
    later runs only redo the current month."""
    with _backtest_lock:
        bt = _backtester.run(df)
        if _cache["backtest"] is None or bt.as_of >= _cache["backtest"].as_of:
            _cache["backtest"] = bt
    print(f"Backtest ready through {bt.as_of.date()}", flush=True)


WARMING_UP = ("Practice is warming up: the models are being retrained month by month through the last two years. "
              "This takes two or three minutes after the server starts.")


# Download and train in the background at startup; early requests wait on the lock.
threading.Thread(target=current, daemon=True).start()


@app.before_request
def identify():
    """Each browser gets an anonymous id so it can guess once per round."""
    g.player = request.cookies.get(COOKIE) or secrets.token_urlsafe(16)


@app.after_request
def remember(response):
    if request.cookies.get(COOKIE) != g.player:
        response.set_cookie(COOKIE, g.player, max_age=2 * 365 * 86400, httponly=True, samesite="Lax")
    return response


def day(ts) -> str:
    return ts.strftime("%Y-%m-%d")


def series(closes) -> list:
    return [{"date": day(d), "close": round(float(c), 2)} for d, c in closes.items()]


def parse_guess(reference: float):
    """The posted guess as a float, or an error message."""
    try:
        value = float((request.get_json(silent=True) or {})["guess"])
    except (KeyError, TypeError, ValueError):
        return None, "Enter a price."
    if not 0.8 * reference < value < 1.2 * reference:
        return None, "That's more than 20% away from the last close. Try something realistic."
    return value, None


@app.get("/")
def index():
    return app.send_static_file("index.html")


@app.get("/api/state")
def state():
    df, live = current()
    base = df.index[-1]
    target = market.next_session(base)
    lock = market.lock_time(target)
    is_open = market.now_et() < lock
    game.open_round(base, target, live.last_close, live.forecasts)
    rnd = game.get_round(base)
    guess = game.player_guess(base, g.player)
    count, median = game.crowd(base)
    # Forecasts and the crowd's pick stay hidden until you commit, so they can't anchor your guess.
    reveal = guess is not None or not is_open
    bt = _cache["backtest"]
    return jsonify({
        "as_of": day(base),
        "last_close": live.last_close,
        "prev_close": float(df["Close"].iloc[-2]),
        "target_date": day(target),
        "lock_at": lock.isoformat(),
        "open": is_open,
        "guess": guess,
        "crowd": {"count": count, "median": median if reveal else None},
        "predictions": json.loads(rnd["predictions"]) if reveal else None,
        "history": series(df["Close"].iloc[-120:]),
        "rounds": game.history(g.player),
        "accuracy": game.accuracy(g.player),
        "scoreboard": game.scoreboard(g.player, game.quarter(market.now_et().date())),
        "models": [{"model": m, "about": about} for m, about in ml.ABOUT.items()],
        "backtest": None if bt is None else {
            "days": len(bt.table), "start": day(bt.table.index[0]), "end": day(bt.table.index[-1]),
            "train_years": ml.TRAIN_YEARS, "models": bt.metrics},
    })


@app.post("/api/guess")
def guess():
    df, live = current()
    base = df.index[-1]
    target = market.next_session(base)
    if market.now_et() >= market.lock_time(target):
        return jsonify(error="Guessing is closed: that session has already opened."), 409
    value, error = parse_guess(live.last_close)
    if error:
        return jsonify(error=error), 400
    game.open_round(base, target, live.last_close, live.forecasts)
    if not game.add_guess(base, g.player, value):
        return jsonify(error="You already locked in a guess for this session."), 409
    return state()


@app.get("/api/practice")
def practice_question():
    """A random day from the last two years, forecast by models trained only on data from before its month.
    Its date stays hidden until you answer."""
    df, _ = current()
    bt = _cache["backtest"]
    if bt is None:
        return jsonify(error=WARMING_UP), 503
    when = random.choice(bt.table.index)
    return jsonify({"q": day(when), "base_close": float(bt.table.at[when, "base"]),
                    "history": [p["close"] for p in series(df["Close"].loc[:when].iloc[-PRACTICE_HISTORY:])]})


@app.post("/api/practice")
def practice_answer():
    df, _ = current()
    bt = _cache["backtest"]
    if bt is None:
        return jsonify(error=WARMING_UP), 503
    try:
        when = pd.Timestamp((request.get_json(silent=True) or {})["q"])
        row = bt.table.loc[when]
    except (KeyError, TypeError, ValueError):
        return jsonify(error="Unknown practice question."), 400
    value, error = parse_guess(float(row["base"]))
    if error:
        return jsonify(error=error), 400
    forecasts = {m: float(row[m]) for m in row.index if m not in ("base", "actual", "target")}
    result = game.score_round({game.YOU: value, **forecasts}, float(row["base"]), float(row["actual"]))
    return jsonify({
        "result": {"base_date": day(when), "target_date": day(row["target"]), "base": float(row["base"]),
                   "actual": float(row["actual"]), **result},
        "history": series(df["Close"].loc[:row["target"]].iloc[-PRACTICE_HISTORY - 1:]),
    })


if __name__ == "__main__":
    print("Open http://127.0.0.1:5000 (models are training; the first load may take a few seconds)", flush=True)
    app.run(port=5000, threaded=True)
