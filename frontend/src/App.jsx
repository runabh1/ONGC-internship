import { useEffect, useState, useCallback, useMemo, useRef } from 'react';
import axios from 'axios';
import {
  AreaChart, Area, LineChart, Line, BarChart, Bar,
  ResponsiveContainer, Tooltip as ReTooltip,
  XAxis, YAxis, CartesianGrid, Legend,
} from 'recharts';

const API = process.env.REACT_APP_API_URL || `${window.location.protocol}//${window.location.hostname}:8000`;
const WS_URL = process.env.REACT_APP_WS_URL || `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.hostname}:8000/api/ws/live`;

/* ─────────────────────────────────────────────────────────────────────────
   METRIC DEFINITIONS
   ───────────────────────────────────────────────────────────────────────── */
const METRICS = {
  'CPU Used %': { key: 'cpu_used_pct', unit: '%', color: '#7dd3fc', category: 'CPU' },
  'CPU Idle %': { key: 'cpu_idle_pct', unit: '%', color: '#86efac', category: 'CPU' },
  'CPU User %': { key: 'cpu_user_pct', unit: '%', color: '#bae6fd', category: 'CPU' },
  'CPU System %': { key: 'cpu_system_pct', unit: '%', color: '#c4b5fd', category: 'CPU' },
  'IOWait %': { key: 'cpu_iowait_pct', unit: '%', color: '#fdba74', category: 'CPU' },
  'Memory Used %': { key: 'memory_used_pct', unit: '%', color: '#ddd6fe', category: 'Memory' },
  'Memory Total GB': { key: 'memory_total_gb', unit: 'GB', color: '#fbcfe8', category: 'Memory' },
  'Load (1m)': { key: 'load_one', unit: '', color: '#a7f3d0', category: 'Load' },
  'Load (5m)': { key: 'load_five', unit: '', color: '#d9f99d', category: 'Load' },
  'Load (15m)': { key: 'load_fifteen', unit: '', color: '#a5f3fc', category: 'Load' },
  'Net RX (B/s)': { key: 'net_rx_bytes', unit: 'B/s', color: '#fde68a', category: 'Network' },
  'Net TX (B/s)': { key: 'net_tx_bytes', unit: 'B/s', color: '#fbcfe8', category: 'Network' },
  'Disk Used %': { key: 'disk_used_pct', unit: '%', color: '#fef08a', category: 'Disk' },
  'Disk Read (B/s)': { key: 'disk_read_bytes', unit: 'B/s', color: '#bfdbfe', category: 'Disk' },
  'Disk Write (B/s)': { key: 'disk_write_bytes', unit: 'B/s', color: '#bae6fd', category: 'Disk' },
  'Procs Running': { key: 'procs_running', unit: '', color: '#c7d2fe', category: 'Processes' },
  'Procs Blocked': { key: 'procs_blocked', unit: '', color: '#fbcfe8', category: 'Processes' },
  'Logged-in Users': { key: 'node_logind_sessions', unit: 'users', color: '#fde68a', category: 'Users' },
};

const METRIC_GROUPS = [
  { label: 'CPU', keys: ['cpu_used_pct', 'cpu_idle_pct', 'cpu_user_pct', 'cpu_system_pct', 'cpu_iowait_pct'] },
  { label: 'Memory', keys: ['memory_used_pct', 'memory_total_gb'] },
  { label: 'Load', keys: ['load_one', 'load_five', 'load_fifteen'] },
  { label: 'Network', keys: ['net_rx_bytes', 'net_tx_bytes'] },
  { label: 'Disk', keys: ['disk_used_pct', 'disk_read_bytes', 'disk_write_bytes'] },
  { label: 'Processes', keys: ['procs_running', 'procs_blocked'] },
  { label: 'Users', keys: ['node_logind_sessions'] },
];

// All metrics available in the grid view
const GRID_METRICS = [
  { key: 'cpu_used_pct', label: 'CPU %', unit: '%', color: '#7dd3fc' },
  { key: 'cpu_idle_pct', label: 'CPU Idle %', unit: '%', color: '#86efac' },
  { key: 'cpu_user_pct', label: 'CPU User %', unit: '%', color: '#bae6fd' },
  { key: 'cpu_system_pct', label: 'CPU Sys %', unit: '%', color: '#c4b5fd' },
  { key: 'cpu_iowait_pct', label: 'IOWait %', unit: '%', color: '#fdba74' },
  { key: 'memory_used_pct', label: 'MEM %', unit: '%', color: '#ddd6fe' },
  { key: 'memory_total_gb', label: 'MEM GB', unit: 'GB', color: '#fbcfe8' },
  { key: 'load_one', label: 'Load 1m', unit: '', color: '#a7f3d0' },
  { key: 'load_five', label: 'Load 5m', unit: '', color: '#d9f99d' },
  { key: 'load_fifteen', label: 'Load 15m', unit: '', color: '#a5f3fc' },
  { key: 'net_rx_bytes', label: 'Net RX', unit: 'B/s', color: '#fde68a' },
  { key: 'net_tx_bytes', label: 'Net TX', unit: 'B/s', color: '#fbcfe8' },
  { key: 'disk_used_pct', label: 'Disk %', unit: '%', color: '#fef08a' },
  { key: 'disk_read_bytes', label: 'Disk Read', unit: 'B/s', color: '#bfdbfe' },
  { key: 'disk_write_bytes', label: 'Disk Write', unit: 'B/s', color: '#bae6fd' },
  { key: 'procs_running', label: 'Procs', unit: '', color: '#c7d2fe' },
  { key: 'procs_blocked', label: 'Blocked', unit: '', color: '#fbcfe8' },
  { key: 'node_logind_sessions', label: 'Users', unit: 'users', color: '#fde68a' },
];

const NODE_COLORS = [
  '#7dd3fc', '#86efac', '#fde68a', '#fbcfe8', '#c4b5fd',
  '#bfdbfe', '#bae6fd', '#d9f99d', '#fef08a', '#c7d2fe',
  '#fda4af', '#fcd34d', '#a5f3fc', '#d8b4fe', '#fda4af',
  '#e0f2fe', '#e9d5ff', '#fef3c7', '#d9f99d', '#fbcfe8',
];

/* ─────────────────────── Helpers ─────────────────────── */
// Convert UTC timestamp to IST (UTC+5:30)
// Ensure bare UTC ISO strings (no Z / no offset) are always parsed as UTC.
// The backend uses datetime.utcnow().isoformat() which omits the 'Z' suffix.
// Without it, JS Date() may treat the string as local time causing ~5:30h drift.
const toUTC = (iso) => {
  if (!iso) return null;
  // Already has timezone info (Z, +, -)
  if (/[Zz]$|[+-]\d{2}:?\d{2}$/.test(iso)) return new Date(iso);
  // Bare UTC string — append Z so JS treats it as UTC
  return new Date(iso + 'Z');
};

const toIST = (iso) => {
  if (!iso) return null;
  // toUTC gives a proper UTC Date; getTime() is always UTC ms.
  // We then format it in IST using Intl (no manual +5:30 needed).
  return toUTC(iso);
};

const fmtTime = (iso) => {
  if (!iso) return '—';
  const d = toUTC(iso);
  if (!d) return '—';
  return d.toLocaleTimeString('en-IN', {
    timeZone: 'Asia/Kolkata',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  });
};

const fmtDate = (iso) => {
  if (!iso) return '—';
  const d = toUTC(iso);
  if (!d) return '—';
  return d.toLocaleString('en-IN', {
    timeZone: 'Asia/Kolkata',
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false,
  });
};

const timeAgo = (iso) => {
  if (!iso) return '—';
  const d = toUTC(iso);
  if (!d) return '—';
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return `${Math.round(diff)}s ago`;
  if (diff < 3600) return `${Math.round(diff / 60)}m ago`;
  return `${Math.round(diff / 3600)}h ago`;
};
const fmtBytes = (v) => {
  if (v == null) return '—';
  if (v < 1024) return `${v.toFixed(0)} B/s`;
  if (v < 1048576) return `${(v / 1024).toFixed(1)} KB/s`;
  return `${(v / 1048576).toFixed(1)} MB/s`;
};
const fmtVal = (v, unit) => {
  if (v == null) return '—';
  if (unit === 'B/s') return fmtBytes(v);
  if (unit === '%') return `${v.toFixed(1)}%`;
  if (unit === 'GB') return `${v.toFixed(1)} GB`;
  if (unit === 'users') return `${Math.round(v)} users`;
  return typeof v === 'number' ? (Number.isInteger(v) ? `${v}` : v.toFixed(2)) : String(v);
};

const severityColor = (s) => {
  const m = { Critical: '#fda4af', High: '#fbbf24', Medium: '#fef08a', Low: '#d9f99d', Normal: '#86efac' };
  return m[s] || '#86efac';
};

const STATUS_COLORS = {
  online: '#86efac',
  warning: '#fde68a',
  critical: '#fda4af',
  warmup: '#c4b5fd',
  offline: '#94a3b8',
  unknown: '#94a3b8',
};

const nodeStatusColor = (node) => {
  if (node.active_anomalies > 0) return severityColor(node.latest_anomaly?.severity);
  return STATUS_COLORS[node.status] || '#94a3b8';
};

const latestVal = (node, metricKey) => {
  const m = node.latest_metrics?.find(x => x.metric_name === metricKey);
  return m ? m.value : null;
};

/* ─────────────────────── GaugeBar ─────────────────────── */
function GaugeBar({ value, max = 100, color = '#00d4ff', width = '100%' }) {
  const pct = Math.min(100, Math.max(0, (value / max) * 100));
  return (
    <div style={{ width, height: 4, background: 'rgba(255,255,255,0.08)', borderRadius: 2, overflow: 'hidden' }}>
      <div style={{ width: `${pct}%`, height: '100%', background: color, borderRadius: 2, transition: 'width 0.4s ease' }} />
    </div>
  );
}

/* ─────────────────────── Sparkline ─────────────────────── */
function Sparkline({ data = [], color = '#00d4ff', height = 36 }) {
  if (!data || data.length < 2) {
    return <div style={{ height, display: 'flex', alignItems: 'center', color: '#333', fontSize: 10 }}>No data</div>;
  }
  const pts = data.map((d, i) => ({ i, v: typeof d === 'object' ? d.value : d }));
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={pts} margin={{ top: 1, right: 0, left: 0, bottom: 1 }}>
        <defs>
          <linearGradient id={`sg-${color.replace('#', '')}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor={color} stopOpacity={0.35} />
            <stop offset="95%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <Area type="monotone" dataKey="v" stroke={color} strokeWidth={1.5}
          fill={`url(#sg-${color.replace('#', '')})`} dot={false} isAnimationActive={false} />
      </AreaChart>
    </ResponsiveContainer>
  );
}

/* ─────────────────────── Stat Pill ─────────────────────── */
function StatPill({ label, value, color = '#00d4ff', icon }) {
  return (
    <div style={{
      background: 'rgba(255,255,255,0.92)', border: '1px solid rgba(148, 163, 184, 0.22)',
      borderRadius: 12, padding: '10px 18px', minWidth: 110, flex: '1 1 110px',
      display: 'flex', flexDirection: 'column', gap: 4, backdropFilter: 'blur(8px)',
    }}>
      <div style={{ fontSize: 11, color: '#888', letterSpacing: 1, textTransform: 'uppercase' }}>{icon} {label}</div>
      <div style={{ fontSize: 24, fontWeight: 700, color, lineHeight: 1 }}>{value ?? '—'}</div>
    </div>
  );
}

/* ─────────────────────── Cluster Aggregate Bar ─────────────────────── */
function ClusterAggBar({ summary }) {
  if (!summary) return null;
  const pills = [
    { label: 'Avg CPU', value: summary.avg_cpu != null ? `${summary.avg_cpu.toFixed(1)}%` : '—', color: '#00d4ff' },
    { label: 'Avg MEM', value: summary.avg_mem != null ? `${summary.avg_mem.toFixed(1)}%` : '—', color: '#9b8fff' },
    { label: 'Load 1m', value: summary.avg_load_1 != null ? summary.avg_load_1.toFixed(2) : '—', color: '#4ade80' },
    { label: 'Load 5m', value: summary.avg_load_5 != null ? summary.avg_load_5.toFixed(2) : '—', color: '#a3e635' },
    { label: 'Load 15m', value: summary.avg_load_15 != null ? summary.avg_load_15.toFixed(2) : '—', color: '#22d3ee' },
    { label: 'Avg Disk', value: summary.avg_disk != null ? `${summary.avg_disk.toFixed(1)}%` : '—', color: '#f7e25a' },
    { label: 'Net RX', value: fmtBytes(summary.total_net_rx), color: '#ffb347' },
    { label: 'Net TX', value: fmtBytes(summary.total_net_tx), color: '#ff6b35' },
  ];
  return (
    <div style={{
      background: 'rgba(224, 242, 254, 0.8)', border: '1px solid rgba(148, 163, 184, 0.18)',
      borderRadius: 14, padding: '12px 18px', display: 'flex', gap: 14, flexWrap: 'wrap',
      alignItems: 'center',
    }}>
      <div style={{ fontSize: 11, color: '#00d4ff', fontWeight: 700, letterSpacing: 1, marginRight: 4 }}>
        📡 CLUSTER AVG
      </div>
      {pills.map(p => (
        <div key={p.label} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 1 }}>
          <div style={{ fontSize: 10, color: '#555', letterSpacing: 0.5 }}>{p.label}</div>
          <div style={{ fontSize: 15, fontWeight: 700, color: p.color }}>{p.value}</div>
        </div>
      ))}
    </div>
  );
}

/* ─────────────────────── Cluster Overview Chart ─────────────────────── */
function ClusterOverviewChart({ rawHistory, allNodes, metricLabel, metricUnit }) {
  const { chartData, hostnames } = useMemo(() => {
    if (!rawHistory || rawHistory.length === 0) return { chartData: [], hostnames: [] };
    const hosts = [...new Set(rawHistory.map(r => r.hostname))].sort();
    const timeMap = new Map();
    for (const row of rawHistory) {
      let t;
      if (row.timestamp && row.timestamp.endsWith('00:00:00')) {
        t = row.timestamp.substring(5, 16);
      } else {
        const istDate = toIST(row.timestamp);
        t = istDate.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: false }).split(' ')[0] ||
          istDate.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      }
      if (!timeMap.has(t)) timeMap.set(t, { time: t });
      if (timeMap.get(t)[row.hostname] === undefined) {
        timeMap.get(t)[row.hostname] = parseFloat(row.value.toFixed(2));
      }
    }
    const data = Array.from(timeMap.values()).sort((a, b) => a.time.localeCompare(b.time));
    return { chartData: data, hostnames: hosts };
  }, [rawHistory]);

  if (chartData.length === 0) {
    return (
      <div style={{ height: 160, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#444', fontSize: 13 }}>
        No history data yet — waiting for first collection cycle…
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={180}>
      <AreaChart data={chartData} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
        <defs>
          {hostnames.map((h, i) => (
            <linearGradient key={h} id={`cg-${i}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor={NODE_COLORS[i % NODE_COLORS.length]} stopOpacity={0.5} />
              <stop offset="95%" stopColor={NODE_COLORS[i % NODE_COLORS.length]} stopOpacity={0.05} />
            </linearGradient>
          ))}
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(148, 163, 184, 0.25)" />
        <XAxis dataKey="time" tick={{ fontSize: 10, fill: '#475569' }} tickLine={false} />
        <YAxis tick={{ fontSize: 10, fill: '#475569' }} tickLine={false} axisLine={false}
          tickFormatter={v => metricUnit === 'B/s' ? fmtBytes(v) : `${v}${metricUnit}`} width={48} />
        <ReTooltip contentStyle={{ background: '#ffffff', border: '1px solid rgba(148, 163, 184, 0.2)', borderRadius: 8, fontSize: 11 }} />
        <Legend wrapperStyle={{ fontSize: 11, paddingTop: 8 }} />
        {hostnames.map((h, i) => (
          <Area key={h} type="monotone" dataKey={h} stackId="1"
            stroke={NODE_COLORS[i % NODE_COLORS.length]} strokeWidth={1.5}
            fill={`url(#cg-${i})`} dot={false} isAnimationActive={false} />
        ))}
      </AreaChart>
    </ResponsiveContainer>
  );
}

/* ─────────────────────── Node Card ─────────────────────── */
function NodeCard({ node, onClick }) {
  const borderColor = nodeStatusColor(node);
  const cpu = latestVal(node, 'cpu_used_pct');
  const mem = latestVal(node, 'memory_used_pct');
  const load = latestVal(node, 'load_one');
  const disk = latestVal(node, 'disk_used_pct');

  return (
    <div onClick={onClick} style={{
      background: 'rgba(236, 244, 255, 0.14)', border: `1.5px solid ${borderColor}20`,
      borderLeft: `3px solid ${borderColor}`, borderRadius: 14,
      padding: '14px 16px', cursor: 'pointer', transition: 'all 0.2s',
      display: 'flex', flexDirection: 'column', gap: 10,
    }}
      onMouseEnter={e => e.currentTarget.style.background = 'rgba(236, 244, 255, 0.22)'}
      onMouseLeave={e => e.currentTarget.style.background = 'rgba(236, 244, 255, 0.14)'}
    >
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <div style={{ fontWeight: 700, fontSize: 13, color: '#0f172a', letterSpacing: 0.3 }}>{node.hostname}</div>
          <div style={{ fontSize: 10, color: '#475569', fontFamily: 'monospace' }}>{node.ip_address}</div>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 3 }}>
          <div style={{
            fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 99,
            background: `${borderColor}20`, color: borderColor,
            letterSpacing: 0.5, textTransform: 'uppercase',
          }}>{node.status || 'unknown'}</div>
          {node.status === 'warmup' && node.warmup_ends_at && (
            <div style={{ fontSize: 9, color: '#a855f7' }}>
              ⏳ ends {timeAgo(node.warmup_ends_at)}
            </div>
          )}
          {node.active_users > 0 && (
            <div style={{ fontSize: 10, color: '#475569', display: 'flex', alignItems: 'center', gap: 3 }}>
              👤 <span style={{ fontWeight: 700 }}>{node.active_users}</span>
              <span style={{ color: '#666' }}>user{node.active_users > 1 ? 's' : ''}</span>
            </div>
          )}
          {node.running_procs > 0 && (
            <div style={{ fontSize: 10, color: '#34d399', display: 'flex', alignItems: 'center', gap: 3 }}>
              ⚙️ <span style={{ fontWeight: 700 }}>{node.running_procs}</span>
              <span style={{ color: '#666' }}>running</span>
            </div>
          )}
        </div>
      </div>

      {/* Key metrics */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
        {[
          { label: 'CPU', value: cpu, unit: '%', color: '#00d4ff' },
          { label: 'MEM', value: mem, unit: '%', color: '#9b8fff' },
          { label: 'Load', value: load, unit: '', color: '#4ade80' },
          { label: 'Disk', value: disk, unit: '%', color: '#f7e25a' },
        ].map(({ label, value, unit, color }) => (
          <div key={label}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 2 }}>
              <span style={{ fontSize: 10, color: '#666' }}>{label}</span>
              <span style={{ fontSize: 11, fontWeight: 600, color }}>
                {value != null ? `${value.toFixed(1)}${unit}` : '—'}
              </span>
            </div>
            {unit === '%' && value != null && <GaugeBar value={value} color={color} />}
          </div>
        ))}
      </div>

      {/* Sparklines */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 6 }}>
        {[
          { k: 'cpu_used_pct', c: '#00d4ff', label: 'CPU' },
          { k: 'memory_used_pct', c: '#9b8fff', label: 'MEM' },
          { k: 'load_one', c: '#4ade80', label: 'LD' },
        ].map(({ k, c, label }) => (
          <div key={k}>
            <div style={{ fontSize: 9, color: '#444', marginBottom: 1 }}>{label}</div>
            <Sparkline data={node.sparklines?.[k] || []} color={c} height={28} />
          </div>
        ))}
      </div>

      {node.active_anomalies > 0 && (
        <div style={{
          fontSize: 10, color: severityColor(node.latest_anomaly?.severity),
          background: `${severityColor(node.latest_anomaly?.severity)}15`,
          borderRadius: 6, padding: '3px 8px',
        }}>
          🚨 {node.active_anomalies} active anomal{node.active_anomalies > 1 ? 'ies' : 'y'}
          {node.latest_anomaly && ` — ${node.latest_anomaly.description?.substring(0, 50)}`}
        </div>
      )}
    </div>
  );
}

/* ─────────────────────── Grid View ─────────────────────── */
function GridView({ nodes }) {
  if (!nodes || nodes.length === 0) return (
    <div style={{ color: '#555', textAlign: 'center', padding: 40 }}>No nodes to display</div>
  );

  const thStyle = {
    padding: '6px 10px', fontSize: 10, fontWeight: 700, color: '#475569',
    letterSpacing: 0.8, textTransform: 'uppercase', textAlign: 'center',
    borderBottom: '1px solid rgba(148, 163, 184, 0.18)',
    position: 'sticky', top: 0, background: 'rgba(248, 250, 252, 0.98)', zIndex: 2,
  };
  const tdStyle = {
    padding: '4px 6px', borderBottom: '1px solid rgba(148, 163, 184, 0.12)',
    verticalAlign: 'middle',
  };

  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
        <thead>
          <tr>
            <th style={{ ...thStyle, textAlign: 'left', minWidth: 130, position: 'sticky', left: 0, zIndex: 3, background: '#f8fafc' }}>
              Host
            </th>
            {GRID_METRICS.map(m => (
              <th key={m.key} style={{ ...thStyle, minWidth: 90 }}>
                <span style={{ color: m.color }}>{m.label}</span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {nodes.map((node, ni) => {
            const bc = STATUS_COLORS[node.status] || '#6c757d';
            return (
              <tr key={node.id} style={{ background: ni % 2 === 0 ? 'rgba(248, 250, 252, 0.7)' : 'transparent' }}>
                {/* Node label — sticky */}
                <td style={{
                  ...tdStyle, position: 'sticky', left: 0,
                  background: ni % 2 === 0 ? 'rgba(248, 250, 252, 0.98)' : 'rgba(255, 255, 255, 0.98)', zIndex: 1,
                  borderLeft: `3px solid ${bc}`,
                }}>
                  <div style={{ fontWeight: 700, color: '#0f172a' }}>{node.hostname}</div>
                  <div style={{
                    display: 'inline-block', fontSize: 9, fontWeight: 700,
                    padding: '1px 5px', borderRadius: 99, background: `${bc}20`, color: bc,
                    marginTop: 2, textTransform: 'uppercase',
                  }}>{node.status}</div>
                </td>
                {/* One cell per metric */}
                {GRID_METRICS.map(m => {
                  const val = latestVal(node, m.key);
                  const sparkData = node.sparklines?.[m.key] || [];
                  return (
                    <td key={m.key} style={{ ...tdStyle, textAlign: 'center' }}>
                      <div style={{ fontSize: 12, fontWeight: 700, color: m.color, marginBottom: 2 }}>
                        {fmtVal(val, m.unit)}
                      </div>
                      <Sparkline data={sparkData} color={m.color} height={24} />
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/* ─────────────────────── Metrics Table ─────────────────────── */
function MetricsTable({ nodes, onExport }) {
  const [sortKey, setSortKey] = useState('hostname');
  const [sortDir, setSortDir] = useState('asc');

  const cols = [
    { key: 'hostname', label: 'Node IP', w: 140 },
    { key: 'status', label: 'Status', w: 90 },
    { key: 'cpu_used_pct', label: 'CPU %', w: 80, metric: true, unit: '%' },
    { key: 'memory_used_pct', label: 'MEM %', w: 80, metric: true, unit: '%' },
    { key: 'load_one', label: 'Load', w: 70, metric: true, unit: '' },
    { key: 'disk_used_pct', label: 'Disk %', w: 80, metric: true, unit: '%' },
    { key: 'net_rx_bytes', label: 'Net RX', w: 90, metric: true, unit: 'B/s' },
    { key: 'net_tx_bytes', label: 'Net TX', w: 90, metric: true, unit: 'B/s' },
    { key: 'procs_running', label: 'Procs', w: 70, metric: true, unit: '' },
    { key: 'active_anomalies', label: 'Anomalies', w: 80 },
    { key: 'active_users', label: 'Users', w: 60 },
  ];

  const getVal = (node, col) => {
    if (col.metric) return latestVal(node, col.key);
    return node[col.key];
  };

  const sorted = useMemo(() => {
    return [...nodes].sort((a, b) => {
      const av = getVal(a, cols.find(c => c.key === sortKey) || {});
      const bv = getVal(b, cols.find(c => c.key === sortKey) || {});
      const cmp = typeof av === 'number' && typeof bv === 'number'
        ? av - bv
        : String(av ?? '').localeCompare(String(bv ?? ''));
      return sortDir === 'asc' ? cmp : -cmp;
    });
  }, [nodes, sortKey, sortDir]);

  const toggleSort = (key) => {
    if (sortKey === key) setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    else { setSortKey(key); setSortDir('asc'); }
  };

  const thS = {
    padding: '8px 10px', fontSize: 10, fontWeight: 700, color: '#666',
    letterSpacing: 0.8, textTransform: 'uppercase', cursor: 'pointer', userSelect: 'none',
    borderBottom: '1px solid rgba(255,255,255,0.08)', whiteSpace: 'nowrap',
    transition: 'color 0.15s',
  };
  const tdS = {
    padding: '7px 10px', fontSize: 12, borderBottom: '1px solid rgba(255,255,255,0.04)',
    whiteSpace: 'nowrap',
  };

  // Export as CSV (client-side)
  const exportCSV = () => {
    const headers = cols.map(c => c.label).join(',');
    const rows = sorted.map(node =>
      cols.map(c => {
        const v = getVal(node, c);
        return v != null ? (c.unit ? fmtVal(v, c.unit) : String(v)) : '';
      }).join(',')
    ).join('\n');
    const blob = new Blob([headers + '\n' + rows], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `cluster_metrics_${new Date().toISOString().slice(0, 19).replace(/:/g, '-')}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const exportJSON = () => {
    const data = sorted.map(node => {
      const obj = { hostname: node.hostname, status: node.status };
      cols.filter(c => c.metric).forEach(c => { obj[c.key] = getVal(node, c); });
      obj.active_anomalies = node.active_anomalies;
      obj.active_users = node.active_users;
      return obj;
    });
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `cluster_metrics_${new Date().toISOString().slice(0, 19).replace(/:/g, '-')}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div>
      {/* Toolbar */}
      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginBottom: 10 }}>
        <button onClick={exportCSV} style={btnStyle('#4ade80')}>⬇ CSV</button>
        <button onClick={exportJSON} style={btnStyle('#00d4ff')}>⬇ JSON</button>
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
          <thead>
            <tr>
              {cols.map(c => (
                <th key={c.key} style={{ ...thS, color: sortKey === c.key ? '#00d4ff' : '#666' }}
                  onClick={() => toggleSort(c.key)}>
                  {c.label} {sortKey === c.key ? (sortDir === 'asc' ? '↑' : '↓') : ''}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map((node, ni) => {
              const bc = STATUS_COLORS[node.status] || '#6c757d';
              return (
                <tr key={node.id} style={{ background: ni % 2 === 0 ? 'rgba(15, 23, 42, 0.03)' : 'transparent' }}>
                  {cols.map(c => {
                    const v = getVal(node, c);
                    if (c.key === 'hostname') return (
                      <td key={c.key} style={{ ...tdS, borderLeft: `3px solid ${bc}`, fontWeight: 700, color: '#0f172a' }}>{v}</td>
                    );
                    if (c.key === 'status') return (
                      <td key={c.key} style={tdS}>
                        <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 7px', borderRadius: 99, background: `${bc}20`, color: bc, textTransform: 'uppercase' }}>{v}</span>
                      </td>
                    );
                    if (c.key === 'active_anomalies') return (
                      <td key={c.key} style={{ ...tdS, color: v > 0 ? '#ff4757' : '#4ade80', fontWeight: 700 }}>{v ?? 0}</td>
                    );
                    if (c.metric && c.unit === '%' && v != null) return (
                      <td key={c.key} style={tdS}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                          <span style={{ color: v > 90 ? '#ff4757' : v > 70 ? '#ffb347' : '#0f172a', fontWeight: v > 70 ? 700 : 400 }}>
                            {v.toFixed(1)}%
                          </span>
                          <div style={{ flex: 1, minWidth: 30 }}>
                            <GaugeBar value={v} color={v > 90 ? '#ff4757' : v > 70 ? '#ffb347' : '#00d4ff'} />
                          </div>
                        </div>
                      </td>
                    );
                    return <td key={c.key} style={{ ...tdS, color: '#aaa' }}>{v != null ? fmtVal(v, c.unit) : '—'}</td>;
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

const btnStyle = (color) => ({
  background: `${color}15`, border: `1px solid ${color}50`, color,
  borderRadius: 7, padding: '5px 12px', cursor: 'pointer', fontSize: 11, fontWeight: 600,
});

/* ─────────────────────── Node Drilldown Modal ─────────────────────── */
function NodeModal({ node, onClose, onResolveAnomaly }) {
  const [tab, setTab] = useState('overview');
  const [anomalies, setAnomalies] = useState([]);
  const [health, setHealth] = useState(null);
  const [baselines, setBaselines] = useState([]);
  const [metrics, setMetrics] = useState([]);
  const [users, setUsers] = useState([]);
  const [processes, setProcesses] = useState([]);
  const [processesTotal, setProcessesTotal] = useState(0);

  useEffect(() => {
    if (!node) return;
    axios.get(`${API}/api/cluster/node/${node.id}/anomalies?limit=20`)
      .then(r => setAnomalies(r.data)).catch(() => { });
    axios.get(`${API}/api/cluster/node/${node.id}/health`)
      .then(r => setHealth(r.data)).catch(() => { });
    axios.get(`${API}/api/cluster/node/${node.id}/baselines`)
      .then(r => setBaselines(r.data)).catch(() => { });
    axios.get(`${API}/api/cluster/node/${node.id}/metrics?limit=200`)
      .then(r => setMetrics(r.data)).catch(() => { });
    axios.get(`${API}/api/cluster/node/${node.id}/users`)
      .then(r => setUsers(r.data)).catch(() => { });
    axios.get(`${API}/api/cluster/node/${node.id}/processes`)
      .then(r => {
        // API now returns { total, processes }
        if (r.data && Array.isArray(r.data.processes)) {
          setProcesses(r.data.processes);
          setProcessesTotal(r.data.total || r.data.processes.length || 0);
        } else if (Array.isArray(r.data)) {
          // fallback for older backend
          setProcesses(r.data);
          setProcessesTotal(r.data.length);
        } else {
          setProcesses([]);
          setProcessesTotal(0);
        }
      }).catch(() => { setProcesses([]); setProcessesTotal(0); });
  }, [node]);

  const metricRows = useMemo(() => {
    const grouped = {};
    for (const m of metrics) {
      if (!grouped[m.metric_name]) grouped[m.metric_name] = [];
      grouped[m.metric_name].push(m);
    }
    return Object.entries(grouped).map(([metric_name, rows]) => {
      const sorted = [...rows].sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));
      const latest = sorted[sorted.length - 1];
      return {
        metric_name,
        latest_value: latest?.value,
        latest_ts: latest?.timestamp,
        history: sorted.map(r => ({ value: r.value, timestamp: r.timestamp })),
      };
    }).sort((a, b) => a.metric_name.localeCompare(b.metric_name));
  }, [metrics]);

  if (!node) return null;
  const borderColor = nodeStatusColor(node);

  const tabs = [
    { key: 'overview', label: '📊 Overview' },
    { key: 'metrics', label: '📈 Metrics' },
    { key: 'users', label: '👥 Users' },
    { key: 'processes', label: '⚙️ Processes' },
    { key: 'anomalies', label: `🚨 Anomalies (${node.active_anomalies})` },
    { key: 'health', label: '🔬 Health' },
  ];

  const tabStyle = (active) => ({
    padding: '6px 14px', borderRadius: 8, cursor: 'pointer', fontSize: 12, fontWeight: 600,
    background: active ? 'rgba(0,212,255,0.15)' : 'transparent',
    color: active ? '#00d4ff' : '#666', border: 'none',
    transition: 'all 0.15s',
  });

  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(248, 250, 252, 0.95)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
      backdropFilter: 'blur(6px)', padding: 20,
    }} onClick={onClose}>
      <div style={{
        background: '#ffffff', border: `1px solid ${borderColor}20`,
        borderRadius: 20, width: '100%', maxWidth: 760, maxHeight: '90vh', overflowY: 'auto',
        padding: '24px 28px', boxShadow: `0 0 40px rgba(15, 23, 42, 0.08)`,
      }} onClick={e => e.stopPropagation()}>

        {/* Modal header */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 18 }}>
          <div>
            <div style={{ fontWeight: 800, fontSize: 18, color: '#0f172a' }}>{node.hostname}</div>
            <div style={{ fontSize: 12, color: '#475569' }}>{node.ip_address}</div>
            {/* OS + architecture + uptime */}
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 5 }}>
              {node.os_version && (
                <span style={{ fontSize: 10, background: 'rgba(0,212,255,0.1)', color: '#00d4ff', padding: '2px 8px', borderRadius: 6, fontWeight: 600 }}>
                  🐧 {node.os_version}
                </span>
              )}
              {node.architecture && (
                <span style={{ fontSize: 10, background: 'rgba(155,143,255,0.1)', color: '#9b8fff', padding: '2px 8px', borderRadius: 6, fontWeight: 600 }}>
                  ⚙️ {node.architecture}
                </span>
              )}
              {node.boot_time && (() => {
                const upSecs = Math.floor((Date.now() - new Date(node.boot_time + 'Z').getTime()) / 1000);
                const d = Math.floor(upSecs / 86400);
                const h = Math.floor((upSecs % 86400) / 3600);
                const m = Math.floor((upSecs % 3600) / 60);
                const uptimeStr = d > 0 ? `${d}d ${h}h ${m}m` : h > 0 ? `${h}h ${m}m` : `${m}m`;
                return (
                  <span style={{ fontSize: 10, background: 'rgba(74,222,128,0.1)', color: '#4ade80', padding: '2px 8px', borderRadius: 6, fontWeight: 600 }}>
                    ⏱ Up {uptimeStr}
                  </span>
                );
              })()}
            </div>
            {node.status === 'warmup' && (
              <div style={{ fontSize: 11, color: '#a855f7', marginTop: 4 }}>
                ⏳ Warmup mode — anomaly detection activates after {node.warmup_ends_at ? fmtTime(node.warmup_ends_at) : '…'}
              </div>
            )}
          </div>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            <div style={{
              fontSize: 12, fontWeight: 700, padding: '4px 12px', borderRadius: 99,
              background: `${borderColor}20`, color: borderColor, textTransform: 'uppercase',
            }}>{node.status}</div>
            <button onClick={onClose} style={{
              background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.1)',
              color: '#888', borderRadius: 8, padding: '5px 10px', cursor: 'pointer', fontSize: 13,
            }}>✕</button>
          </div>
        </div>

        {/* Tabs */}
        <div style={{ display: 'flex', gap: 6, marginBottom: 18 }}>
          {tabs.map(t => (
            <button key={t.key} style={tabStyle(tab === t.key)} onClick={() => setTab(t.key)}>
              {t.label}
            </button>
          ))}
        </div>

        {/* Overview tab */}
        {tab === 'overview' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            {/* Stat pills */}
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10 }}>
              {[
                { label: 'Active Users', value: node.active_users ?? 0, color: '#9b8fff', icon: '👤' },
                { label: 'Procs Running', value: node.running_procs ?? 0, color: '#4ade80', icon: '⚙️' },
                { label: 'Anomalies', value: node.active_anomalies, color: node.active_anomalies > 0 ? '#ff4757' : '#4ade80', icon: '🚨' },
                { label: 'CPU Used', value: latestVal(node, 'cpu_used_pct') != null ? `${latestVal(node, 'cpu_used_pct').toFixed(1)}%` : '—', color: '#00d4ff', icon: '🔲' },
                { label: 'Mem Used', value: latestVal(node, 'memory_used_pct') != null ? `${latestVal(node, 'memory_used_pct').toFixed(1)}%` : '—', color: '#9b8fff', icon: '💾' },
                { label: 'Load (1m)', value: latestVal(node, 'load_one') != null ? latestVal(node, 'load_one').toFixed(2) : '—', color: '#4ade80', icon: '📈' },
                { label: 'Net RX', value: latestVal(node, 'net_rx_bytes') != null ? fmtBytes(latestVal(node, 'net_rx_bytes')) : '—', color: '#ffb347', icon: '📡' },
                { label: 'Disk Used', value: latestVal(node, 'disk_used_pct') != null ? `${latestVal(node, 'disk_used_pct').toFixed(1)}%` : '—', color: '#f7e25a', icon: '💿' },
              ].map(p => <StatPill key={p.label} {...p} />)}
            </div>

            {/* Sparklines for every metric */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: 12 }}>
              {GRID_METRICS.map(m => (
                <div key={m.key} style={{
                  background: 'rgba(255,255,255,0.03)', borderRadius: 10, padding: '8px 10px',
                }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                    <span style={{ fontSize: 10, color: '#666' }}>{m.label}</span>
                    <span style={{ fontSize: 11, fontWeight: 700, color: m.color }}>
                      {fmtVal(latestVal(node, m.key), m.unit)}
                    </span>
                  </div>
                  <Sparkline data={node.sparklines?.[m.key] || []} color={m.color} height={36} />
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Metrics tab */}
        {tab === 'metrics' && (
          <div style={{ display: 'grid', gap: 14 }}>
            {METRIC_GROUPS.map(group => (
              <div key={group.label} style={{ background: 'rgba(255,255,255,0.03)', borderRadius: 14, padding: 14 }}>
                <div style={{ fontWeight: 700, fontSize: 13, color: '#0f172a', marginBottom: 10 }}>{group.label}</div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))', gap: 10 }}>
                  {group.keys.map(metricKey => {
                    const row = metricRows.find(r => r.metric_name === metricKey);
                    const def = Object.values(METRICS).find(m => m.key === metricKey);
                    return (
                      <div key={metricKey} style={{ background: 'rgba(255,255,255,0.95)', border: '1px solid rgba(148, 163, 184, 0.18)', borderRadius: 12, padding: 10 }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                          <span style={{ fontSize: 11, color: '#475569' }}>{def ? Object.keys(METRICS).find(k => METRICS[k].key === metricKey) : metricKey.replace(/_/g, ' ')}</span>
                          <span style={{ fontSize: 11, color: def?.color || '#0f172a' }}>{fmtVal(row?.latest_value, def?.unit)}</span>
                        </div>
                        <Sparkline data={row?.history || []} color={def?.color || '#00d4ff'} height={36} />
                        <div style={{ marginTop: 8, fontSize: 10, color: '#555' }}>
                          Latest: {fmtDate(row?.latest_ts)}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Users tab */}
        {tab === 'users' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {users.length === 0 ? (
              <div style={{ color: '#555', padding: 20, textAlign: 'center' }}>
                No active user sessions detected
              </div>
            ) : (
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
                  <thead>
                    <tr>
                      {['username', 'terminal', 'remote_host', 'login_time', 'collected_at'].map(col => (
                        <th key={col} style={{ textAlign: 'left', padding: '8px 10px', fontSize: 10, color: '#666', textTransform: 'uppercase', borderBottom: '1px solid rgba(255,255,255,0.08)' }}>
                          {col.replace('_', ' ')}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {users.map(u => (
                      <tr key={u.id} style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                        <td style={{ padding: '8px 10px' }}>{u.username}</td>
                        <td style={{ padding: '8px 10px' }}>{u.terminal || '—'}</td>
                        <td style={{ padding: '8px 10px' }}>{u.remote_host || 'local'}</td>
                        <td style={{ padding: '8px 10px' }}>{fmtDate(u.login_time)}</td>
                        <td style={{ padding: '8px 10px' }}>{fmtDate(u.collected_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {/* Processes tab */}
        {tab === 'processes' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            {processes.length === 0 ? (
              <div style={{ color: '#555', padding: 20, textAlign: 'center' }}>
                No process data available
              </div>
            ) : (
              <>
                {/* Top 5 Processes Card */}
                <div style={{ background: 'rgba(255,198,0,0.08)', border: '1px solid rgba(255,198,0,0.3)', borderRadius: 12, padding: 14 }}>
                  <div style={{ fontSize: 12, fontWeight: 700, color: '#ffb800', marginBottom: 10, display: 'flex', alignItems: 'center', gap: 6 }}>
                    🔥 Top 5 Resource-Intensive Processes
                  </div>
                  <div style={{ display: 'grid', gap: 8 }}>
                    {processes.slice(0, 5).map((p, idx) => (
                      <div key={p.id} style={{
                        background: 'rgba(255,255,255,0.95)', borderRadius: 8, padding: '10px 12px',
                        border: `1px solid rgba(255,184,0,0.2)`, display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                        borderLeft: `3px solid #ffb800`,
                      }}>
                        <div style={{ flex: 1 }}>
                          <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', marginBottom: 4 }}>
                            <span style={{ fontWeight: 700, fontSize: 11, background: '#ffb80020', color: '#ffb800', padding: '1px 6px', borderRadius: 4 }}>#{idx + 1}</span>
                            <span style={{ fontWeight: 600, fontSize: 11, color: '#0f172a' }}>{p.command || 'unknown'}</span>
                            <span style={{ fontSize: 9, color: '#999', fontFamily: 'monospace' }}>PID: {p.pid}</span>
                          </div>
                          <div style={{ display: 'flex', gap: 12, fontSize: 10, color: '#666' }}>
                            <span>👤 {p.username || '—'}</span>
                            <span>📊 CPU: <span style={{ color: '#ff6b6b', fontWeight: 600 }}>{p.cpu_pct?.toFixed(1)}%</span></span>
                            <span>💾 MEM: <span style={{ color: '#9b8fff', fontWeight: 600 }}>{p.mem_pct?.toFixed(1)}%</span></span>
                            <span>🔄 {p.status || '—'}</span>
                          </div>
                        </div>
                        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                          <div style={{ textAlign: 'right', fontSize: 9, color: '#999' }}>
                            <div>{fmtTime(p.collected_at)}</div>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>

                {/* All Processes Table */}
                <div>
                  <div style={{ fontSize: 12, fontWeight: 700, color: '#0f172a', marginBottom: 8 }}>
                    📋 All Running Processes ({processesTotal} total)
                  </div>
                  <div style={{ overflowX: 'auto' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
                      <thead>
                        <tr>
                          {['#', 'PID', 'User', 'CPU %', 'MEM %', 'Status', 'Command', 'Time'].map(col => (
                            <th key={col} style={{ textAlign: 'left', padding: '8px 10px', fontSize: 9, color: '#666', fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.5, borderBottom: '1px solid rgba(148, 163, 184, 0.2)' }}>
                              {col}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {processes.map((p, idx) => {
                          const isTop5 = idx < 5;
                          const cpuColor = p.cpu_pct > 50 ? '#ff6b6b' : p.cpu_pct > 25 ? '#ffb347' : '#0f172a';
                          const memColor = p.mem_pct > 50 ? '#ff6b6b' : p.mem_pct > 25 ? '#9b8fff' : '#0f172a';
                          return (
                            <tr key={p.id} style={{
                              background: isTop5 ? 'rgba(255,184,0,0.06)' : idx % 2 === 0 ? 'rgba(15, 23, 42, 0.02)' : 'transparent',
                              borderBottom: '1px solid rgba(148, 163, 184, 0.12)',
                              borderLeft: isTop5 ? '3px solid #ffb800' : 'transparent',
                            }}>
                              <td style={{ padding: '8px 10px', fontWeight: isTop5 ? 700 : 400, color: isTop5 ? '#ffb800' : '#666' }}>
                                {isTop5 ? `⭐ ${idx + 1}` : '—'}
                              </td>
                              <td style={{ padding: '8px 10px', fontFamily: 'monospace', fontSize: 10, color: '#0f172a' }}>{p.pid}</td>
                              <td style={{ padding: '8px 10px', color: '#666' }}>{p.username || '—'}</td>
                              <td style={{ padding: '8px 10px', fontWeight: 700, color: cpuColor }}>{p.cpu_pct?.toFixed(1)}%</td>
                              <td style={{ padding: '8px 10px', fontWeight: 700, color: memColor }}>{p.mem_pct?.toFixed(1)}%</td>
                              <td style={{ padding: '8px 10px', color: '#666', fontSize: 10 }}>{p.status || '—'}</td>
                              <td style={{ padding: '8px 10px', color: '#0f172a', fontSize: 10, maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                {p.command || 'unknown'}
                              </td>
                              <td style={{ padding: '8px 10px', fontSize: 9, color: '#999' }}>{fmtTime(p.collected_at)}</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
              </>
            )}
          </div>
        )}

        {/* Anomalies tab */}
        {tab === 'anomalies' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {anomalies.length === 0 && (
              <div style={{ color: '#4ade80', padding: 20, textAlign: 'center' }}>
                ✅ No anomaly events recorded
              </div>
            )}
            {anomalies.map(ev => {
              const sc = severityColor(ev.severity);
              return (
                <div key={ev.id} style={{
                  background: `${sc}08`, border: `1px solid ${sc}25`,
                  borderRadius: 10, padding: '12px 14px',
                  opacity: ev.resolved ? 0.6 : 1,
                }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                      <span style={{ fontWeight: 700, fontSize: 12, color: sc }}>{ev.severity}</span>
                      <span style={{ fontSize: 11, color: '#888', background: 'rgba(255,255,255,0.06)', padding: '1px 7px', borderRadius: 5 }}>
                        {ev.detector}
                      </span>
                      <span style={{ fontSize: 11, color: '#555' }}>score: {ev.anomaly_score?.toFixed(3)}</span>
                    </div>
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                      <span style={{ fontSize: 11, color: '#555' }}>{fmtDate(ev.detected_at)}</span>
                      {!ev.resolved && (
                        <button onClick={() => onResolveAnomaly(node.id, ev.id)} style={{
                          ...btnStyle('#4ade80'), padding: '2px 8px', fontSize: 10,
                        }}>✓ Resolve</button>
                      )}
                    </div>
                  </div>
                  <div style={{ fontSize: 11, color: '#aaa', marginBottom: 2 }}>
                    <strong style={{ color: '#ccc' }}>{ev.metric_name?.replace(/_/g, ' ')}</strong>
                    {ev.metric_value != null && ` — ${ev.metric_value.toFixed(2)}`}
                  </div>
                  <div style={{ fontSize: 11, color: '#888' }}>{ev.description}</div>
                  {ev.resolved && (
                    <div style={{ fontSize: 10, color: '#4ade80', marginTop: 4 }}>
                      ✓ Resolved {ev.resolved_at ? fmtDate(ev.resolved_at) : ''}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {/* Health tab */}
        {tab === 'health' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            {/* Infra checks */}
            <div>
              <div style={{ fontWeight: 700, fontSize: 12, color: '#475569', marginBottom: 8, textTransform: 'uppercase', letterSpacing: 0.5 }}>🔬 Infrastructure Checks</div>
              {!health || !health.checks || health.checks.length === 0 ? (
                <div style={{ color: '#555', padding: 16, textAlign: 'center', fontSize: 12 }}>
                  No health check data yet — checks run every 30s
                </div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {health.checks.map(c => (
                    <div key={c.check_type} style={{
                      display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                      background: c.passed ? 'rgba(74,222,128,0.05)' : 'rgba(255,71,87,0.05)',
                      borderRadius: 10, padding: '12px 16px',
                      border: `1px solid ${c.passed ? '#4ade8030' : '#ff475730'}`,
                    }}>
                      <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
                        <div style={{ fontSize: 18 }}>{c.passed ? '✅' : '❌'}</div>
                        <div>
                          <div style={{ fontWeight: 700, fontSize: 13, color: '#0f172a', textTransform: 'uppercase' }}>
                            {c.check_type.replace('_', ' ')}
                          </div>
                          <div style={{ fontSize: 11, color: '#666' }}>{c.detail}</div>
                        </div>
                      </div>
                      <div style={{ fontSize: 10, color: '#555' }}>{fmtDate(c.checked_at)}</div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* ML Baselines */}
            <div>
              <div style={{ fontWeight: 700, fontSize: 12, color: '#475569', marginBottom: 8, textTransform: 'uppercase', letterSpacing: 0.5 }}>🤖 ML Anomaly Baselines</div>
              {baselines.length === 0 ? (
                <div style={{
                  color: '#888', padding: '12px 16px', fontSize: 12,
                  background: 'rgba(255,179,71,0.07)', border: '1px solid rgba(255,179,71,0.2)',
                  borderRadius: 10
                }}>
                  ⏳ No baselines yet — will be computed automatically from metric history
                </div>
              ) : (
                <div style={{ overflowX: 'auto' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
                    <thead>
                      <tr>
                        {['Metric', 'Mean', 'Std Dev', 'P95', 'P99'].map(h => (
                          <th key={h} style={{
                            textAlign: 'left', padding: '8px 10px', fontSize: 10, color: '#475569',
                            fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.5,
                            borderBottom: '1px solid rgba(148,163,184,0.2)'
                          }}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {baselines.map((b, i) => (
                        <tr key={b.metric_name} style={{ background: i % 2 === 0 ? 'rgba(248,250,252,0.8)' : 'transparent' }}>
                          <td style={{ padding: '7px 10px', fontWeight: 600, color: '#0f172a' }}>
                            {b.metric_name.replace(/_/g, ' ')}
                          </td>
                          <td style={{ padding: '7px 10px', color: '#00d4ff', fontFamily: 'monospace' }}>
                            {b.mean != null ? b.mean.toFixed(2) : '—'}
                          </td>
                          <td style={{ padding: '7px 10px', color: '#9b8fff', fontFamily: 'monospace' }}>
                            {b.std != null ? b.std.toFixed(3) : '—'}
                          </td>
                          <td style={{ padding: '7px 10px', color: '#ffb347', fontFamily: 'monospace' }}>
                            {b.p95 != null ? b.p95.toFixed(2) : '—'}
                          </td>
                          <td style={{ padding: '7px 10px', color: '#ff6b35', fontFamily: 'monospace' }}>
                            {b.p99 != null ? b.p99.toFixed(2) : '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────────────────
   Main App
   ───────────────────────────────────────────────────────────────────────── */
export default function App() {
  const [overview, setOverview] = useState(null);
  const [summary, setSummary] = useState(null);
  const [nodes, setNodes] = useState([]);
  const [feed, setFeed] = useState([]);
  const [incidents, setIncidents] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [history, setHistory] = useState([]);
  const [selectedNode, setSelectedNode] = useState(null);
  const [now, setNow] = useState(new Date());
  const [lastRefresh, setLastRefresh] = useState(null);
  const [filterStatus, setFilterStatus] = useState('all');
  const [chartMetric, setChartMetric] = useState('CPU Used %');
  const [historyHours, setHistoryHours] = useState(1);
  const [viewMode, setViewMode] = useState('cards'); // 'cards' | 'grid' | 'table'
  const [wsStatus, setWsStatus] = useState('connecting');
  const [incAlertTab, setIncAlertTab] = useState('incidents'); // 'incidents' | 'alerts'

  const currentMetricDef = METRICS[chartMetric];

  // --- WebSocket ---
  const wsRef = useRef(null);
  useEffect(() => {
    const connect = () => {
      try {
        const ws = new WebSocket(WS_URL);
        wsRef.current = ws;
        ws.onopen = () => setWsStatus('connected');
        ws.onclose = () => { setWsStatus('reconnecting'); setTimeout(connect, 5000); };
        ws.onerror = (err) => { console.error('WebSocket error', err); setWsStatus('error'); };
        ws.onmessage = (evt) => {
          try {
            const msg = JSON.parse(evt.data);
            if (msg.type === 'metrics_update') {
              setOverview(msg.payload.overview);
              setNodes(msg.payload.nodes);
              setFeed(msg.payload.feed);
              setSummary(msg.payload.summary);
              if (msg.payload.incidents) setIncidents(msg.payload.incidents);
              if (msg.payload.alerts) setAlerts(msg.payload.alerts);
              setLastRefresh(new Date());
            }
          } catch (err) {
            console.error('WebSocket message parse error', err);
          }
        };
      } catch (err) {
        console.error('WebSocket connection failed', err);
      }
    };
    connect();
    return () => { wsRef.current?.close(); };
  }, []);

  // --- HTTP polling fallback ---
  const fetchHistory = useCallback((metricKey, hours) => {
    axios.get(`${API}/api/cluster/history?metric=${metricKey}&hours=${hours}`)
      .then(r => setHistory(r.data)).catch(() => { });
  }, []);

  const fetchAll = useCallback(() => {
    axios.get(`${API}/api/cluster/overview`)
      .then(r => setOverview(r.data))
      .catch(err => console.error('Fetch overview failed', err));
    axios.get(`${API}/api/cluster/nodes`)
      .then(r => setNodes(r.data))
      .catch(err => console.error('Fetch nodes failed', err));
    axios.get(`${API}/api/cluster/anomaly-feed?limit=60`)
      .then(r => setFeed(r.data))
      .catch(err => console.error('Fetch anomaly feed failed', err));
    axios.get(`${API}/api/cluster/summary`)
      .then(r => setSummary(r.data))
      .catch(err => console.error('Fetch summary failed', err));
    axios.get(`${API}/api/cluster/incidents?limit=50`)
      .then(r => setIncidents(r.data))
      .catch(() => { });
    axios.get(`${API}/api/cluster/alerts?limit=50`)
      .then(r => setAlerts(r.data))
      .catch(() => { });
    fetchHistory(currentMetricDef.key, historyHours);
    setLastRefresh(new Date());
  }, [fetchHistory, currentMetricDef.key, historyHours]);

  useEffect(() => {
    fetchAll();
    const iv1 = setInterval(fetchAll, 30000);
    const iv2 = setInterval(() => setNow(new Date()), 1000);
    return () => { clearInterval(iv1); clearInterval(iv2); };
  }, [fetchAll]);

  useEffect(() => {
    fetchHistory(currentMetricDef.key, historyHours);
  }, [chartMetric, historyHours, fetchHistory, currentMetricDef.key]);

  const filtered = nodes.filter(n => {
    if (filterStatus === 'all') return true;
    if (filterStatus === 'anomaly') return n.active_anomalies > 0;
    return n.status === filterStatus;
  });

  const handleResolveAnomaly = async (nodeId, anomalyId) => {
    try {
      await axios.post(`${API}/api/cluster/node/${nodeId}/anomalies/${anomalyId}/resolve`);
      fetchAll();
      if (selectedNode?.id === nodeId) {
        const updated = nodes.find(n => n.id === nodeId);
        if (updated) setSelectedNode(updated);
      }
    } catch { }
  };

  const statusFilters = [
    { key: 'all', label: 'All Nodes' },
    { key: 'online', label: '🟢 Online' },
    { key: 'anomaly', label: '🔴 Anomaly' },
    { key: 'warning', label: '🟡 Warning' },
    { key: 'warmup', label: '🟣 Warmup' },
    { key: 'critical', label: '🔴 Critical' },
    { key: 'offline', label: '⚫ Offline' },
  ];

  const wsColor = wsStatus === 'connected' ? '#4ade80' : wsStatus === 'reconnecting' ? '#ffb347' : '#ff4757';

  return (
    <div style={{
      minHeight: '100vh', background: '#f8fafc',
      fontFamily: "'Inter', -apple-system, BlinkMacSystemFont, sans-serif",
      color: '#0f172a',
    }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap');
        * { box-sizing: border-box; margin: 0; padding: 0; }
        ::-webkit-scrollbar { width: 6px; height: 6px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: rgba(148, 163, 184, 0.35); border-radius: 3px; }
        @keyframes pulse {
          0%   { box-shadow: 0 0 0 0 currentColor; opacity: 1; }
          70%  { box-shadow: 0 0 0 8px transparent; opacity: 0.4; }
          100% { box-shadow: 0 0 0 0 transparent; opacity: 1; }
        }
        @keyframes warmup-pulse {
          0%, 100% { opacity: 1; }
          50%       { opacity: 0.5; }
        }
        select { appearance: none; -webkit-appearance: none; }
        button:focus { outline: none; }
      `}</style>

      {/* ── Top Bar ── */}
      <div style={{
        position: 'sticky', top: 0, zIndex: 100,
        background: 'rgba(255,255,255,0.95)', borderBottom: '1px solid rgba(148, 163, 184, 0.25)',
        backdropFilter: 'blur(20px)', padding: '12px 28px',
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          <div style={{ fontSize: 20 }}>🖥️</div>
          <div>
            <div style={{ fontWeight: 800, fontSize: 15, color: '#0f172a', letterSpacing: 0.5 }}>
              ONGC AI Cluster Monitor
            </div>
            <div style={{ fontSize: 10, color: '#444', letterSpacing: 1 }}>
              HPC DASHBOARD
            </div>
          </div>
          {/* WS indicator */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 10 }}>
            <div style={{ width: 6, height: 6, borderRadius: '50%', background: wsColor, animation: wsStatus === 'connected' ? 'none' : 'pulse 1.5s infinite' }} />
            <span style={{ color: wsColor }}>LIVE {wsStatus === 'connected' ? '●' : wsStatus}</span>
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          {lastRefresh && (
            <div style={{ fontSize: 11, color: '#444' }}>
              Updated {timeAgo(lastRefresh.toISOString())}
            </div>
          )}
          <div style={{ fontSize: 12, color: '#555', fontFamily: 'monospace' }}>
            {toIST(now.toISOString()).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })}
          </div>
          <button onClick={fetchAll} style={{
            background: 'rgba(0,212,255,0.1)', border: '1px solid rgba(0,212,255,0.3)',
            color: '#00d4ff', borderRadius: 8, padding: '6px 14px', cursor: 'pointer',
            fontSize: 12, fontWeight: 600,
          }}>↺ Refresh</button>
        </div>
      </div>

      <div style={{ padding: '22px 28px', display: 'flex', flexDirection: 'column', gap: 20 }}>

        {/* ── Cluster Aggregate Bar ── */}
        <ClusterAggBar summary={summary} />

        {/* ── Summary Strip ── */}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10 }}>
          <StatPill label="Total Nodes" value={overview?.total_nodes} color="#00d4ff" icon="🖥️" />
          <StatPill label="Online" value={overview?.healthy_nodes} color="#4ade80" icon="✅" />
          <StatPill label="Warnings" value={overview?.warnings} color="#ffb347" icon="⚠️" />
          <StatPill label="Critical" value={overview?.critical} color="#ff4757" icon="🔴" />
          <StatPill label="Warmup" value={overview?.warmup} color="#a855f7" icon="🟣" />
          <StatPill label="Offline" value={overview?.offline} color="#6c757d" icon="⚫" />
          <StatPill label="Active Alerts" value={overview?.active_alerts} color="#9b8fff" icon="🔔" />
          <StatPill label="Anomalies" value={overview?.active_anomalies} color={overview?.active_anomalies > 0 ? '#ff4757' : '#4ade80'} icon="🚨" />
          <StatPill label="Incidents" value={overview?.active_incidents} color="#ff6b35" icon="📋" />
        </div>

        {/* ── Ganglia-style Cluster Overview Chart ── */}
        <div style={{
          background: 'rgba(255,255,255,0.95)', border: '1px solid rgba(148, 163, 184, 0.18)',
          borderRadius: 16, padding: '18px 20px', backdropFilter: 'blur(10px)',
        }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
            <div>
              <div style={{ fontWeight: 700, fontSize: 14, color: '#0f172a', letterSpacing: 0.3 }}>
                📊 Cluster Overview — {chartMetric}
              </div>
              <div style={{ fontSize: 11, color: '#475569', marginTop: 2 }}>
                Stacked area · all {nodes.length} node{nodes.length !== 1 ? 's' : ''} · last {historyHours}h
              </div>
            </div>
            <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
              <select value={chartMetric} onChange={e => setChartMetric(e.target.value)}
                style={{ background: '#ffffff', border: '1px solid rgba(148, 163, 184, 0.2)', color: '#0f172a', borderRadius: 8, padding: '5px 10px', fontSize: 12, cursor: 'pointer', outline: 'none' }}>
                {Object.keys(METRICS).map(k => <option key={k} value={k}>{k}</option>)}
              </select>
              <select value={historyHours} onChange={e => setHistoryHours(Number(e.target.value))}
                style={{ background: '#ffffff', border: '1px solid rgba(148, 163, 184, 0.2)', color: '#0f172a', borderRadius: 8, padding: '5px 10px', fontSize: 12, cursor: 'pointer', outline: 'none' }}>
                <option value={1}>Last 1h</option>
                <option value={3}>Last 3h</option>
                <option value={6}>Last 6h</option>
                <option value={12}>Last 12h</option>
                <option value={24}>Last 1 Day</option>
                <option value={168}>Last 1 Week</option>
                <option value={720}>Last 1 Month</option>
                <option value={8760}>Last 1 Year</option>
              </select>
            </div>
          </div>
          <ClusterOverviewChart rawHistory={history} allNodes={nodes} metricLabel={chartMetric} metricUnit={currentMetricDef.unit} />
        </div>

        {/* ── Nodes Section ── */}
        <div style={{
          background: 'rgba(255,255,255,0.98)', border: '1px solid rgba(148, 163, 184, 0.18)',
          borderRadius: 16, padding: '18px 20px',
        }}>
          {/* Toolbar: status filter + view mode toggle */}
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14, flexWrap: 'wrap', gap: 10 }}>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              {statusFilters.map(f => (
                <button key={f.key} onClick={() => setFilterStatus(f.key)} style={{
                  padding: '5px 12px', borderRadius: 8, cursor: 'pointer', fontSize: 11, fontWeight: 600,
                  background: filterStatus === f.key ? 'rgba(0,212,255,0.15)' : 'rgba(255,255,255,0.04)',
                  border: filterStatus === f.key ? '1px solid rgba(0,212,255,0.4)' : '1px solid rgba(255,255,255,0.08)',
                  color: filterStatus === f.key ? '#00d4ff' : '#888', transition: 'all 0.15s',
                }}>{f.label}</button>
              ))}
            </div>
            <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
              <span style={{ fontSize: 11, color: '#555', marginRight: 6 }}>
                {filtered.length} node{filtered.length !== 1 ? 's' : ''}{lastRefresh ? ` · Updated ${timeAgo(lastRefresh.toISOString())}` : ''}
              </span>
              {/* View mode toggle */}
              {[
                { mode: 'cards', icon: '⊞', title: 'Card View' },
                { mode: 'grid', icon: '⊟', title: 'Grid View (Ganglia-style)' },
                { mode: 'table', icon: '☰', title: 'Metrics Table' },
              ].map(({ mode, icon, title }) => (
                <button key={mode} onClick={() => setViewMode(mode)} title={title} style={{
                  width: 30, height: 30, display: 'flex', alignItems: 'center', justifyContent: 'center',
                  background: viewMode === mode ? 'rgba(0,212,255,0.15)' : 'rgba(255,255,255,0.04)',
                  border: viewMode === mode ? '1px solid rgba(0,212,255,0.4)' : '1px solid rgba(255,255,255,0.08)',
                  color: viewMode === mode ? '#00d4ff' : '#666', borderRadius: 7, cursor: 'pointer', fontSize: 14,
                }}>{icon}</button>
              ))}
            </div>
          </div>

          {/* Card View */}
          {viewMode === 'cards' && (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 14 }}>
              {filtered.map(node => (
                <NodeCard key={node.id} node={node} onClick={() => setSelectedNode(node)} />
              ))}
              {filtered.length === 0 && (
                <div style={{ gridColumn: '1/-1', color: '#444', textAlign: 'center', padding: 40 }}>
                  No nodes match the current filter
                </div>
              )}
            </div>
          )}

          {/* Grid View */}
          {viewMode === 'grid' && (
            <div style={{
              background: 'rgba(248, 250, 252, 0.96)', borderRadius: 12, padding: '12px',
              border: '1px solid rgba(148, 163, 184, 0.18)',
            }}>
              <div style={{ fontSize: 11, color: '#475569', marginBottom: 10 }}>
                All metrics × all nodes — each cell shows current value + sparkline
              </div>
              <GridView nodes={filtered} />
            </div>
          )}

          {/* Metrics Table */}
          {viewMode === 'table' && (
            <div>
              <MetricsTable nodes={filtered} />
            </div>
          )}
        </div>

        {/* ── Live Anomaly Feed ── */}
        <div style={{
          background: 'rgba(255,75,87,0.04)', border: '1px solid rgba(255,75,87,0.15)',
          borderRadius: 16, padding: '18px 20px',
        }}>
          <div style={{ fontWeight: 700, fontSize: 14, color: '#ff4757', marginBottom: 12 }}>
            🚨 Live Anomaly Feed
          </div>
          {feed.length === 0 ? (
            <div style={{ color: '#4ade80', fontSize: 12 }}>✅ No active anomalies</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6, maxHeight: 340, overflowY: 'auto' }}>
              {feed.map(ev => {
                const sc = severityColor(ev.severity);
                return (
                  <div key={ev.id} style={{
                    background: `${sc}08`, border: `1px solid ${sc}20`,
                    borderRadius: 10, padding: '10px 14px', opacity: ev.resolved ? 0.55 : 1,
                  }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3 }}>
                      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                        <span style={{ fontWeight: 700, fontSize: 12, color: sc }}>{ev.severity}</span>
                        <span style={{ fontSize: 11, color: '#666', background: 'rgba(255,255,255,0.06)', padding: '1px 6px', borderRadius: 4 }}>
                          {ev.detector}
                        </span>
                        <span style={{ fontSize: 11, fontWeight: 700, color: '#aaa' }}>{ev.hostname}</span>
                        <span style={{ fontSize: 10, color: '#555' }}>score: {ev.anomaly_score?.toFixed(3)}</span>
                      </div>
                      <span style={{ fontSize: 11, color: '#555' }}>{timeAgo(ev.detected_at)}</span>
                    </div>
                    <div style={{ fontSize: 11, color: '#999' }}>
                      <strong style={{ color: '#ccc' }}>{ev.metric_name?.replace(/_/g, ' ')}</strong>
                      {ev.metric_value != null && ` — ${ev.metric_value.toFixed(2)}`}
                      {' · '}{ev.description}
                    </div>
                    {ev.resolved && <div style={{ fontSize: 10, color: '#4ade80', marginTop: 3 }}>✓ Resolved</div>}
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* ── Incidents & Alerts Panel ── */}
        <div style={{
          background: 'rgba(255,107,53,0.04)', border: '1px solid rgba(255,107,53,0.15)',
          borderRadius: 16, padding: '18px 20px',
        }}>
          {/* Panel header + tab switcher */}
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
            <div style={{ fontWeight: 700, fontSize: 14, color: '#ff6b35' }}>
              📋 Incidents &amp; Alerts
            </div>
            <div style={{ display: 'flex', gap: 6 }}>
              {[{ key: 'incidents', label: `📋 Incidents (${incidents.filter(i => i.status === 'Active').length} open)`, color: '#ff6b35' },
              { key: 'alerts', label: `🔔 Alerts (${alerts.filter(a => a.status === 'active').length} active)`, color: '#9b8fff' }]
                .map(tab => (
                  <button key={tab.key} onClick={() => setIncAlertTab(tab.key)} style={{
                    padding: '5px 13px', borderRadius: 8, cursor: 'pointer', fontSize: 11, fontWeight: 600, border: 'none',
                    background: incAlertTab === tab.key ? `${tab.color}20` : 'transparent',
                    color: incAlertTab === tab.key ? tab.color : '#888',
                    transition: 'all 0.15s',
                  }}>{tab.label}</button>
                ))}
            </div>
          </div>

          {/* Incidents list */}
          {incAlertTab === 'incidents' && (
            incidents.length === 0 ? (
              <div style={{ color: '#4ade80', fontSize: 12 }}>✅ No incidents in the last 48 hours</div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8, maxHeight: 340, overflowY: 'auto' }}>
                {incidents.map(inc => {
                  const sc = severityColor(inc.severity);
                  const isOpen = inc.status === 'Active';
                  const startMs = new Date(inc.start_time + 'Z').getTime();
                  const endMs = inc.end_time ? new Date(inc.end_time + 'Z').getTime() : Date.now();
                  const duration = Math.round((endMs - startMs) / 60000);
                  return (
                    <div key={inc.id} style={{
                      background: isOpen ? `${sc}0c` : 'rgba(74,222,128,0.05)',
                      border: `1px solid ${isOpen ? sc : '#4ade80'}28`,
                      borderLeft: `3px solid ${isOpen ? sc : '#4ade80'}`,
                      borderRadius: 10, padding: '12px 14px',
                      opacity: isOpen ? 1 : 0.7,
                    }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 4 }}>
                        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                          <span style={{ fontWeight: 700, fontSize: 12, color: isOpen ? sc : '#4ade80' }}>
                            {isOpen ? '🔴' : '✅'} {inc.severity || 'Unknown'}
                          </span>
                          <span style={{ fontSize: 11, fontWeight: 700, color: '#555', background: 'rgba(0,0,0,0.05)', padding: '1px 7px', borderRadius: 4 }}>
                            {inc.hostname}
                          </span>
                          <span style={{ fontSize: 10, color: '#888', padding: '1px 6px', border: '1px solid rgba(0,0,0,0.08)', borderRadius: 4 }}>
                            {isOpen ? 'OPEN' : 'RESOLVED'}
                          </span>
                          {inc.confidence != null && (
                            <span style={{ fontSize: 10, color: '#aaa' }}>conf: {(inc.confidence * 100).toFixed(0)}%</span>
                          )}
                        </div>
                        <div style={{ fontSize: 10, color: '#666', textAlign: 'right', whiteSpace: 'nowrap', marginLeft: 8 }}>
                          <div>{fmtDate(inc.start_time)}</div>
                          <div style={{ color: '#aaa' }}>{duration}m {isOpen ? 'ongoing' : 'duration'}</div>
                        </div>
                      </div>
                      <div style={{ fontSize: 11, color: '#666' }}>{inc.description}</div>
                      {inc.peak_value != null && (
                        <div style={{ fontSize: 10, color: '#aaa', marginTop: 3 }}>Peak: {inc.peak_value.toFixed(2)}</div>
                      )}
                    </div>
                  );
                })}
              </div>
            )
          )}

          {/* Alerts list */}
          {incAlertTab === 'alerts' && (
            alerts.length === 0 ? (
              <div style={{ color: '#4ade80', fontSize: 12 }}>✅ No alerts in the last 48 hours</div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8, maxHeight: 340, overflowY: 'auto' }}>
                {alerts.map(al => {
                  const sc = severityColor(al.severity);
                  const isActive = al.status === 'active';
                  return (
                    <div key={al.id} style={{
                      background: isActive ? `${sc}0c` : 'rgba(74,222,128,0.05)',
                      border: `1px solid ${isActive ? sc : '#4ade80'}28`,
                      borderLeft: `3px solid ${isActive ? sc : '#4ade80'}`,
                      borderRadius: 10, padding: '12px 14px',
                      opacity: isActive ? 1 : 0.65,
                    }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                          <span style={{ fontWeight: 700, fontSize: 12, color: isActive ? sc : '#4ade80' }}>
                            {isActive ? '🔔' : '✅'} {al.severity || 'Unknown'}
                          </span>
                          <span style={{ fontSize: 11, fontWeight: 700, color: '#555', background: 'rgba(0,0,0,0.05)', padding: '1px 7px', borderRadius: 4 }}>
                            {al.hostname}
                          </span>
                          <span style={{ fontSize: 10, color: '#888', padding: '1px 6px', border: '1px solid rgba(0,0,0,0.08)', borderRadius: 4 }}>
                            {isActive ? 'ACTIVE' : 'RESOLVED'}
                          </span>
                        </div>
                        <span style={{ fontSize: 11, color: '#666' }}>{timeAgo(al.alert_time)}</span>
                      </div>
                      <div style={{ fontSize: 11, color: '#666', marginTop: 5 }}>{al.summary}</div>
                    </div>
                  );
                })}
              </div>
            )
          )}
        </div>

      </div>

      {/* ── Node Drilldown Modal ── */}
      {selectedNode && (
        <NodeModal
          node={nodes.find(n => n.id === selectedNode.id) || selectedNode}
          onClose={() => setSelectedNode(null)}
          onResolveAnomaly={handleResolveAnomaly}
        />
      )}
    </div>
  );
}
