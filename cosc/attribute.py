"""Projection-only trajectory-point attribution.

Given the pipeline's already-made (flight, mask) associations, this module
answers "which discrete trajectory point along the flight produced this
mask?" by projecting the mask polygon onto the flight's polyline at the
mask's timestamp. Perpendicular distance is preserved but plays no role
in the decision — the pipeline has already certified that the mask
belongs to the flight; perpendicular offset just reflects wind error.

Public API:
    build_polyline_at_ts(window_df, mask_ts) -> DataFrame
    split_polyline_at_gaps(poly_df) -> List[(lo, hi)]
    densify_polygon(polygon, max_edge_px) -> ndarray
    attribute_mask(window_df, polygon, mask_ts) -> DataFrame
"""

from __future__ import annotations

from typing import Dict, List, Tuple

import cv2
import numpy as np
import pandas as pd


# Time-gap threshold for splitting the polyline at multi-pass / loiter voids:
# consecutive ADS-B emit times more than this apart → not connected (no
# contrail in between to attribute). Anything >> ADS-B resampling cadence
# (seconds to a minute) and << typical loiter interval (minutes) works.
SEGMENT_MAX_GAP_S = 300.0

# Parametric t-edge rule for which polyline-segment endpoint to attribute:
# t in [0, EDGE)        → only the lower-index endpoint
# t in (1 - EDGE, 1]    → only the upper-index endpoint
# else                  → both endpoints
ATTRIB_T_EDGE = 0.25


def build_polyline_at_ts(flight_trace_window: pd.DataFrame,
                         mask_ts: pd.Timestamp) -> pd.DataFrame:
    """Reconstruct the flight's polyline at mask_ts using REAL trajectory
    points only (encoded interpolation bridges from interpolation.py — those
    with waypoint >= 10000 — are dropped). One row per trajectory point,
    nearest-in-time sample to mask_ts, sorted by waypoint."""
    if flight_trace_window.empty:
        return pd.DataFrame()
    df = flight_trace_window.copy()
    df = df[df['waypoint'] < 10000]
    if df.empty:
        return pd.DataFrame()
    df['_dt'] = (df['timestamp'] - mask_ts).abs()
    return (df.sort_values(['waypoint', '_dt'])
              .drop_duplicates('waypoint', keep='first')
              .sort_values('waypoint')
              .reset_index(drop=True))


def split_polyline_at_gaps(poly_df: pd.DataFrame,
                           max_gap_s: float = SEGMENT_MAX_GAP_S) -> List[Tuple[int, int]]:
    """Return [(lo, hi), ...] index ranges of contiguous polyline runs:
    consecutive trajectory points whose ADS-B emit_ts gap exceeds max_gap_s
    are treated as separate runs (a multi-pass loiter / extended absence
    creates a void with no contrail in between)."""
    if len(poly_df) < 2:
        return [(0, len(poly_df))]
    emit_ts_s = (poly_df['timestamp'] - poly_df['age']).astype('int64').to_numpy() / 1e9
    t_gaps_s = np.diff(emit_ts_s)
    split_at = np.where(t_gaps_s > max_gap_s)[0]
    runs = []
    lo = 0
    for s in split_at:
        runs.append((lo, s + 1))
        lo = s + 1
    runs.append((lo, len(poly_df)))
    return [(a, b) for a, b in runs if b - a >= 1]


def densify_polygon(polygon: np.ndarray, max_edge_px: float = 2.0) -> np.ndarray:
    """Insert intermediate vertices along each polygon edge so the max edge
    length is ≤ max_edge_px. Lets the closest-vertex-to-segment
    approximation correctly find the closest polygon BOUNDARY point even
    when the polyline crosses a long edge with no native vertex nearby."""
    pts = polygon.reshape(-1, 2).astype(np.float32)
    if len(pts) < 2:
        return polygon
    out = []
    for i in range(len(pts)):
        p1 = pts[i]
        p2 = pts[(i + 1) % len(pts)]
        edge_len = float(np.linalg.norm(p2 - p1))
        n_steps = max(1, int(np.ceil(edge_len / max_edge_px)))
        for j in range(n_steps):
            t = j / n_steps
            out.append(p1 + t * (p2 - p1))
    return np.array(out, dtype=np.float32).reshape(-1, 1, 2)


def attribute_mask(flight_trace_window: pd.DataFrame,
                   polygon: np.ndarray,
                   mask_ts: pd.Timestamp) -> pd.DataFrame:
    """Project every densified polygon-boundary point onto the flight's
    polyline (split at multi-pass gaps) — for each polygon point we find
    its closest polyline segment and the parametric t at which it lands,
    then attribute the bracketing waypoints via the t-edge rule.

    Returns one row per attributed trajectory point with columns:
        waypoint, ts_utc, age_s, x, y, perp_d_px
    The perpendicular distance is preserved for downstream wind-error
    diagnostics; it does NOT gate attribution."""
    poly_df = build_polyline_at_ts(flight_trace_window, mask_ts)
    if len(poly_df) < 2:
        return pd.DataFrame()

    polygon_d = densify_polygon(polygon, max_edge_px=2.0)
    poly_pts = polygon_d.reshape(-1, 2).astype(np.float64)
    wps = poly_df['waypoint'].to_numpy()
    xs = poly_df['x'].to_numpy(dtype=np.float64)
    ys = poly_df['y'].to_numpy(dtype=np.float64)
    tss = poly_df['timestamp'].to_numpy()
    ages = poly_df['age'].dt.total_seconds().to_numpy()
    runs = split_polyline_at_gaps(poly_df)

    by_wp: Dict[int, dict] = {}

    def _record(j: int, d_perp: float) -> None:
        wp = int(wps[j])
        cur = by_wp.get(wp)
        if cur is None or d_perp < cur['perp_d_px']:
            by_wp[wp] = {
                'waypoint': wp,
                'ts_utc': pd.Timestamp(tss[j]).tz_convert('UTC'),
                'age_s': float(ages[j]),
                'x': float(xs[j]),
                'y': float(ys[j]),
                'perp_d_px': float(d_perp),
            }

    for px, py in poly_pts:
        best = None  # (perp_d, seg_i, t_best)
        for lo, hi in runs:
            if hi - lo < 2:
                continue
            x1 = xs[lo:hi - 1]; y1 = ys[lo:hi - 1]
            x2 = xs[lo + 1:hi]; y2 = ys[lo + 1:hi]
            dx = x2 - x1; dy = y2 - y1
            seg_len_sq = dx * dx + dy * dy
            safe = np.where(seg_len_sq > 1e-12, seg_len_sq, 1.0)
            t = np.clip(((px - x1) * dx + (py - y1) * dy) / safe, 0.0, 1.0)
            nx = x1 + t * dx; ny = y1 + t * dy
            d = np.sqrt((px - nx) ** 2 + (py - ny) ** 2)
            mi = int(np.argmin(d))
            cand = (float(d[mi]), lo + mi, float(t[mi]))
            if best is None or cand[0] < best[0]:
                best = cand
        if best is None:
            continue
        perp_d, seg_i, t_best = best
        if t_best < ATTRIB_T_EDGE:
            _record(seg_i, perp_d)
        elif t_best > 1 - ATTRIB_T_EDGE:
            _record(seg_i + 1, perp_d)
        else:
            _record(seg_i, perp_d)
            _record(seg_i + 1, perp_d)

    return pd.DataFrame(list(by_wp.values()))
