from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

import pandas as pd
from sklearn.ensemble import IsolationForest

from ml.utils import AnomalyResult, ensure_df_index


@dataclass
class IsolationForestDetector:
    metric: str
    contamination: float = 0.05
    random_state: int = 42
    model: IsolationForest | None = None

    def fit(self, data: pd.DataFrame) -> None:
        df = ensure_df_index(data, 'timestamp')
        values = df[['value']].astype(float)
        self.model = IsolationForest(
            contamination=self.contamination,
            random_state=self.random_state,
        )
        self.model.fit(values)

    def predict(self, data: pd.DataFrame) -> List[AnomalyResult]:
        if self.model is None:
            raise RuntimeError('Model not fitted yet')
        df = ensure_df_index(data, 'timestamp')
        values = df[['value']].astype(float)
        labels = self.model.predict(values)
        scores = -self.model.decision_function(values)
        results: List[AnomalyResult] = []
        for ts, instance, value, label, score in zip(df.index, df['instance'], df['value'], labels, scores):
            is_anomaly = label == -1
            results.append(
                AnomalyResult(
                    timestamp=ts,
                    instance=str(instance),
                    metric=self.metric,
                    score=float(score),
                    is_anomaly=is_anomaly,
                    reason='isolation forest outlier' if is_anomaly else 'normal',
                    details={'value': float(value), 'raw_label': int(label)},
                )
            )
        return results

    def score(self, data: pd.DataFrame) -> float:
        results = self.predict(data)
        return float(sum(r.is_anomaly for r in results) / max(len(results), 1))

    def explain(self, data: pd.DataFrame) -> Dict[str, Any]:
        return {
            'metric': self.metric,
            'contamination': self.contamination,
            'random_state': self.random_state,
            'rule': 'Isolation Forest anomaly score',
        }
