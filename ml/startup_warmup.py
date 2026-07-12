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
from typing import Any


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
) -> dict[str, Any]:
    """
    Detect if collected metrics are in a startup warmup period.

    The warmup period is determined by either:
    1. Time-based: First N minutes after earliest timestamp for each instance
    2. Sample-based: First M samples collected for each instance

    If multiple instances are present, warmup is considered active if any
    instance still has insufficient history.
    """
    if df.empty:
        return {
            'in_warmup': True,
            'warmup_end_time': None,
            'samples_collected': 0,
            'sample_threshold': sample_threshold,
            'warmup_minutes': warmup_minutes,
            'samples_remaining': sample_threshold,
            'time_remaining_seconds': 0.0,
            'reason': 'No data collected yet - in initial warmup',
            'instance_infos': {},
        }

    try:
        df_copy = df.copy()
        if not pd.api.types.is_datetime64_any_dtype(df_copy['timestamp']):
            df_copy['timestamp'] = pd.to_datetime(df_copy['timestamp'], unit='s', utc=True)
    except Exception:
        return {
            'in_warmup': True,
            'warmup_end_time': None,
            'samples_collected': len(df),
            'sample_threshold': sample_threshold,
            'warmup_minutes': warmup_minutes,
            'samples_remaining': max(0, sample_threshold - len(df)),
            'time_remaining_seconds': 0.0,
            'reason': 'Could not parse timestamps - assuming warmup for safety',
            'instance_infos': {},
        }

    if 'instance' not in df_copy.columns:
        earliest_ts = df_copy['timestamp'].min()
        latest_ts = df_copy['timestamp'].max()
        num_samples = len(df_copy)
        time_based_warmup_end = earliest_ts + pd.Timedelta(minutes=warmup_minutes)
        sample_based_in_warmup = num_samples < sample_threshold
        time_based_in_warmup = latest_ts < time_based_warmup_end
        in_warmup = time_based_in_warmup or sample_based_in_warmup
        warmup_end_time = time_based_warmup_end
        if in_warmup:
            if time_based_in_warmup and sample_based_in_warmup:
                reason = (
                    f'Warmup period active: Only {num_samples} samples collected (need {sample_threshold}), '
                    f'and {warmup_minutes}min elapsed time not reached'
                )
            elif time_based_in_warmup:
                reason = (
                    f'Warmup period active: {warmup_minutes}min threshold not yet passed '
                    f'(elapsed: {(latest_ts - earliest_ts).total_seconds() / 60:.1f}min)'
                )
            else:
                reason = f'Warmup period active: Only {num_samples} samples collected (need {sample_threshold})'
        else:
            reason = 'Warmup period complete - sufficient data accumulated for reliable anomaly detection'

        samples_remaining = max(0, sample_threshold - num_samples)
        time_remaining = max(0.0, (warmup_end_time - latest_ts).total_seconds()) if warmup_end_time and latest_ts < warmup_end_time else 0.0
        return {
            'in_warmup': in_warmup,
            'warmup_end_time': warmup_end_time,
            'samples_collected': num_samples,
            'sample_threshold': sample_threshold,
            'warmup_minutes': warmup_minutes,
            'samples_remaining': samples_remaining,
            'time_remaining_seconds': time_remaining,
            'reason': reason,
            'instance_infos': {},
        }

    instance_infos: dict[str, dict[str, Any]] = {}
    active_instances: list[str] = []
    all_end_times: list[pd.Timestamp] = []
    total_samples = 0
    total_samples_remaining = 0

    for inst, group in df_copy.groupby('instance', sort=False):
        group = group.sort_values('timestamp').reset_index(drop=True)
        earliest_ts = group['timestamp'].min()
        latest_ts = group['timestamp'].max()
        num_samples = len(group)
        total_samples += num_samples

        time_based_warmup_end = earliest_ts + pd.Timedelta(minutes=warmup_minutes)
        sample_based_in_warmup = num_samples < sample_threshold
        time_based_in_warmup = latest_ts < time_based_warmup_end
        inst_in_warmup = time_based_in_warmup or sample_based_in_warmup

        samples_remaining = max(0, sample_threshold - num_samples)
        time_remaining = max(0.0, (time_based_warmup_end - latest_ts).total_seconds()) if latest_ts < time_based_warmup_end else 0.0

        instance_infos[str(inst)] = {
            'in_warmup': inst_in_warmup,
            'warmup_end_time': time_based_warmup_end,
            'samples_collected': num_samples,
            'samples_remaining': samples_remaining,
            'time_remaining_seconds': time_remaining,
        }

        if inst_in_warmup:
            active_instances.append(str(inst))
            total_samples_remaining += samples_remaining

        all_end_times.append(time_based_warmup_end)

    in_warmup = bool(active_instances)
    warmup_end_time = max(all_end_times) if all_end_times else None
    if in_warmup:
        reason = f'Warmup active for instances: {", ".join(active_instances)}'
    else:
        reason = 'Warmup period complete - sufficient data accumulated for reliable anomaly detection'

    return {
        'in_warmup': in_warmup,
        'warmup_end_time': warmup_end_time,
        'samples_collected': total_samples,
        'sample_threshold': sample_threshold,
        'warmup_minutes': warmup_minutes,
        'samples_remaining': total_samples_remaining,
        'time_remaining_seconds': max((info['time_remaining_seconds'] for info in instance_infos.values()), default=0.0),
        'reason': reason,
        'instance_infos': instance_infos,
    }


def filter_startup_samples(
    df: pd.DataFrame,
    warmup_minutes: int = 5,
    sample_threshold: int = 10,
) -> tuple[pd.DataFrame, dict[str, Any]]:
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

    try:
        df_copy = df.copy()
        if not pd.api.types.is_datetime64_any_dtype(df_copy['timestamp']):
            df_copy['timestamp'] = pd.to_datetime(df_copy['timestamp'], unit='s', utc=True)
    except Exception:
        return df, {
            'in_warmup': True,
            'warmup_end_time': None,
            'samples_collected': len(df),
            'sample_threshold': sample_threshold,
            'warmup_minutes': warmup_minutes,
            'samples_remaining': max(0, sample_threshold - len(df)),
            'time_remaining_seconds': 0.0,
            'reason': 'Could not parse timestamps - no filtering applied',
            'filtered_out': 0,
            'instance_infos': {},
        }

    if has_sufficient_historical_data(df_copy):
        warmup_info = detect_warmup_period(df_copy, warmup_minutes, sample_threshold)
        warmup_info['skipped_warmup_due_to_historical_data'] = True
        warmup_info['filtered_out'] = 0
        return df, warmup_info

    filtered_rows = []
    all_filtered_out = 0
    instance_infos: dict[str, dict[str, Any]] = {}

    for inst, group in df_copy.groupby('instance', sort=False):
        group = group.sort_values('timestamp').reset_index(drop=True)
        earliest_ts = group['timestamp'].min()
        warmup_end_time = earliest_ts + pd.Timedelta(minutes=warmup_minutes)
        keep_mask = (group['timestamp'] >= warmup_end_time) & (group.index >= sample_threshold)
        filtered_group = group[keep_mask].reset_index(drop=True)
        filtered_rows.append(filtered_group)
        filtered_out = len(group) - len(filtered_group)
        all_filtered_out += filtered_out

        samples_collected = len(group)
        samples_remaining = max(0, sample_threshold - samples_collected)
        time_remaining = max(0.0, (warmup_end_time - group['timestamp'].max()).total_seconds()) if group['timestamp'].max() < warmup_end_time else 0.0

        instance_infos[str(inst)] = {
            'filtered_out': filtered_out,
            'samples_collected': samples_collected,
            'samples_remaining': samples_remaining,
            'warmup_end_time': warmup_end_time,
            'time_remaining_seconds': time_remaining,
            'in_warmup': (samples_remaining > 0 or time_remaining > 0),
        }

    filtered_df = pd.concat(filtered_rows, ignore_index=True) if filtered_rows else pd.DataFrame(columns=df_copy.columns)

    warmup_info = detect_warmup_period(df_copy, warmup_minutes, sample_threshold)
    warmup_info['filtered_out'] = all_filtered_out
    warmup_info['instance_infos'] = instance_infos

    if all_filtered_out > 0:
        warmup_info['reason'] += f' — Filtered out {all_filtered_out} startup samples'

    return filtered_df, warmup_info
