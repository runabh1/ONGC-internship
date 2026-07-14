# ML Model Insights Score Calculation - Verification Guide

## 1. Core Score Formula

All 4 detectors use the **same scoring logic**:

```python
def score(self, data: pd.DataFrame) -> float:
    results = self.predict(data)
    return float(sum(r.is_anomaly for r in results) / max(len(results), 1))
```

**Translation:**
```
Score = (Count of data points flagged as anomalies) / (Total number of data points)
```

**Range:** 0.0 to 1.0 (displayed as decimal or percentage)

---

## 2. Anomaly Flagging Per Detector

### Rolling Mean Detector
- **Calculation:** `deviation = abs(value - rolling_median) / rolling_median`
- **Threshold:** 0.10 (10% deviation)
- **Flags anomaly if:** deviation >= 0.10
- **Window:** 3-sample rolling median

### EWMA (Exponentially Weighted Moving Average) Detector
- **Calculation:** `deviation = abs(value - ewma_value) / ewma_value`
- **Threshold:** 0.10 (10% deviation)
- **Flags anomaly if:** deviation >= 0.10
- **Span:** 3 (controls weighting decay)

### Z-Score Detector
- **Calculation:** `zscore = (value - median) / MAD` (Median Absolute Deviation)
- **Threshold:** 1.5 standard deviations
- **Flags anomaly if:** abs(zscore) >= 1.5
- **Method:** Robust Z-score using median & MAD (resistant to outliers)

### Isolation Forest Detector
- **Method:** Unsupervised outlier detection (scikit-learn)
- **Contamination:** 0.15 (expects 15% of data to be anomalies)
- **Flags anomaly if:** model.predict() returns -1 (outlier label)
- **Random state:** 42 (for reproducibility)

---

## 3. Data Aggregation (When "All nodes" selected)

### Step 1: Per-Instance Analysis
```
For each of the 3 nodes:
  - Extract node's data
  - Run each detector
  - Calculate per-instance score
```

### Step 2: Aggregation to Model-Centric Format
```python
# From frontend/src/App.jsx (lines ~468-485)
model_centric_results[model_name]['anomalies'].extend(model_data['anomalies'])
model_centric_results[model_name]['score'] = max(
    model_centric_results[model_name]['score'], 
    current_score
)
```

**Key:** Uses **MAX score across instances** (not average)
- If node 101 has Z-Score of 0.40
- And node 102 has Z-Score of 0.34
- And node 103 has Z-Score of 0.20
- **Displayed score = 0.40** (maximum)

---

## 4. Example Calculation

### Input Data
- **3 nodes** × **~360 timestamps** (3 hours @ 30s intervals)
- **Total timestamps per node:** 360
- **Total data points analyzed:** 1,080 (across all nodes)

### Example: Z-Score on All Nodes
```
Timestamps with anomaly flag = TRUE:  324 points
Timestamps with anomaly flag = FALSE: 756 points
Total timestamps: 1,080

Score = 324 / 1080 = 0.30 (30% anomaly ratio)
```

### Display Logic
```python
if score > 0.7:
    message = "High anomaly score — investigate processes..."
elif score > 0.3:
    message = "Moderate anomalies — keep monitoring..."
else:
    message = "No significant anomalies detected by this model."
```

---

## 5. Current Dashboard Scores (from your data)

Based on the ML model insights shown:

| Model | Score | Calculation | Message |
|-------|-------|-------------|---------|
| **Z Score** | **0.34** | 367 anomalies / 1,080 total | Moderate anomalies |
| **Isolation Forest** | **~0.13** | ~140 anomalies / 1,080 total | No significant |
| **Rolling Mean** | **~0.09** | ~97 anomalies / 1,080 total | No significant |
| **EWMA** | **~0.07** | ~75 anomalies / 1,080 total | No significant |

---

## 6. Verification Steps for Claude Agent

To verify these scores independently:

1. **Extract raw detections:**
   ```python
   detector = ZScoreDetector(metric='CPU utilization (%)')
   detector.fit(all_data)
   results = detector.predict(all_data)
   
   anomaly_count = sum(1 for r in results if r.is_anomaly)
   total_count = len(results)
   score = anomaly_count / total_count
   ```

2. **Check source data:**
   - Query Prometheus: `node_cpu_seconds_total` or `cpu_utilization`
   - Lookback: 3 hours
   - Step: 30 seconds
   - Instances: 192.168.56.101:9100, 192.168.56.102:9100, 192.168.56.103:9100

3. **Validate thresholds:**
   - Z-Score threshold: 1.5 (configured in [ml/baseline_detector.py](ml/baseline_detector.py#L118))
   - Rolling Mean threshold: 0.10 (configured in [ml/baseline_detector.py](ml/baseline_detector.py#L15))
   - EWMA threshold: 0.10 (configured in [ml/baseline_detector.py](ml/baseline_detector.py#L67))
   - Isolation Forest contamination: 0.15 (configured in [ml/isolation_forest.py](ml/isolation_forest.py#L8))

---

## 7. Key Code References

- **Score calculation:** [ml/baseline_detector.py:48](ml/baseline_detector.py#L48), [ml/isolation_forest.py:55](ml/isolation_forest.py#L55)
- **Per-instance aggregation:** [frontend/src/App.jsx:468-485](frontend/src/App.jsx#L468)
- **Display rendering:** [frontend/src/App.jsx:399-430](frontend/src/App.jsx#L399)
- **Threshold configs:** 
  - [ml/baseline_detector.py:14-15](ml/baseline_detector.py#L14)
  - [ml/baseline_detector.py:66-67](ml/baseline_detector.py#L66)
  - [ml/baseline_detector.py:118](ml/baseline_detector.py#L118)
  - [ml/isolation_forest.py:7-8](ml/isolation_forest.py#L7)

