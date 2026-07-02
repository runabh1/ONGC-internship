from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import pandas as pd
import requests

from ml.utils import to_dataframe

logger = logging.getLogger(__name__)


class PrometheusClient:
    def __init__(self, base_url: str, timeout: int = 10) -> None:
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        response = requests.get(url, params=params, timeout=self.timeout)
        response.raise_for_status()
        result = response.json()
        if result.get('status') != 'success':
            raise RuntimeError(f"Prometheus query failed: {result}")
        return result['data']

    def query_range(self, query: str, start: str, end: str, step: str) -> pd.DataFrame:
        params = {'query': query, 'start': start, 'end': end, 'step': step}
        data = self._get('/api/v1/query_range', params=params)
        samples = []
        for item in data.get('result', []):
            metric = item['metric']
            for timestamp, value in item['values']:
                samples.append({
                    'timestamp': float(timestamp),
                    'instance': metric.get('instance', ''),
                    'metric_name': metric.get('__name__', ''),
                    'value': float(value),
                    'labels': metric,
                })
        return to_dataframe(samples)

    def query(self, query: str, time: Optional[str] = None) -> pd.DataFrame:
        params = {'query': query}
        if time is not None:
            params['time'] = time
        data = self._get('/api/v1/query', params=params)
        samples = []
        for item in data.get('result', []):
            metric = item['metric']
            value = item['value']
            samples.append({
                'timestamp': float(value[0]),
                'instance': metric.get('instance', ''),
                'metric_name': metric.get('__name__', ''),
                'value': float(value[1]),
                'labels': metric,
            })
        return to_dataframe(samples)
