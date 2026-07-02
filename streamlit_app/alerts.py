from __future__ import annotations

from typing import List, Dict


def severity_from_score(score: float) -> str:
    if score >= 0.75:
        return 'critical'
    if score >= 0.4:
        return 'warning'
    return 'normal'


def summarize_anomalies(results: List[Dict]) -> Dict[str, int]:
    summary = {'critical': 0, 'warning': 0, 'normal': 0}
    for r in results:
        s = severity_from_score(r.get('score', 0.0))
        summary[s] = summary.get(s, 0) + 1
    return summary
