from __future__ import annotations

from typing import Any, Dict, List

from ml.utils import AnomalyResult


def explain_anomalies(results: List[AnomalyResult]) -> List[Dict[str, Any]]:
    return [
        {
            'timestamp': result.timestamp.isoformat(),
            'instance': result.instance,
            'metric': result.metric,
            'score': result.score,
            'is_anomaly': result.is_anomaly,
            'reason': result.reason,
            'details': result.details,
        }
        for result in results
        if result.is_anomaly
    ]
