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
            if baseline == 0.0:
                deviation = 0.0
            else:
                deviation = (value - baseline) / baseline
            min_delta = max(1.0, baseline * 0.20)
            is_anomaly = (
                value > baseline and
                deviation >= self.threshold and
                (value - baseline) >= min_delta
            )
            results.append(
                AnomalyResult(
                    timestamp=ts,
                    instance=str(instance),
                    metric=self.metric,
                    score=float(abs(deviation)),
                    is_anomaly=is_anomaly,
                    reason='upward rolling median deviation' if is_anomaly else 'normal',
                    details={
                        'value': float(value),
                        'baseline': float(baseline),
                        'threshold': self.threshold,
                        'min_delta': float(min_delta),
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
            if expected == 0.0:
                deviation = 0.0
            else:
                deviation = (value - expected) / expected
            min_delta = max(1.0, expected * 0.20)
            is_anomaly = (
                value > expected and
                deviation >= self.threshold and
                (value - expected) >= min_delta
            )
            results.append(
                AnomalyResult(
                    timestamp=ts,
                    instance=str(instance),
                    metric=self.metric,
                    score=float(abs(deviation)),
                    is_anomaly=is_anomaly,
                    reason='upward ewma deviation' if is_anomaly else 'normal',
                    details={
                        'value': float(value),
                        'ewma': float(expected),
                        'threshold': self.threshold,
                        'min_delta': float(min_delta),
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
        self.fitted_std = float(max((values - values.median()).abs().median() * 1.4826, 1.0))

    def predict(self, data: pd.DataFrame) -> List[AnomalyResult]:
        df = ensure_df_index(data, 'timestamp')
        values = df['value'].astype(float)

        # Use a stable baseline if the detector was fitted previously.
        if self.fitted_mean is not None and self.fitted_std is not None:
            baseline_median = self.fitted_mean
            baseline_mad = self.fitted_std
        else:
            baseline_median = float(values.median())
            baseline_mad = float(max((values - values.median()).abs().median() * 1.4826, 1.0))

        zscores = (values - baseline_median) / baseline_mad
        results: List[AnomalyResult] = []
        for ts, instance, value, score in zip(df.index, df['instance'], df['value'], zscores):
            is_anomaly = score >= self.threshold
            results.append(
                AnomalyResult(
                    timestamp=ts,
                    instance=str(instance),
                    metric=self.metric,
                    score=float(max(score, 0.0)),
                    is_anomaly=is_anomaly,
                    reason='positive z-score anomaly' if is_anomaly else 'normal',
                    details={
                        'value': float(value),
                        'zscore': float(score),
                        'threshold': self.threshold,
                        'median': baseline_median,
                        'mad': baseline_mad,
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
