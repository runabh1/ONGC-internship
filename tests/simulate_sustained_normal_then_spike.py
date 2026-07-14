import sys
from datetime import datetime, timedelta, timezone

import pandas as pd

sys.path.insert(0, r'c:\Users\aruna\OneDrive\Desktop\Ongc-cluster-monitor')
from ml.baseline_detector import RollingMeanDetector, EWMAAnomalyDetector, ZScoreDetector

inst = 'node-test'
start = datetime.now(timezone.utc) - timedelta(minutes=10)

rolling = RollingMeanDetector(metric='cpu')
ewma = EWMAAnomalyDetector(metric='cpu')
zscore = ZScoreDetector(metric='cpu')

rows = []
# 60 seconds of normal traffic around 10%
for i in range(60):
    rows.append({'timestamp': start + timedelta(seconds=i), 'instance': inst, 'value': 10.0})
# 120 seconds of sustained spike at 100%
for i in range(60, 180):
    rows.append({'timestamp': start + timedelta(seconds=i), 'instance': inst, 'value': 100.0})

df = pd.DataFrame(rows)
df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)

print('Running normal baseline then sustained spike simulation')

for detector_name, detector in [
    ('Rolling Mean', rolling),
    ('EWMA', ewma),
    ('Z Score', zscore),
]:
    if detector_name == 'Z Score':
        detector.fit(df.iloc[:60])

    preds = detector.predict(df)
    anomaly_indices = [i for i, p in enumerate(preds) if p.is_anomaly]
    if anomaly_indices:
        first = anomaly_indices[0]
        print(f'{detector_name} first anomaly at sample {first+1} timestamp {preds[first].timestamp} value={preds[first].details.get("value")} score={preds[first].score:.3f}')
        print(f'  total anomalies: {len(anomaly_indices)}')
        print('  sample anomalies:')
        for idx in anomaly_indices[:5]:
            p = preds[idx]
            print(f'    {idx+1}: ts={p.timestamp}, value={p.details.get("value")}, score={p.score:.3f}, reason={p.reason}')
    else:
        print(f'{detector_name} did not flag any anomalies')

print('Simulation complete')
