from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import pandas as pd

from ml.utils import AnomalyResult, ensure_df_index, rolling_mean, ewma, z_score


@dataclass
class RollingMeanDetector:
    metric: str
    window: int = 15
    threshold: float = 0.20

    def fit(self, data: pd.DataFrame) -> None:
        # Rolling mean detector does not memorize a model beyond hyperparameters.
        pass

    def predict(self, data: pd.DataFrame) -> List[AnomalyResult]:
        df = ensure_df_index(data, 'timestamp')
        series = df['value'].astype(float)
        rolling = series.rolling(window=self.window, min_periods=1).median()
        results: List[AnomalyResult] = []
        for ts, instance, value, baseline in zip(df.index, df['instance'], series, rolling):
            deviation = abs(value - baseline) / (baseline if baseline != 0 else 1)
            is_anomaly = deviation >= self.threshold
            results.append(
                AnomalyResult(
                    timestamp=ts,
                    instance=str(instance),
                    metric=self.metric,
                    score=float(deviation),
                    is_anomaly=is_anomaly,
                    reason='robust rolling median deviation' if is_anomaly else 'normal',
                    details={
                        'value': float(value),
                        'baseline': float(baseline),
                        'threshold': self.threshold,
                    },
                )
            )
        return results

    def score(self, data: pd.DataFrame) -> float:
        results = self.predict(data)
        return float(sum(r.is_anomaly for r in results) / max(len(results), 1))

    def explain(self, data: pd.DataFrame) -> Dict[str, Any]:
        return {
            'metric': self.metric,
            'window': self.window,
            'threshold': self.threshold,
            'rule': 'rolling median percentage deviation',
        }


@dataclass
class EWMAAnomalyDetector:
    metric: str
    span: int = 12
    threshold: float = 0.20

    def fit(self, data: pd.DataFrame) -> None:
        pass

    def predict(self, data: pd.DataFrame) -> List[AnomalyResult]:
        df = ensure_df_index(data, 'timestamp')
        series = df['value'].astype(float)
        ewma_series = ewma(series, span=self.span)
        results: List[AnomalyResult] = []
        for ts, instance, value, expected in zip(df.index, df['instance'], series, ewma_series):
            deviation = abs(value - expected) / (expected if expected != 0 else 1)
            is_anomaly = deviation >= self.threshold
            results.append(
                AnomalyResult(
                    timestamp=ts,
                    instance=str(instance),
                    metric=self.metric,
                    score=float(deviation),
                    is_anomaly=is_anomaly,
                    reason='short-span ewma deviation' if is_anomaly else 'normal',
                    details={
                        'value': float(value),
                        'ewma': float(expected),
                        'threshold': self.threshold,
                    },
                )
            )
        return results

    def score(self, data: pd.DataFrame) -> float:
        results = self.predict(data)
        return float(sum(r.is_anomaly for r in results) / max(len(results), 1))

    def explain(self, data: pd.DataFrame) -> Dict[str, Any]:
        return {
            'metric': self.metric,
            'span': self.span,
            'threshold': self.threshold,
            'rule': 'short-span exponentially weighted moving average deviation',
        }


@dataclass
class ZScoreDetector:
    metric: str
    threshold: float = 2.5
    fitted_mean: Optional[float] = None
    fitted_std: Optional[float] = None

    def fit(self, data: pd.DataFrame) -> None:
        df = ensure_df_index(data, 'timestamp')
        values = df['value'].astype(float)
        self.fitted_mean = float(values.median())
        self.fitted_std = float((values - values.median()).abs().median() * 1.4826)

    def predict(self, data: pd.DataFrame) -> List[AnomalyResult]:
        df = ensure_df_index(data, 'timestamp')
        values = df['value'].astype(float)
        mad = (values - values.median()).abs().median()
        zscores = (values - values.median()) / (mad if mad != 0 else 1)
        results: List[AnomalyResult] = []
        for ts, instance, value, score in zip(df.index, df['instance'], df['value'], zscores):
            is_anomaly = abs(score) >= self.threshold
            results.append(
                AnomalyResult(
                    timestamp=ts,
                    instance=str(instance),
                    metric=self.metric,
                    score=float(abs(score)),
                    is_anomaly=is_anomaly,
                    reason='robust z-score anomaly' if is_anomaly else 'normal',
                    details={
                        'value': float(value),
                        'zscore': float(score),
                        'threshold': self.threshold,
                        'median': self.fitted_mean,
                        'mad': self.fitted_std,
                    },
                )
            )
        return results

    def score(self, data: pd.DataFrame) -> float:
        results = self.predict(data)
        return float(sum(r.is_anomaly for r in results) / max(len(results), 1))

    def explain(self, data: pd.DataFrame) -> Dict[str, Any]:
        return {
            'metric': self.metric,
            'threshold': self.threshold,
            'mean': self.fitted_mean,
            'std': self.fitted_std,
            'rule': 'z-score anomaly detection',
        }
