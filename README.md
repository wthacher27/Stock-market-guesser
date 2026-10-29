# Stock Market Guesser

Guess where the S&P 500 will close on the next trading day, then see how the players' guesses stack up
against ten machine-learning models: Linear Regression, Decision Tree, Random Forest, Gradient Boosting,
Naive Bayes, K-Nearest Neighbors, Support Vector Machine, a Neural Network, an Ensemble average, and a
"No Change" baseline.

## Run it locally

```
pip install -r requirements.txt
python app.py
```

Then open http://127.0.0.1:5000. The models train when the server starts, which takes about 10 seconds.

## How to play

- **Live game.** After each day's close, guess the next trading day's close. Each browser gets one guess,
  and guessing locks when the market opens at 9:30 AM ET. Every guess is saved. **Humanity**'s guess is the
  median of all of them, so a few deliberately terrible guesses can't move it. Your own guess (**You**)
  competes as a separate entrant. The forecasts and the median stay hidden until you lock in. After the
  close, the Live tab shows how Humanity and You did against the bots.
- **Scoreboard.** Each trading day the five closest entrants score 10, 7, 5, 3 and 1 points (ties share the
  better place), and points reset every calendar quarter. There are two leagues:
  - **Humans vs bots:** days at least one person guessed, with You and Humanity competing against the bots.
  - **Bot league:** every trading day, bots only, so the models keep scoring even when nobody plays.

  A head-to-head table shows Humanity's and your win-loss record against each bot.
- **Practice.** Get a random "mystery day" from the last two years, with 60 days of chart and no date.
  Guess the next close and the answer is revealed right away. Each day's forecasts come from models retrained
  at the start of that month on only the data before it, so they're never more than a month out of date.
  That retraining runs in the background when the server starts, so practice takes two or three minutes to warm up.
- **The models.** A 500-day backtest showing how each model would have done, compared with just guessing
  yesterday's close.

## Host it online (Render)

1. Push this repo to GitHub.
2. On render.com, create a **New → Web Service** from the repo.
   - Build command: `pip install -r requirements.txt`
   - Start command: `gunicorn app:app --workers 1 --threads 4 --timeout 300`
3. To keep guesses across restarts and redeploys, add a **Disk** (paid plans), mounted at e.g. `/var/data`,
   and set the environment variable `DATA_DIR=/var/data`. Without a disk the site still works, but saved
   guesses are wiped whenever the server restarts.

Keep `--workers 1`, since each worker would train its own copy of the models.

## Price data

`sp500.csv` is a saved copy of daily S&P 500 prices since 2005. On each refresh, the server only asks Yahoo
Finance for the last week and appends anything new to `DATA_DIR/sp500.csv`. If Yahoo is unreachable, the app
keeps running on the saved prices, and rounds are scored once Yahoo answers again.

## Files

| File | What it does |
|---|---|
| `market.py` | Loads prices and works out NYSE trading days and lock times. |
| `ml.py` | Builds features (recent returns, moving-average gaps, volatility, RSI, range, volume) and trains every model on all saved history (`TRAIN_YEARS` can limit it) to predict tomorrow's % return, then calibrates each one by pulling noisy forecasts toward the typical daily drift. |
| `game.py` | Stores rounds and guesses in SQLite (`DATA_DIR/game.db`) and scores them. |
| `app.py` | Flask server. Refreshes prices every 10 minutes and retrains when a new close arrives. |
| `static/` | The single-page front end (Chart.js for the price chart). |

Naive Bayes is a classifier, so it sorts days into five return buckets. Its forecast is each bucket's
average return, weighted by how likely the model thinks that bucket is.
