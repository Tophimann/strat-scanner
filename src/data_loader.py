"""
Data Loader — The Strat v2.0
==============================
Downloads 2 years of daily OHLCV per ticker via yfinance.
Caches each ticker as a Parquet file to avoid repeated full downloads.

Cache policy:
  - If cache file exists and is < 24h old: load from disk (fast)
  - Otherwise: download from yfinance and refresh cache

Cache location: <project_root>/data/cache/<SYMBOL>.parquet
"""

import os
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf
import warnings
warnings.filterwarnings("ignore")

# Project root = parent of src/
_SRC_DIR    = Path(__file__).parent
_CACHE_DIR  = _SRC_DIR.parent / "data" / "cache"
CACHE_MAX_AGE_HOURS = 20  # refresh after 20h (always fresh for EOD scans)


def cache_path(symbol: str) -> Path:
    """Return the parquet cache path for a symbol."""
    return _CACHE_DIR / f"{symbol.upper()}.parquet"


def load_or_fetch(symbol: str, years: int = 2, force_refresh: bool = False) -> pd.DataFrame:
    """
    Load daily OHLCV from cache if fresh, otherwise download from yfinance.

    Parameters
    ----------
    symbol        : Ticker symbol (Yahoo Finance format, e.g. 'AAPL', 'BRK-B')
    years         : Years of history to download (default 2)
    force_refresh : Force re-download even if cache is fresh

    Returns
    -------
    pd.DataFrame with columns Open/High/Low/Close/Volume and DatetimeIndex.
    Returns empty DataFrame on failure.
    """
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    fpath = cache_path(symbol)

    # Try loading from cache first
    if not force_refresh and fpath.exists():
        age = datetime.now() - datetime.fromtimestamp(fpath.stat().st_mtime)
        if age < timedelta(hours=CACHE_MAX_AGE_HOURS):
            try:
                df = pd.read_parquet(fpath)
                return df
            except Exception as e:
                print(f"[data_loader] Cache read error for {symbol}: {e} — re-downloading")

    # Download from yfinance
    try:
        df = yf.download(
            symbol,
            period=f"{years}y",
            interval="1d",
            auto_adjust=True,
            progress=False,
        )
    except Exception as e:
        print(f"[data_loader] Download error for {symbol}: {e}")
        return pd.DataFrame()

    if df is None or df.empty:
        return pd.DataFrame()

    # Flatten MultiIndex columns if present (happens with single-ticker downloads too)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    # Keep only standard OHLCV columns
    keep = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
    df = df[keep].copy()
    df = df.dropna(subset=["Close"])

    # Ensure DatetimeIndex (timezone-naive)
    df.index = pd.to_datetime(df.index).tz_localize(None)

    # Save to cache
    try:
        df.to_parquet(fpath)
    except Exception as e:
        print(f"[data_loader] Cache write error for {symbol}: {e}")

    return df


def load_batch(symbols: list, years: int = 2, force_refresh: bool = False) -> dict:
    """
    Load data for multiple symbols. Returns {symbol: DataFrame}.
    Silently skips symbols that fail.
    """
    result = {}
    for sym in symbols:
        df = load_or_fetch(sym, years=years, force_refresh=force_refresh)
        if not df.empty:
            result[sym] = df
    return result


def passes_liquidity(df: pd.DataFrame, min_avg_vol: int = 500_000, min_price: float = 5.0) -> bool:
    """
    Return True if symbol passes minimum liquidity requirements.
      - 20-day average volume >= min_avg_vol
      - Latest close >= min_price
    """
    if df is None or df.empty or len(df) < 5:
        return False
    avg_vol = df["Volume"].tail(20).mean()
    last_close = float(df["Close"].iloc[-1])
    return avg_vol >= min_avg_vol and last_close >= min_price
