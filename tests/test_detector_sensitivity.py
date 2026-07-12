import os
import unittest
from pathlib import Path

import pandas as pd

from ml.baseline_detector import EWMAAnomalyDetector, RollingMeanDetector, ZScoreDetector
from ml.isolation_forest import IsolationForestDetector
from ml.startup_warmup import filter_startup_samples


class DetectorSensitivityTests(unittest.TestCase):
    def test_detector_defaults_are_less_sensitive(self) -> None:
        self.assertEqual(RollingMeanDetector(metric='cpu').window, 15)
        self.assertEqual(RollingMeanDetector(metric='cpu').threshold, 0.20)

        self.assertEqual(EWMAAnomalyDetector(metric='cpu').span, 12)
        self.assertEqual(EWMAAnomalyDetector(metric='cpu').threshold, 0.20)

        self.assertEqual(ZScoreDetector(metric='cpu').threshold, 2.5)

        self.assertEqual(IsolationForestDetector(metric='cpu').contamination, 0.03)

    def test_filter_startup_samples_removes_warmup_period(self) -> None:
        df = pd.DataFrame(
            {
                'timestamp': pd.date_range('2026-01-01 00:00', periods=8, freq='T'),
                'instance': ['node1'] * 8,
                'value': [20.0] * 8,
            }
        )

        filtered_df, warmup_info = filter_startup_samples(df, warmup_minutes=5, sample_threshold=10)

        self.assertTrue(warmup_info.get('in_warmup'))
        self.assertEqual(filtered_df.shape[0], 0)
        self.assertEqual(warmup_info.get('samples_collected'), 8)
        self.assertEqual(warmup_info.get('sample_threshold'), 10)

    def test_isolation_forest_detects_clear_spike(self) -> None:
        model_dir = Path('ml') / 'models'
        detector = IsolationForestDetector(metric='cpu', contamination=0.03, min_train_samples=20, retrain_samples=50, retrain_hours=0.0)
        for path in detector._model_paths().values():
            if path.exists():
                path.unlink()

        df = pd.DataFrame(
            {
                'timestamp': pd.date_range('2026-01-01 00:00', periods=100, freq='T'),
                'instance': ['node1'] * 100,
                'value': [30.0] * 80 + [95.0] * 20,
            }
        )

        detector.fit(df)
        preds = detector.predict(df)
        self.assertIsNotNone(detector.raw_score_threshold)
        self.assertGreater(sum(p.is_anomaly for p in preds), 0)
        self.assertTrue(preds[-1].is_anomaly or any(preds[-5:]))


if __name__ == '__main__':
    unittest.main()
