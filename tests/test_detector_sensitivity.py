import unittest

from ml.baseline_detector import EWMAAnomalyDetector, RollingMeanDetector, ZScoreDetector
from ml.isolation_forest import IsolationForestDetector


class DetectorSensitivityTests(unittest.TestCase):
    def test_detector_defaults_are_less_sensitive(self) -> None:
        self.assertEqual(RollingMeanDetector(metric='cpu').window, 15)
        self.assertEqual(RollingMeanDetector(metric='cpu').threshold, 0.20)

        self.assertEqual(EWMAAnomalyDetector(metric='cpu').span, 12)
        self.assertEqual(EWMAAnomalyDetector(metric='cpu').threshold, 0.20)

        self.assertEqual(ZScoreDetector(metric='cpu').threshold, 2.5)

        self.assertEqual(IsolationForestDetector(metric='cpu').contamination, 0.03)


if __name__ == '__main__':
    unittest.main()
