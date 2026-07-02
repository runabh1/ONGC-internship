from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd


@dataclass
class AnomalyResult:
    timestamp: pd.Timestamp
    instance: str
    metric: str
    score: float
    is_anomaly: bool
    reason: str
    details: Dict[str, Any]


def to_dataframe(samples: Iterable[Dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(samples)
    if 'timestamp' in df.columns:
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='s', utc=True)
        df = df.sort_values('timestamp')
    return df


def rolling_mean(series: pd.Series, window: int = 5) -> pd.Series:
    return series.rolling(window=window, min_periods=1).mean()


def ewma(series: pd.Series, span: int = 5) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def z_score(series: pd.Series) -> pd.Series:
    mean = series.mean()
    std = series.std(ddof=0)
    return (series - mean) / (std if std != 0 else 1)


def ensure_df_index(df: pd.DataFrame, index: str = 'timestamp') -> pd.DataFrame:
    if index in df.columns:
        df = df.set_index(index)
    return df


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()
