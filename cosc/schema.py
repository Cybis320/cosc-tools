"""COSC v0.1 column definitions.

One Schema per table. Each Schema enumerates columns + Arrow dtypes so the
build script can coerce the produced DataFrames into a typed parquet without
relying on pandas' inferred types (which can drift between runs and break
the public contract).

Keep this file in sync with ../specs/SCHEMA.md. If you add a column here
without updating SCHEMA.md, the contract is broken.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

import pyarrow as pa


@dataclass(frozen=True)
class Schema:
    """One table's column definitions in Arrow types."""
    table: str
    fields: List[pa.Field]

    @property
    def arrow_schema(self) -> pa.Schema:
        return pa.schema(self.fields)

    @property
    def column_names(self) -> List[str]:
        return [f.name for f in self.fields]


_UTC = pa.timestamp('ns', tz='UTC')


STATIONS = Schema(
    table='stations',
    fields=[
        pa.field('station_id', pa.string(), nullable=False),
        pa.field('country', pa.string(), nullable=False),
        pa.field('station_name', pa.string()),
        pa.field('lat', pa.float64(), nullable=False),
        pa.field('lon', pa.float64(), nullable=False),
        pa.field('alt_m', pa.float64()),
        pa.field('tz', pa.string()),
        pa.field('image_w_px', pa.int32(), nullable=False),
        pa.field('image_h_px', pa.int32(), nullable=False),
        pa.field('camera_make', pa.string()),
        pa.field('camera_model', pa.string()),
        pa.field('lens_model', pa.string()),
        pa.field('platepar_version', pa.string()),
        pa.field('fov_mask_geojson', pa.string()),
        pa.field('comment', pa.string()),
    ],
)


VIDEOS = Schema(
    table='videos',
    fields=[
        pa.field('video_id', pa.string(), nullable=False),
        pa.field('station_id', pa.string(), nullable=False),
        pa.field('start_ts_utc', _UTC, nullable=False),
        pa.field('end_ts_utc', _UTC, nullable=False),
        pa.field('n_frames', pa.int32(), nullable=False),
        pa.field('frame_step_s', pa.float32(), nullable=False),
        pa.field('n_flights_associated', pa.int32()),
        pa.field('n_detection_events', pa.int64()),
        pa.field('n_trajectory_points', pa.int64()),
        pa.field('gt_status', pa.string()),  # 'none' | 'wip' | 'complete'
        pa.field('gt_last_edit_ts_utc', _UTC),
        pa.field('gt_edit_count', pa.int32()),
        pa.field('pipeline_version', pa.string()),
        pa.field('pipeline_commit_sha', pa.string()),
        pa.field('cleaning_version', pa.string()),
    ],
)


TRAJECTORIES = Schema(
    table='trajectories',
    fields=[
        pa.field('video_id', pa.string(), nullable=False),
        pa.field('flight_id', pa.string(), nullable=False),
        pa.field('ts_utc', _UTC, nullable=False),
        pa.field('lat', pa.float64(), nullable=False),
        pa.field('lon', pa.float64(), nullable=False),
        pa.field('alt_baro_m', pa.float32()),
        pa.field('alt_geom_m', pa.float32()),
        pa.field('alt_gnss_m', pa.float32()),
        pa.field('sampling_interval_s', pa.float32()),
        pa.field('range_to_camera_km', pa.float32()),
        pa.field('flight_number', pa.string()),
        pa.field('callsign', pa.string()),
        pa.field('tail_number', pa.string()),
        pa.field('icao_address', pa.string()),
        pa.field('aircraft_type_icao', pa.string()),
        pa.field('dep_apt_icao', pa.string()),
        pa.field('arr_apt_icao', pa.string()),
        pa.field('source', pa.string()),
    ],
)


TRAJECTORY_POINT_OBSERVATIONS = Schema(
    table='trajectory_point_observations',
    fields=[
        pa.field('video_id', pa.string(), nullable=False),
        pa.field('flight_id', pa.string(), nullable=False),
        pa.field('emit_ts_utc', _UTC, nullable=False),
        pa.field('emit_lat', pa.float64()),
        pa.field('emit_lon', pa.float64()),
        pa.field('emit_alt_baro_m', pa.float32()),
        pa.field('emit_alt_geom_m', pa.float32()),
        pa.field('in_fov_first_age_s', pa.float32()),
        pa.field('in_fov_last_age_s', pa.float32()),
        pa.field('n_in_fov_events', pa.int32()),
        pa.field('detect_first_age_s', pa.float32()),
        pa.field('detect_last_age_s', pa.float32()),
        pa.field('n_detection_events', pa.int32()),
        pa.field('detection_rate', pa.float32()),
        pa.field('censored_fov_exit', pa.bool_()),
        pa.field('censored_video_end', pa.bool_()),
        pa.field('source', pa.string()),
        pa.field('pipeline_confidence_score', pa.float32()),
        pa.field('wind_correction_w_perp_ms', pa.float32()),
    ],
)


DETECTION_EVENTS = Schema(
    table='detection_events',
    fields=[
        pa.field('video_id', pa.string(), nullable=False),
        pa.field('flight_id', pa.string(), nullable=False),
        pa.field('emit_ts_utc', _UTC, nullable=False),
        pa.field('observed_ts_utc', _UTC, nullable=False),
        pa.field('age_s', pa.float32(), nullable=False),
        pa.field('frame_idx', pa.int32(), nullable=False),
        pa.field('mask_oid', pa.int64(), nullable=False),
        pa.field('mask_centroid_x_px', pa.float32()),
        pa.field('mask_centroid_y_px', pa.float32()),
        pa.field('mask_area_px', pa.int32()),
        pa.field('mask_yolo_confidence', pa.float32()),
        pa.field('trajectory_point_x_px', pa.float32()),
        pa.field('trajectory_point_y_px', pa.float32()),
        pa.field('perp_dist_px', pa.float32()),
        pa.field('pipeline_dist_to_polyline_m', pa.float32()),
        pa.field('pipeline_mask_thresh_m', pa.float32()),
        pa.field('pipeline_angle_deg', pa.float32()),
        pa.field('source', pa.string()),
    ],
)


MASK_POLYGONS = Schema(
    table='mask_polygons',
    fields=[
        pa.field('video_id', pa.string(), nullable=False),
        pa.field('mask_oid', pa.int64(), nullable=False),
        pa.field('frame_idx', pa.int32(), nullable=False),
        pa.field('observed_ts_utc', _UTC, nullable=False),
        pa.field('yolo_confidence', pa.float32()),
        pa.field('polygon_wkt', pa.string(), nullable=False),
        pa.field('polygon_area_px', pa.int32()),
        pa.field('polygon_n_vertices', pa.int32()),
        pa.field('pipeline_assigned_flight_id', pa.string()),
        pa.field('gt_assigned_flight_id', pa.string()),
        pa.field('is_artifact_gt', pa.bool_()),
    ],
)


ALL_TABLES: Dict[str, Schema] = {
    s.table: s for s in (STATIONS, VIDEOS, TRAJECTORIES,
                         TRAJECTORY_POINT_OBSERVATIONS, DETECTION_EVENTS,
                         MASK_POLYGONS)
}


def coerce_to_arrow(df, table_name: str) -> pa.Table:
    """Coerce a pandas DataFrame to an Arrow Table conforming to the named
    schema. Columns absent from the DataFrame are filled with null; columns
    present in the DataFrame but not in the schema are dropped with a
    warning (forward-compatible: build can pass extras safely)."""
    schema = ALL_TABLES[table_name]
    cols = {f.name: None for f in schema.fields}
    extras = []
    for col in df.columns:
        if col in cols:
            cols[col] = df[col]
        else:
            extras.append(col)
    if extras:
        import warnings
        warnings.warn(
            f"[cosc.schema] table '{table_name}' got unknown columns "
            f"{extras!r}; they will be dropped from the parquet."
        )
    # Build the Arrow table column-by-column to apply per-column types.
    arrays = []
    for f in schema.fields:
        s = cols[f.name]
        if s is None:
            arrays.append(pa.nulls(len(df), type=f.type))
        else:
            try:
                # Cast string-typed schema columns from whatever pandas gave us.
                # ADS-B sources like Spire can hand back icao_address as int64
                # (24-bit number) when we want the hex string — coerce to str.
                if pa.types.is_string(f.type):
                    s = s.astype('string').where(s.notna(), None)
                arrays.append(pa.array(s, type=f.type, from_pandas=True))
            except Exception as e:
                raise type(e)(
                    f"column '{f.name}' (expected {f.type}) coercion failed: "
                    f"got dtype={getattr(s, 'dtype', type(s).__name__)}; "
                    f"first values={list(s.head(3)) if hasattr(s, 'head') else 'n/a'}; "
                    f"original error: {e}"
                ) from e
    return pa.Table.from_arrays(arrays, schema=schema.arrow_schema)
