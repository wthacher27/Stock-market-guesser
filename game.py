"""Daily rounds, everyone's guesses, scoring and the quarterly leagues, stored in SQLite."""
import json
import sqlite3
import statistics
from datetime import datetime

import pandas as pd

from market import DATA_DIR

DB = DATA_DIR / "game.db"
HUMANITY = "Humanity"  # the median of every guess submitted for a round
YOU = "You"            # the visitor looking at the page (their own guess)
POINTS = {1: 10, 2: 7, 3: 5, 4: 3, 5: 1}  # per place, each trading day

SCHEMA = """
CREATE TABLE IF NOT EXISTS rounds (
    base_date   TEXT PRIMARY KEY,   -- the last known close the guesses start from
    target_date TEXT NOT NULL,      -- the close being predicted
    base_close  REAL NOT NULL,
    predictions TEXT NOT NULL,      -- JSON {model: predicted close}, frozen when the round opens
    actual      REAL
);
CREATE TABLE IF NOT EXISTS guesses (
    base_date  TEXT NOT NULL REFERENCES rounds(base_date),
    player     TEXT NOT NULL,       -- anonymous browser id from a cookie
    guess      REAL NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (base_date, player)
);
"""


def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _day(ts) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


def quarter(day) -> str:
    ts = pd.Timestamp(day)
    return f"{ts.year} Q{ts.quarter}"


def open_round(base_date, target_date, base_close, forecasts):
    """Create the round, freezing the model forecasts. No-op if it already exists."""
    with connect() as conn:
        conn.execute("INSERT OR IGNORE INTO rounds (base_date, target_date, base_close, predictions)"
                     " VALUES (?, ?, ?, ?)",
                     (_day(base_date), _day(target_date), base_close, json.dumps(forecasts)))


def get_round(base_date):
    with connect() as conn:
        return conn.execute("SELECT * FROM rounds WHERE base_date = ?", (_day(base_date),)).fetchone()


def add_guess(base_date, player, guess) -> bool:
    """Save a visitor's guess. False if they already guessed this round."""
    with connect() as conn:
        cur = conn.execute("INSERT OR IGNORE INTO guesses (base_date, player, guess, created_at) VALUES (?, ?, ?, ?)",
                           (_day(base_date), player, guess, datetime.now().isoformat(timespec="seconds")))
        return cur.rowcount == 1


def player_guess(base_date, player):
    with connect() as conn:
        row = conn.execute("SELECT guess FROM guesses WHERE base_date = ? AND player = ?",
                           (_day(base_date), player)).fetchone()
    return row["guess"] if row else None


def crowd(base_date):
    """(number of guesses, their median) for a round."""
    with connect() as conn:
        guesses = [r["guess"] for r in conn.execute("SELECT guess FROM guesses WHERE base_date = ?",
                                                    (_day(base_date),))]
    return len(guesses), statistics.median(guesses) if guesses else None


def resolve(df: pd.DataFrame):
    """Fill in the actual close for any round whose target session has finished."""
    with connect() as conn:
        pending = conn.execute("SELECT base_date FROM rounds WHERE actual IS NULL").fetchall()
        for r in pending:
            later = df[df.index > r["base_date"]]
            if len(later):
                conn.execute("UPDATE rounds SET actual = ?, target_date = ? WHERE base_date = ?",
                             (float(later["Close"].iloc[0]), _day(later.index[0]), r["base_date"]))


def score(pred, base, actual) -> dict:
    """100 points minus 50 per 1% of error (floor 0), plus 20 for calling the direction. Used in practice."""
    err = abs(pred - actual)
    pct = err / actual * 100
    direction = None if pred == base else (pred > base) == (actual > base)
    return {"pred": pred, "err": err, "pct": pct, "direction": direction,
            "points": max(0, round(100 - 50 * pct)) + (20 if direction else 0)}


def score_round(players: dict, base, actual) -> dict:
    """Score {player: predicted close} against the actual close."""
    scores = {p: score(v, base, actual) for p, v in players.items()}
    best = min(s["err"] for s in scores.values())
    bots = [p for p in scores if p not in (HUMANITY, YOU)]
    return {"scores": scores,
            "winners": [p for p, s in scores.items() if s["err"] == best],
            "best_model": min(bots, key=lambda p: scores[p]["err"])}


def placings(players: dict, actual) -> dict:
    """Place of each player by miss, rounded to the cent. Ties share the better place (1, 2, 2, 4...)."""
    miss = {p: round(abs(v - actual), 2) for p, v in players.items()}
    return {p: 1 + sum(m < miss[p] for m in miss.values()) for p in miss}


def _finished(player=None):
    """Every scored round, newest first, as (round, bots, humans, number of guesses).
    humans holds Humanity (the median guess) if anyone guessed, and You if `player` guessed."""
    with connect() as conn:
        rounds = conn.execute("SELECT * FROM rounds WHERE actual IS NOT NULL ORDER BY base_date DESC").fetchall()
        guesses, mine = {}, {}
        for g in conn.execute("SELECT base_date, player, guess FROM guesses"):
            guesses.setdefault(g["base_date"], []).append(g["guess"])
            if g["player"] == player:
                mine[g["base_date"]] = g["guess"]
    for r in rounds:
        bots = json.loads(r["predictions"])
        day = guesses.get(r["base_date"], [])
        humans = {HUMANITY: statistics.median(day)} if day else {}
        if r["base_date"] in mine:
            humans[YOU] = mine[r["base_date"]]
        yield r, bots, humans, len(day)


def history(player, limit=30) -> list:
    """Finished rounds, newest first. Places are from the humans-vs-bots league when anyone guessed,
    otherwise from the bot league."""
    out = []
    for r, bots, humans, count in _finished(player):
        players = {**bots, **humans}
        places = placings(players, r["actual"])
        out.append({"base_date": r["base_date"], "target_date": r["target_date"], "base": r["base_close"],
                    "actual": r["actual"], "guesses": count, "league": "main" if humans else "bots",
                    "places": places, "points": {p: POINTS.get(n, 0) for p, n in places.items()},
                    **score_round(players, r["base_close"], r["actual"])})
        if len(out) == limit:
            break
    return out


def accuracy(player) -> list:
    """Each entrant's average accuracy over every finished round, smallest average miss first."""
    totals = {}
    for r, bots, humans, _ in _finished(player):
        for p, v in {**bots, **humans}.items():
            s = score(v, r["base_close"], r["actual"])
            t = totals.setdefault(p, {"rounds": 0, "err": 0.0, "pct": 0.0, "calls": 0, "hits": 0})
            t["rounds"] += 1
            t["err"] += s["err"]
            t["pct"] += s["pct"]
            if s["direction"] is not None:
                t["calls"] += 1
                t["hits"] += s["direction"]
    rows = [{"player": p, "rounds": t["rounds"], "avg_err": round(t["err"] / t["rounds"], 2),
             "avg_pct": round(t["pct"] / t["rounds"], 3),
             "direction": round(t["hits"] / t["calls"] * 100, 1) if t["calls"] else None}
            for p, t in totals.items()]
    return sorted(rows, key=lambda a: a["avg_err"])


def _award(table: dict, places: dict):
    for p, place in places.items():
        t = table.setdefault(p, {"points": 0, "firsts": 0, "days": 0})
        t["points"] += POINTS.get(place, 0)
        t["firsts"] += place == 1
        t["days"] += 1


def _standings(table: dict) -> list:
    rows = [{"entrant": p, **t} for p, t in table.items()]
    return sorted(rows, key=lambda r: (-r["points"], -r["firsts"], r["entrant"]))


def scoreboard(player, current_quarter) -> dict:
    """Quarterly standings for both leagues, and each human's win-loss-tie record against every bot.

    - Humans vs bots: days anyone guessed. You and Humanity are separate entrants alongside the bots.
    - Bot league: every finished day, bots only.
    head_to_head[quarter or "all"][bot][human] = [wins, losses, ties] for the human.
    """
    leagues = {current_quarter: {"main": {}, "bots": {}}}
    head_to_head = {}
    for r, bots, humans, _ in _finished(player):
        q = quarter(r["target_date"])
        league = leagues.setdefault(q, {"main": {}, "bots": {}})
        _award(league["bots"], placings(bots, r["actual"]))
        if not humans:
            continue
        _award(league["main"], placings({**bots, **humans}, r["actual"]))
        miss = {p: round(abs(v - r["actual"]), 2) for p, v in {**bots, **humans}.items()}
        for scope in (q, "all"):
            for bot in bots:
                for human in humans:
                    record = head_to_head.setdefault(scope, {}).setdefault(bot, {}).setdefault(human, [0, 0, 0])
                    record[0 if miss[human] < miss[bot] else 1 if miss[human] > miss[bot] else 2] += 1
    return {"current": current_quarter,
            "quarters": sorted(leagues, reverse=True),
            "leagues": {q: {name: _standings(t) for name, t in lg.items()} for q, lg in leagues.items()},
            "head_to_head": head_to_head}
