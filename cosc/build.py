"""COSC v0.1 producer — turn one video's Janus pipeline output into the
COSC parquet table set.

Public entry point:
    build_video(staging_dir, out_root) -> dict

Re-runs over the same inputs are deterministic (parquet writes overwrite).
"""

from __future__ import annotations

import json
import subprocess
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from cosc import SCHEMA_VERSION, __version__
from cosc.attribute import attribute_mask, build_polyline_at_ts, split_polyline_at_gaps
from cosc.schema import ALL_TABLES, coerce_to_arrow


# ---- Pipeline-output loaders ------------------------------------------------


def _find_prefix(staging_dir: Path) -> str:
    """Extract the '<station>_<start>' filename prefix used inside output/."""
    poly_files = list(staging_dir.glob("*_polygons.json"))
    if not poly_files:
        raise FileNotFoundError(f"no polygons.json in {staging_dir}")
    return poly_files[0].stem.replace("_polygons", "").split("_to_")[0]


def _load_janus_output(staging_dir: Path) -> dict:
    """Pull every pipeline artifact needed to build the COSC tables."""
    out = staging_dir / "output"
    prefix = _find_prefix(staging_dir)
    station_code = prefix.split("_")[0]

    # Lazy-import advection_storage from the Janus repo so this module doesn't
    # take a hard dependency on Janus being installed as a package.
    janus_assoc = Path("/home/ops/source/Janus/Associator")
    if str(janus_assoc) not in sys.path:
        sys.path.insert(0, str(janus_assoc))
    from advection_storage import load_advection_efficient  # type: ignore

    print(f"[load] staging={staging_dir.name} prefix={prefix}")

    polygons_list = json.loads((staging_dir / f"{staging_dir.name}_polygons.json").read_text())
    oid_index = {int(k): v for k, v in
                 json.loads((out / "object_id_index.json").read_text()).items()}
    frametimes = json.loads(
        (staging_dir / f"{staging_dir.name}_frametimes.json").read_text())
    adsb = pd.read_parquet(out / f"{prefix}_adsb_filtered.parquet")
    flight_interp = pd.read_parquet(out / f"{prefix}_flight_interpolated.parquet")
    dry_adv = load_advection_efficient(out / f"{prefix}_advection_raw.parquet")

    assoc_csv = pd.read_csv(out / f"{prefix}_associations.csv")

    # Optional: ground truth
    gt_path = out / f"{prefix}_ground_truth.json"
    gt = None
    gt_status = 'none'
    gt_last_edit_ts = None
    gt_edit_count = 0
    if gt_path.exists():
        gt = json.loads(gt_path.read_text())
        gt_status = gt.get('status', 'wip').lower()
        if 'last_modified' in gt:
            try:
                gt_last_edit_ts = pd.Timestamp(gt['last_modified'], tz='UTC')
            except Exception:
                gt_last_edit_ts = None
        gt_edit_count = int(gt.get('edit_count', 0))
        print(f"[load] GT status={gt_status} edits={gt_edit_count}")

    # Platepar (for image dimensions + full-precision camera coords). Full
    # precision stays INTERNAL to the producer (used for range_to_camera
    # calculation, projection); the published stations.parquet exposes
    # only the rounded coords from station_meta for privacy.
    platepars = list(staging_dir.glob("platepars*.json"))
    image_w_px, image_h_px = 1280, 720  # safe defaults
    platepar_version = None
    platepar_lat = None
    platepar_lon = None
    if platepars:
        pp_data = json.loads(platepars[0].read_text())
        first_key = next(iter(pp_data))
        pp_entry = pp_data[first_key]
        image_w_px = int(pp_entry.get('X_res', image_w_px))
        image_h_px = int(pp_entry.get('Y_res', image_h_px))
        platepar_version = pp_entry.get('version') or pp_entry.get('star_list_file')
        platepar_lat = pp_entry.get('lat')
        platepar_lon = pp_entry.get('lon')

    return {
        'staging_dir': staging_dir, 'station_code': station_code, 'prefix': prefix,
        'polygons_list': polygons_list, 'oid_index': oid_index,
        'frametimes': frametimes, 'adsb': adsb,
        'flight_interp': flight_interp, 'dry_adv': dry_adv,
        'assoc_csv': assoc_csv, 'gt': gt, 'gt_status': gt_status,
        'gt_last_edit_ts': gt_last_edit_ts, 'gt_edit_count': gt_edit_count,
        'image_w_px': image_w_px, 'image_h_px': image_h_px,
        'platepar_version': platepar_version,
        'platepar_lat': platepar_lat, 'platepar_lon': platepar_lon,
    }


def _git_commit_sha(janus_repo: Path) -> Optional[str]:
    try:
        return subprocess.check_output(
            ['git', 'rev-parse', '--short', 'HEAD'],
            cwd=janus_repo, text=True).strip()
    except Exception:
        return None


# ---- Helpers ---------------------------------------------------------------


def _decode_oid(frame: int, obj_in_frame: int, poly_idx: int) -> int:
    return frame * 10000 + obj_in_frame * 100 + poly_idx


def _parse_frametimes(ft: dict) -> Dict[int, pd.Timestamp]:
    """Parse the Janus frametimes JSON into {int frame_idx: UTC timestamp}.

    Frametimes JSON has the structure:
        { '<frame_idx>': '<YYYYMMDD>_<HHMMSS>_<mss>_<flag>', ... }
    Keys are stringified integer frame indices; values are the original
    frame-image filenames whose timestamp part is reliable. We parse the
    timestamp (with millisecond precision) and discard the trailing flag.
    """
    out: Dict[int, pd.Timestamp] = {}
    for k, v in ft.items():
        try:
            idx = int(k)
        except ValueError:
            continue
        parts = str(v).split('_')
        if len(parts) < 3:
            continue
        ymd, hms, ms = parts[0], parts[1], parts[2]
        # Pad ms (3 digits) to microseconds (6 digits) so %f parses cleanly.
        ms = (ms + '000')[:6]
        try:
            ts = datetime.strptime(f"{ymd}_{hms}_{ms}",
                                   "%Y%m%d_%H%M%S_%f").replace(tzinfo=timezone.utc)
            out[idx] = pd.Timestamp(ts)
        except ValueError:
            continue
    return out


def _haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0088
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp = np.radians(lat2 - lat1)
    dl = np.radians(lon2 - lon1)
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * R * np.arcsin(np.sqrt(a))


def _polygon_to_wkt(coords: List[List[float]]) -> str:
    """Convert a single-contour polygon (list of [x,y]) to WKT POLYGON."""
    if not coords:
        return ""
    pts = list(coords)
    if pts[0] != pts[-1]:
        pts = pts + [pts[0]]  # close the ring
    inner = ", ".join(f"{p[0]:.2f} {p[1]:.2f}" for p in pts)
    return f"POLYGON (({inner}))"


def _multi_polygon_to_wkt(contours: List[List[List[float]]]) -> str:
    """Convert a multi-contour polygon list to WKT POLYGON (single) or
    MULTIPOLYGON (multiple)."""
    if len(contours) == 1:
        return _polygon_to_wkt(contours[0])
    polys = []
    for c in contours:
        pts = list(c)
        if pts and pts[0] != pts[-1]:
            pts = pts + [pts[0]]
        inner = ", ".join(f"{p[0]:.2f} {p[1]:.2f}" for p in pts)
        polys.append(f"(({inner}))")
    return f"MULTIPOLYGON ({', '.join(polys)})"


# ---- Per-table builders ----------------------------------------------------


def build_station_row(data: dict, station_meta: dict) -> pd.DataFrame:
    """One-row stations DataFrame for this video's station."""
    sc = data['station_code']
    meta = station_meta.get(sc, {})
    return pd.DataFrame([{
        'station_id': sc,
        'country': meta.get('country', sc[:2]),
        'station_name': meta.get('station_name'),
        'lat': float(meta.get('lat', 0.0)),
        'lon': float(meta.get('lon', 0.0)),
        'alt_m': meta.get('alt_m'),
        'tz': meta.get('tz'),
        'image_w_px': data['image_w_px'],
        'image_h_px': data['image_h_px'],
        'camera_make': meta.get('camera_make'),
        'camera_model': meta.get('camera_model'),
        'lens_model': meta.get('lens_model'),
        'platepar_version': data.get('platepar_version'),
        'fov_mask_geojson': meta.get('fov_mask_geojson'),
        'comment': meta.get('comment'),
    }])


def build_video_row(data: dict, video_id: str, n_traj: int, n_events: int,
                    n_assoc: int, pipeline_commit_sha: Optional[str]) -> pd.DataFrame:
    parsed_ft = _parse_frametimes(data['frametimes'])
    frame_keys = sorted(parsed_ft.keys())
    start_ts = parsed_ft[frame_keys[0]] if frame_keys else None
    end_ts = parsed_ft[frame_keys[-1]] if frame_keys else None
    n_frames = len(frame_keys)
    if n_frames >= 2:
        step = (end_ts - start_ts).total_seconds() / max(n_frames - 1, 1)
    else:
        step = float('nan')
    return pd.DataFrame([{
        'video_id': video_id,
        'station_id': data['station_code'],
        'start_ts_utc': start_ts,
        'end_ts_utc': end_ts,
        'n_frames': n_frames,
        'frame_step_s': step,
        'n_flights_associated': n_assoc,
        'n_detection_events': n_events,
        'n_trajectory_points': n_traj,
        'gt_status': data['gt_status'],
        'gt_last_edit_ts_utc': data['gt_last_edit_ts'],
        'gt_edit_count': data['gt_edit_count'],
        'pipeline_version': None,  # populate when pipeline is properly semver'd
        'pipeline_commit_sha': pipeline_commit_sha,
        'cleaning_version': SCHEMA_VERSION,
    }])


def build_trajectories(data: dict, video_id: str,
                       camera_lat: float, camera_lon: float) -> pd.DataFrame:
    """Cleaned trajectory points for every flight in this video.

    Source = flight_interpolated.parquet (post-cleaning, post-range-adaptive
    resampling). Range to camera is computed at row time using camera lat/lon
    from the station meta."""
    fi = data['flight_interp'].copy()
    if fi.empty:
        return pd.DataFrame()
    if fi.index.name == 'timestamp':
        fi = fi.reset_index()

    fi['range_to_camera_km'] = _haversine_km(
        fi['latitude'].to_numpy(), fi['longitude'].to_numpy(),
        camera_lat, camera_lon).astype('float32')

    fi = fi.sort_values(['flight_id', 'timestamp']).reset_index(drop=True)
    fi['sampling_interval_s'] = (fi.groupby('flight_id')['timestamp']
                                 .diff().dt.total_seconds().astype('float32'))

    out = pd.DataFrame({
        'video_id': video_id,
        'flight_id': fi['flight_id'].astype(str),
        'ts_utc': pd.to_datetime(fi['timestamp'], utc=True),
        'lat': fi['latitude'].astype('float64'),
        'lon': fi['longitude'].astype('float64'),
        'alt_baro_m': fi.get('altitude_baro'),
        'alt_geom_m': fi.get('altitude_geom'),
        'alt_gnss_m': fi.get('altitude_gnss'),
        'sampling_interval_s': fi['sampling_interval_s'],
        'range_to_camera_km': fi['range_to_camera_km'],
        'flight_number': fi.get('flight_number'),
        'callsign': fi.get('callsign'),
        'tail_number': fi.get('tail_number'),
        'icao_address': fi.get('icao_address'),
        'aircraft_type_icao': fi.get('aircraft_type_icao'),
        'dep_apt_icao': fi.get('departure_airport_icao'),
        'arr_apt_icao': fi.get('arrival_airport_icao'),
        'source': 'spire',
    })
    return out


def build_detection_events_and_observations(data: dict, video_id: str):
    """Run the projection-only attribution on every winning (flight, mask)
    pair and emit BOTH the per-event long table AND the per-(flight, emit_ts)
    aggregated summary.

    Returns (events_df, obs_df).
    """
    polygons_list = data['polygons_list']
    oid_index = data['oid_index']
    dry_adv = data['dry_adv']
    if dry_adv.index.name == 'timestamp':
        dry_adv = dry_adv.reset_index()
    flight_index = dry_adv.set_index('flight_id').sort_index()

    # Group polygons by winning flight for fast iteration; produce per-mask records.
    per_flight: Dict[str, List[dict]] = {}
    poly_centroid_area = {}  # oid -> (cx, cy, area)
    for entry in polygons_list:
        frame = int(entry['f'])
        ts = pd.Timestamp(int(entry['t']), unit='s', tz='UTC')
        obj_in_frame = int(entry.get('i', 0))
        for poly_idx, poly_pts in enumerate(entry['p']):
            oid = _decode_oid(frame, obj_in_frame, poly_idx)
            fids = oid_index.get(oid)
            if not fids:
                continue
            fid = fids[0]
            poly_arr = np.asarray(poly_pts, dtype=np.float32).reshape(-1, 1, 2)
            per_flight.setdefault(fid, []).append({
                'oid': oid, 'frame': frame, 'timestamp': ts,
                'polygon': poly_arr, 'yolo_confidence': float(entry.get('c', 0.0)),
            })
            # Centroid + area for the events table
            pts = np.asarray(poly_pts, dtype=np.float32)
            import cv2
            area = abs(cv2.contourArea(pts))
            cx = float(pts[:, 0].mean()) if len(pts) else None
            cy = float(pts[:, 1].mean()) if len(pts) else None
            poly_centroid_area[oid] = (cx, cy, int(area))

    win = pd.Timedelta(seconds=15)
    event_rows: List[dict] = []
    print(f"[attribute] {len(per_flight)} winning flights, "
          f"{sum(len(v) for v in per_flight.values())} (flight,mask) pairs")

    # Per-flight confidence_score + w_perp lookup from associations.csv
    assoc = data['assoc_csv'].set_index('flight_id')
    fid_conf = assoc['confidence_score'].to_dict() if 'confidence_score' in assoc.columns else {}
    fid_wperp = assoc['wind_correction_w_perp_ms'].to_dict() if 'wind_correction_w_perp_ms' in assoc.columns else {}

    for fi, (fid, entries) in enumerate(per_flight.items(), 1):
        try:
            flight_df = flight_index.loc[fid]
        except KeyError:
            continue
        if isinstance(flight_df, pd.Series):
            flight_df = flight_df.to_frame().T
        flight_df = flight_df.sort_values('timestamp')
        ts_arr = flight_df['timestamp'].to_numpy()

        for entry in entries:
            ts = entry['timestamp']
            lo = np.searchsorted(ts_arr, ts - win, side='left')
            hi = np.searchsorted(ts_arr, ts + win, side='right')
            window = flight_df.iloc[lo:hi]
            attribs = attribute_mask(window, entry['polygon'], ts)
            if attribs.empty:
                continue
            cx, cy, area = poly_centroid_area.get(entry['oid'], (None, None, None))
            for _, ar in attribs.iterrows():
                event_rows.append({
                    'video_id': video_id, 'flight_id': fid,
                    'emit_ts_utc': (pd.Timestamp(ar['ts_utc']) - pd.Timedelta(seconds=ar['age_s'])).tz_convert('UTC'),
                    'observed_ts_utc': ts,
                    'age_s': float(ar['age_s']),
                    'frame_idx': entry['frame'],
                    'mask_oid': entry['oid'],
                    'mask_centroid_x_px': cx,
                    'mask_centroid_y_px': cy,
                    'mask_area_px': area,
                    'mask_yolo_confidence': entry['yolo_confidence'],
                    'trajectory_point_x_px': float(ar['x']),
                    'trajectory_point_y_px': float(ar['y']),
                    'perp_dist_px': float(ar['perp_d_px']),
                    'pipeline_dist_to_polyline_m': None,
                    'pipeline_mask_thresh_m': None,
                    'pipeline_angle_deg': None,
                    'source': 'gt' if data['gt_status'] == 'complete' else 'pipeline',
                })
        if fi % 25 == 0:
            print(f"[attribute]   {fi}/{len(per_flight)} flights done "
                  f"({len(event_rows)} events)")
    print(f"[attribute] total events: {len(event_rows)}")
    events_df = pd.DataFrame(event_rows)

    if events_df.empty:
        return events_df, pd.DataFrame()

    # Aggregate to trajectory_point_observations
    grp = events_df.groupby(['flight_id', 'emit_ts_utc'])
    obs = grp.agg(
        detect_first_age_s=('age_s', 'min'),
        detect_last_age_s=('age_s', 'max'),
        n_detection_events=('age_s', 'count'),
        emit_lat=('trajectory_point_x_px', lambda _: None),  # placeholder, filled below
    ).reset_index()
    # Look up emission positions from the per-flight trajectory (the trajectories table)
    # We re-derive emit_lat / emit_lon by matching emit_ts_utc to the cleaned trajectory.
    fi_clean = data['flight_interp'].copy()
    if fi_clean.index.name == 'timestamp':
        fi_clean = fi_clean.reset_index()
    fi_clean['timestamp'] = pd.to_datetime(fi_clean['timestamp'], utc=True)
    lookup = (fi_clean.set_index(['flight_id', 'timestamp'])
              [['latitude', 'longitude', 'altitude_baro', 'altitude_geom']])

    def _lookup(fid, ts):
        try:
            r = lookup.loc[(fid, ts)]
            if isinstance(r, pd.DataFrame):
                r = r.iloc[0]
            return (float(r['latitude']), float(r['longitude']),
                    float(r['altitude_baro']) if pd.notna(r['altitude_baro']) else None,
                    float(r['altitude_geom']) if pd.notna(r['altitude_geom']) else None)
        except KeyError:
            return (None, None, None, None)

    emit_meta = obs.apply(lambda r: _lookup(r['flight_id'], r['emit_ts_utc']),
                          axis=1, result_type='expand')
    emit_meta.columns = ['emit_lat', 'emit_lon', 'emit_alt_baro_m', 'emit_alt_geom_m']
    obs = obs.drop(columns=['emit_lat']).join(emit_meta)

    # FOV-window stats — derived from dry_adv positions at observed timestamps.
    # For each (flight, waypoint), walk every (frame, ts) where this point's
    # advected position has a valid x,y inside the frame bounds.
    # Lightweight approximation: use any dry_adv row's x,y for the same
    # (flight_id, ts) range; n_in_fov_events = rows where (0 <= x < W, 0 <= y < H).
    # If a row has NaN x/y it isn't in FOV by definition.
    W = data['image_w_px']; H = data['image_h_px']
    da = data['dry_adv']
    if da.index.name == 'timestamp':
        da = da.reset_index()
    if 'x' in da.columns and 'y' in da.columns:
        da = da.dropna(subset=['x', 'y'])
        in_fov = (da['x'] >= 0) & (da['x'] < W) & (da['y'] >= 0) & (da['y'] < H)
        da = da[in_fov]
        # For each (flight_id, waypoint, emit_ts), record min/max age and count.
        # emit_ts == timestamp - age. We have to compute it from the original.
        da_real = da[da['waypoint'] < 10000].copy()
        da_real['emit_ts_utc'] = (pd.to_datetime(da_real['timestamp'], utc=True)
                                  - pd.to_timedelta(da_real['age']))
        gp = (da_real.groupby(['flight_id', 'emit_ts_utc'])
              .agg(in_fov_first_age_s=('age', lambda s: s.dt.total_seconds().min()),
                   in_fov_last_age_s=('age', lambda s: s.dt.total_seconds().max()),
                   n_in_fov_events=('age', 'count'))
              .reset_index())
        obs = obs.merge(gp, on=['flight_id', 'emit_ts_utc'], how='left')
    else:
        obs['in_fov_first_age_s'] = None
        obs['in_fov_last_age_s'] = None
        obs['n_in_fov_events'] = None

    obs['detection_rate'] = np.where(
        obs['n_in_fov_events'] > 0,
        obs['n_detection_events'] / obs['n_in_fov_events'].clip(lower=1),
        np.nan,
    ).astype('float32')

    # Censoring flags
    frame_step = 5.0  # TODO read from videos table once we plumb it in
    obs['censored_fov_exit'] = (
        (obs['in_fov_last_age_s'].notna())
        & (obs['detect_last_age_s'].notna())
        & (obs['detect_last_age_s'] >= obs['in_fov_last_age_s'] - frame_step)
    )
    _parsed_ft = _parse_frametimes(data['frametimes'])
    end_ts = _parsed_ft[max(_parsed_ft)] if _parsed_ft else pd.Timestamp.now(tz='UTC')
    obs['censored_video_end'] = (
        (obs['emit_ts_utc'] + pd.to_timedelta(obs['detect_last_age_s'], unit='s')
         >= end_ts - pd.Timedelta(seconds=frame_step))
    )

    obs['video_id'] = video_id
    obs['source'] = events_df['source'].iloc[0] if not events_df.empty else 'pipeline'
    obs['pipeline_confidence_score'] = obs['flight_id'].map(fid_conf).astype('float32')
    obs['wind_correction_w_perp_ms'] = obs['flight_id'].map(fid_wperp).astype('float32')

    return events_df, obs


def build_mask_polygons(data: dict, video_id: str) -> pd.DataFrame:
    polygons_list = data['polygons_list']
    oid_index = data['oid_index']
    gt_map = {}
    if data.get('gt'):
        gt_map = data['gt'].get('effective_map') or {}

    rows = []
    for entry in polygons_list:
        frame = int(entry['f'])
        ts = pd.Timestamp(int(entry['t']), unit='s', tz='UTC')
        obj_in_frame = int(entry.get('i', 0))
        yolo_c = float(entry.get('c', 0.0))
        for poly_idx, poly_pts in enumerate(entry['p']):
            oid = _decode_oid(frame, obj_in_frame, poly_idx)
            wkt = _polygon_to_wkt(poly_pts)
            import cv2
            area = int(abs(cv2.contourArea(np.asarray(poly_pts, dtype=np.float32))))
            fids = oid_index.get(oid) or []
            pipeline_fid = fids[0] if fids else None
            gt_fid = gt_map.get(str(oid))  # may be None / 'artifact' / fid
            is_artifact_gt = (gt_fid == 'artifact')
            rows.append({
                'video_id': video_id,
                'mask_oid': oid,
                'frame_idx': frame,
                'observed_ts_utc': ts,
                'yolo_confidence': yolo_c,
                'polygon_wkt': wkt,
                'polygon_area_px': area,
                'polygon_n_vertices': len(poly_pts),
                'pipeline_assigned_flight_id': pipeline_fid,
                'gt_assigned_flight_id': gt_fid if not is_artifact_gt else None,
                'is_artifact_gt': is_artifact_gt,
            })
    return pd.DataFrame(rows)


# ---- Top-level driver ------------------------------------------------------


def build_video(staging_dir: Path, out_root: Path,
                station_meta: Optional[dict] = None) -> dict:
    """Build the COSC table set for one video. Returns a manifest dict.

    `station_meta` is a per-station mapping (station_id -> dict of station-
    level metadata: country, lat, lon, alt_m, station_name, tz, camera info,
    fov_mask_geojson, comment). Required for the stations table.
    """
    station_meta = station_meta or {}
    data = _load_janus_output(staging_dir)
    video_id = f"{data['station_code']}_{data['prefix'].split('_', 1)[1]}"
    sc = data['station_code']
    sm = station_meta.get(sc, {})
    # Use FULL-precision platepar coords for internal range calculations
    # so range_to_camera_km isn't biased by the ~500 m rounding the
    # published station coords get for privacy. Fall back to the
    # published (rounded) coords if no platepar is available.
    camera_lat = float(data.get('platepar_lat') or sm.get('lat', 0.0))
    camera_lon = float(data.get('platepar_lon') or sm.get('lon', 0.0))
    print(f"[build] video_id={video_id}")

    # Compute everything first, then assemble manifest
    pipeline_commit_sha = _git_commit_sha(Path('/home/ops/source/Janus'))

    traj_df = build_trajectories(data, video_id, camera_lat, camera_lon)
    events_df, obs_df = build_detection_events_and_observations(data, video_id)
    masks_df = build_mask_polygons(data, video_id)
    station_df = build_station_row(data, station_meta)
    video_df = build_video_row(data, video_id,
                               n_traj=len(traj_df),
                               n_events=len(events_df),
                               n_assoc=traj_df['flight_id'].nunique() if not traj_df.empty else 0,
                               pipeline_commit_sha=pipeline_commit_sha)

    # Date for partition path = start date in UTC.
    date_partition = video_df['start_ts_utc'].iloc[0].strftime('%Y-%m-%d')
    country = station_df['country'].iloc[0]
    pkey = (country, sc, date_partition)

    base = out_root / f"country={country}" / f"station={sc}" / f"date={date_partition}"
    base.mkdir(parents=True, exist_ok=True)

    written = {}
    for name, df in [
        ('stations', station_df),
        ('videos', video_df),
        ('trajectories', traj_df),
        ('trajectory_point_observations', obs_df),
        ('detection_events', events_df),
        ('mask_polygons', masks_df),
    ]:
        if df is None or df.empty:
            print(f"[write] skipping {name} (empty)")
            continue
        table = coerce_to_arrow(df, name)
        out_path = base / f"{name}.parquet"
        pq.write_table(table, out_path, compression='zstd', compression_level=6)
        written[name] = {'path': str(out_path), 'rows': len(df)}
        print(f"[write] {name}: {len(df)} rows -> {out_path}")

    return {
        'cosc_version': __version__,
        'schema_version': SCHEMA_VERSION,
        'video_id': video_id,
        'partition': pkey,
        'tables': written,
        'pipeline_commit_sha': pipeline_commit_sha,
        'built_at_utc': datetime.now(timezone.utc).isoformat(),
    }
