"""
Startup warmup detection and filtering for anomaly detection models.

This module handles the startup period when Prometheus metrics are unreliable.
After system startup, the rate() function in Prometheus requires historical 
samples to calculate proper rates. Metrics during this warmup period can show 
artificially high CPU utilization (90-100%) that gradually settles to realistic 
values (40-50%), causing false anomaly detections.

This module provides utilities to:
1. Detect if we're in a startup warmup period
2. Filter out unreliable startup samples
3. Determine if historical data is sufficient to skip warmup

Production monitoring practices recommend ignoring initial samples to prevent
false positives during system initialization.
"""

from __future__ import annotations

import pandas as pd
from datetime import datetime, timedelta, timezone


def has_sufficient_historical_data(
    df: pd.DataFrame,
    min_duration_minutes: int = 5,
    min_samples_per_instance: int = 10,
) -> bool:
    """
    Check if the dataframe has sufficient historical data to skip warmup.
    
    This allows automatic warmup skipping when Prometheus already contains
    older historical data (e.g., 3+ hours of existing metrics).
    
    Args:
        df: DataFrame with metrics containing 'timestamp' and 'instance' columns
        min_duration_minutes: Minimum time span of data required (default 5 minutes)
        min_samples_per_instance: Minimum samples per instance (default 10)
    
    Returns:
        True if data is sufficient and warmup can be skipped, False otherwise
    
    Why: rate() in Prometheus needs samples spread over time to calculate proper
    rates. With sufficient historical data, startup warmup is not needed.
    """
    if df.empty:
        return False
    
    if 'timestamp' not in df.columns or 'instance' not in df.columns:
        return False
    
    # Ensure timestamp is datetime
    try:
        df_copy = df.copy()
        if not pd.api.types.is_datetime64_any_dtype(df_copy['timestamp']):
            df_copy['timestamp'] = pd.to_datetime(df_copy['timestamp'], unit='s', utc=True)
    except Exception:
        return False
    
    # Check time span
    time_span = df_copy['timestamp'].max() - df_copy['timestamp'].min()
    min_duration = pd.Timedelta(minutes=min_duration_minutes)
    
    if time_span < min_duration:
        return False
    
    # Check samples per instance
    samples_per_instance = df_copy.groupby('instance').size()
    if (samples_per_instance < min_samples_per_instance).any():
        return False
    
    return True


def detect_warmup_period(
    df: pd.DataFrame,
    warmup_minutes: int = 5,
    sample_threshold: int = 10,
) -> dict[str, bool | int]:
    """
    Detect if collected metrics are in a startup warmup period.
    
    The warmup period is determined by either:
    1. Time-based: First N minutes after earliest timestamp
    2. Sample-based: First M samples collected
    
    Returns whichever is MORE PERMISSIVE (i.e., the larger period).
    This ensures we don't prematurely exit warmup.
    
    Args:
        df: DataFrame with metrics containing 'timestamp' and optional 'instance'
        warmup_minutes: Number of minutes from start to consider as warmup (default 5)
        sample_threshold: Minimum samples before considering warmup complete (default 10)
    
    Returns:
        Dict with:
        - 'in_warmup': True if system is in warmup period, False otherwise
        - 'warmup_end_time': Datetime when warmup period ends (UTC)
        - 'samples_collected': Number of samples collected
        - 'reason': String explaining why we're in/out of warmup
    
    Why: After Prometheus/system startup, the rate() function needs time to
    accumulate samples. Initial metrics can be unstable. Following production
    monitoring practices, we ignore these early samples.
    """
    if df.empty:
        return {
            'in_warmup': True,
            'warmup_end_time': None,
            'samples_collected': 0,
            'reason': 'No data collected yet - in initial warmup',
        }
    
    # Ensure timestamp is datetime
    try:
        df_copy = df.copy()
        if not pd.api.types.is_datetime64_any_dtype(df_copy['timestamp']):
            df_copy['timestamp'] = pd.to_datetime(df_copy['timestamp'], unit='s', utc=True)
    except Exception:
        return {
            'in_warmup': True,
            'warmup_end_time': None,
            'samples_collected': len(df),
            'reason': 'Could not parse timestamps - assuming warmup for safety',
        }
    
    earliest_ts = df_copy['timestamp'].min()
    latest_ts = df_copy['timestamp'].max()
    num_samples = len(df_copy)
    
    # Time-based warmup end
    time_based_warmup_end = earliest_ts + pd.Timedelta(minutes=warmup_minutes)
    
    # Sample-based warmup end: when sample_threshold is reached
    sample_based_in_warmup = num_samples < sample_threshold
    
    # Determine overall warmup status: use time OR sample threshold, whichever is more permissive
    time_based_in_warmup = latest_ts < time_based_warmup_end
    in_warmup = time_based_in_warmup or sample_based_in_warmup
    
    # Warmup ends at the LATER of (time_based_end, whenever sample_threshold is reached)
    warmup_end_time = time_based_warmup_end
    
    if in_warmup:
        if time_based_in_warmup and sample_based_in_warmup:
            reason = f'Warmup period active: Only {num_samples} samples collected (need {sample_threshold}), and {warmup_minutes}min elapsed time not reached'
        elif time_based_in_warmup:
            reason = f'Warmup period active: {warmup_minutes}min threshold not yet passed (elapsed: {(latest_ts - earliest_ts).total_seconds() / 60:.1f}min)'
        else:
            reason = f'Warmup period active: Only {num_samples} samples collected (need {sample_threshold})'
    else:
        reason = 'Warmup period complete - sufficient data accumulated for reliable anomaly detection'
    
    return {
        'in_warmup': in_warmup,
        'warmup_end_time': warmup_end_time,
        'samples_collected': num_samples,
        'reason': reason,
    }


def filter_startup_samples(
    df: pd.DataFrame,
    warmup_minutes: int = 5,
    sample_threshold: int = 10,
) -> tuple[pd.DataFrame, dict[str, bool | int]]:
    """
    Remove unreliable startup samples from metrics data.
    
    If the system is in warmup period and has sufficient historical data,
    no filtering is performed (assuming we have good baseline data).
    
    Otherwise, removes the first N minutes AND first M samples from the beginning
    of the dataset to exclude startup anomalies.
    
    Args:
        df: DataFrame with metrics containing 'timestamp' column
        warmup_minutes: Warmup duration in minutes (default 5)
        sample_threshold: Sample count threshold (default 10)
    
    Returns:
        Tuple of (filtered_dataframe, warmup_info_dict)
        - filtered_dataframe: Data without startup samples
        - warmup_info_dict: Info about warmup state and filtering
    
    Why: Startup metrics can be unstable because:
    1. rate() requires historical samples to calculate rates (Prometheus limitation)
    2. System initialization can cause temporary spikes
    3. Cache warming and I/O initialization can cause anomalies
    This follows production monitoring best practices.
    """
    if df.empty:
        warmup_info = detect_warmup_period(df, warmup_minutes, sample_threshold)
        return df, warmup_info
    
    # Ensure timestamp is datetime
    try:
        df_copy = df.copy()
        if not pd.api.types.is_datetime64_any_dtype(df_copy['timestamp']):
            df_copy['timestamp'] = pd.to_datetime(df_copy['timestamp'], unit='s', utc=True)
    except Exception:
        return df, {
            'in_warmup': True,
            'warmup_end_time': None,
            'samples_collected': len(df),
            'reason': 'Could not parse timestamps - no filtering applied',
            'filtered_out': 0,
        }
    
    # Check if historical data is sufficient - if so, skip warmup entirely
    if has_sufficient_historical_data(df_copy):
        warmup_info = detect_warmup_period(df_copy, warmup_minutes, sample_threshold)
        warmup_info['skipped_warmup_due_to_historical_data'] = True
        warmup_info['filtered_out'] = 0
        return df, warmup_info
    
    earliest_ts = df_copy['timestamp'].min()
    warmup_end_time = earliest_ts + pd.Timedelta(minutes=warmup_minutes)
    
    # Filter: keep only samples after warmup period AND after first sample_threshold samples
    filtered_df = df_copy[
        (df_copy['timestamp'] >= warmup_end_time) &
        (df_copy.index >= sample_threshold)
    ].reset_index(drop=True)
    
    filtered_out = len(df) - len(filtered_df)
    
    warmup_info = detect_warmup_period(df_copy, warmup_minutes, sample_threshold)
    warmup_info['filtered_out'] = filtered_out
    
    if filtered_out > 0:
        warmup_info['reason'] += f' — Filtered out {filtered_out} startup samples'
    
    return filtered_df, warmup_info
