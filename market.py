"""S&P 500 price history and NYSE session timing."""
import os
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf
from pandas.tseries.holiday import (
    AbstractHolidayCalendar, GoodFriday, Holiday, USLaborDay, USMartinLutherKingJr,
    USMemorialDay, USPresidentsDay, USThanksgivingDay, nearest_workday, sunday_to_monday,
)
from pandas.tseries.offsets import CustomBusinessDay

ET = ZoneInfo("America/New_York")
TICKER = "^GSPC"
COLUMNS = ["Open", "High", "Low", "Close", "Volume"]
DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).parent / "data"))
SNAPSHOT = Path(__file__).parent / "sp500.csv"  # committed history, so a fresh deploy only needs recent closes
CACHE = DATA_DIR / "sp500.csv"                   # the snapshot plus every close fetched since
MARKET_OPEN = time(9, 30)
CLOSE_FINAL = time(16, 15)  # give the closing print a few minutes to settle


class NYSEHolidays(AbstractHolidayCalendar):
    rules = [
        Holiday("New Year's Day", month=1, day=1, observance=sunday_to_monday),
        USMartinLutherKingJr, USPresidentsDay, GoodFriday, USMemorialDay,
        Holiday("Juneteenth", month=6, day=19, start_date="2022-01-01", observance=nearest_workday),
        Holiday("Independence Day", month=7, day=4, observance=nearest_workday),
        USLaborDay, USThanksgivingDay,
        Holiday("Christmas", month=12, day=25, observance=nearest_workday),
    ]


SESSION = CustomBusinessDay(calendar=NYSEHolidays())


def now_et() -> datetime:
    return datetime.now(ET)


def next_session(day) -> pd.Timestamp:
    """First trading day strictly after `day`."""
    return pd.Timestamp(day).normalize() + SESSION


def lock_time(target: pd.Timestamp) -> datetime:
    """Guesses for a session's close lock when that session opens."""
    return datetime.combine(target.date(), MARKET_OPEN, ET)


def _download(start: str) -> pd.DataFrame:
    raw = yf.Ticker(TICKER).history(start=start, auto_adjust=False)
    if raw.empty:
        raise ValueError("Yahoo returned no data")
    df = raw[COLUMNS].copy()
    df.index = df.index.tz_localize(None).normalize()
    return df.round(2)


def load_history(now: datetime) -> pd.DataFrame:
    """Daily OHLCV since 2005, indexed by naive date: the saved history plus whatever Yahoo has added since.
    If Yahoo can't be reached, the saved prices are used as they are."""
    saved = CACHE if CACHE.exists() else SNAPSHOT
    df = pd.read_csv(saved, index_col=0, parse_dates=True) if saved.exists() else None
    # Re-fetch the last week too, in case Yahoo revised a recent close.
    start = "2005-01-01" if df is None else (df.index[-1] - pd.Timedelta(days=7)).strftime("%Y-%m-%d")
    try:
        recent = _download(start)
        df = recent if df is None else pd.concat([df[df.index < recent.index[0]], recent])
    except Exception as e:
        if df is None:
            raise
        print(f"Couldn't reach Yahoo Finance ({e}); using saved prices through {df.index[-1].date()}", flush=True)

    # During the session Yahoo's bar for today is a live quote, not a close.
    if df.index[-1].date() == now.date() and now.time() < CLOSE_FINAL:
        df = df.iloc[:-1]
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(CACHE)
    return df
