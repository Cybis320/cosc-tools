# COSC v0.1 — Schema

**Contrail Observations from Sky Cameras**, version 0.1.

This document is the normative reference for the v0.1 dataset layout. All
columns, types, and units below are part of the public contract; later v0.X
releases may add columns but will not remove or repurpose existing ones.

Status: **draft for review**.

## File layout

```
cosc-v0.1/
├── stations/                         # Hive-partitioned parquet
│   └── country=NL/station=NL000C/stations.parquet
├── videos/
│   └── country=NL/station=NL000C/date=2025-10-01/videos.parquet
├── trajectories/
│   └── country=NL/station=NL000C/date=2025-10-01/trajectories.parquet
├── trajectory_point_observations/
│   └── country=NL/station=NL000C/date=2025-10-01/observations.parquet
├── detection_events/
│   └── country=NL/station=NL000C/date=2025-10-01/events.parquet
├── mask_polygons/
│   └── country=NL/station=NL000C/date=2025-10-01/polygons.parquet
├── DATASET_CARD.md
├── SCHEMA.md                         (this file)
├── LICENSE                           (CC-BY-NC-4.0; commercial use requires separate agreement)
└── manifest.json                     (top-level metadata: version, pipeline_commit, build_ts, row counts per table)
```

Conventions:
- All timestamps `ts_utc` are stored as `timestamp[ns, UTC]` (Arrow logical type).
- All coordinates are WGS-84 decimal degrees.
- All distances/altitudes are SI metres unless suffixed `_px` (image pixels) or `_ft` (feet, ADS-B native).
- All durations are seconds (suffixed `_s`).
- All angles are degrees (suffixed `_deg`) unless suffixed `_rad`.
- Missing data: parquet `null`. No sentinel values.

---

## Table 1 — `stations/`

One row per camera station. Static per release; rarely changes.

| Column | Type | Unit | Description |
|---|---|---|---|
| `station_id` | string | — | Canonical station identifier (e.g. `NL000C`). **Primary key.** |
| `country` | string | — | ISO 3166-1 alpha-2 country code (e.g. `NL`, `US`, `CA`, `FR`). |
| `station_name` | string | — | Human-readable name, if any. |
| `lat` | float64 | ° | Station latitude (WGS-84). |
| `lon` | float64 | ° | Station longitude (WGS-84). |
| `alt_m` | float64 | m | Station altitude above MSL. |
| `tz` | string | — | IANA timezone (e.g. `Europe/Amsterdam`). Informational; all data is UTC. |
| `image_w_px` | int32 | px | Image width in pixels. |
| `image_h_px` | int32 | px | Image height in pixels. |
| `camera_make`, `camera_model`, `lens_model` | string | — | Hardware metadata (nullable). |
| `platepar_version` | string | — | Calibration version identifier. Same platepar across all videos in a release. |
| `fov_mask_geojson` | string (GeoJSON Polygon) | — | Pixel-space polygon describing the camera's effective image circle / horizon mask. |
| `comment` | string | — | Free-text notes (e.g. "MMF tanker corridor; high traffic density"). |

---

## Table 2 — `videos/`

One row per video processed.

| Column | Type | Unit | Description |
|---|---|---|---|
| `video_id` | string | — | Canonical video identifier `<station>_<YYYYMMDD>_<HHMMSS>_to_<YYYYMMDD>_<HHMMSS>`. **Primary key.** |
| `station_id` | string | — | Foreign key → `stations.station_id`. |
| `start_ts_utc` | timestamp[ns, UTC] | — | First frame timestamp. |
| `end_ts_utc` | timestamp[ns, UTC] | — | Last frame timestamp. |
| `n_frames` | int32 | — | Frames in the source video. |
| `frame_step_s` | float32 | s | Nominal interval between consecutive frames (often 5 s). |
| `n_flights_associated` | int32 | — | Flights with ≥1 mask assigned by the pipeline. |
| `n_detection_events` | int64 | — | Total rows in `detection_events/` for this video. |
| `n_trajectory_points` | int64 | — | Total rows in `trajectories/` for this video. |
| `gt_status` | string | — | `none` / `wip` / `complete`. Reflects whether a human-validated ground truth exists for this video. |
| `gt_last_edit_ts_utc` | timestamp | — | When the GT was last modified, if any. |
| `gt_edit_count` | int32 | — | Number of editor-recorded edits on this video. |
| `pipeline_version` | string | — | Semver of the upstream Janus pipeline. |
| `pipeline_commit_sha` | string | — | Git SHA of the Janus pipeline at build time. |
| `cleaning_version` | string | — | Version of the ADS-B cleaning spec (matches `DATASET_CARD.md` section). |

---

## Table 3 — `trajectories/`

One row per cleaned ADS-B trajectory point per flight per video. Sampling cadence is **range-adaptive** (denser closer to the camera). See `DATASET_CARD.md` for the resampling spec.

| Column | Type | Unit | Description |
|---|---|---|---|
| `video_id` | string | — | Foreign key → `videos.video_id`. |
| `flight_id` | string | — | Spire-assigned flight identifier (UUID), passed through unchanged from the Contrails API. **Stable across re-fetches of the same flight from Spire.** For researchers comparing against non-Spire ADS-B sources, the fallback bridge key is `(icao_address, ts_utc)`. |
| `ts_utc` | timestamp[ns, UTC] | — | Sample time. Together with `flight_id` forms a candidate composite key (not unique if cleaning introduced gap-fill bridges at duplicate times — unique within a single flight by construction). |
| `lat` | float64 | ° | Cleaned latitude (WGS-84). |
| `lon` | float64 | ° | Cleaned longitude (WGS-84). |
| `alt_baro_m` | float32 | m | Cleaned barometric altitude. |
| `alt_geom_m` | float32 | m | Geometric altitude from ERA5 baro→geom inversion (may be null if ERA5 coverage was insufficient). |
| `alt_gnss_m` | float32 | m | GNSS-reported altitude (often filled from `geom_m` when source missing — see cleaning spec). |
| `sampling_interval_s` | float32 | s | Time interval to the previous sample for this flight. Null on the first row. Enables consumers to recover the variable cadence honestly without resampling. |
| `range_to_camera_km` | float32 | km | Great-circle distance to the camera at this sample (informational; helps explain `sampling_interval_s`). |
| `flight_number` | string | — | IATA/ICAO scheduled flight number (e.g. `KL1903`). May be null for non-commercial flights. |
| `callsign` | string | — | ATC callsign (e.g. `KLM1903`). May be null. |
| `tail_number` | string | — | Aircraft registration (e.g. `PH-BGA`). Included in the clear per dataset card. |
| `icao_address` | string | — | 24-bit ICAO Mode-S address (e.g. `4841A2`). |
| `aircraft_type_icao` | string | — | ICAO aircraft type designator (e.g. `B738`, `A320`). |
| `dep_apt_icao` | string | — | Departure airport ICAO code. May be null. |
| `arr_apt_icao` | string | — | Arrival airport ICAO code. May be null. |
| `source` | string | — | Originating ADS-B provider (e.g. `spire`). |

---

## Table 4 — `trajectory_point_observations/` *(headline table)*

One row per `(video_id, flight_id, ts_utc)` trajectory point that produced **≥1 detection** over the video. Aggregated summary intended for model-vs-observation comparisons.

| Column | Type | Unit | Description |
|---|---|---|---|
| `video_id` | string | — | Foreign key. |
| `flight_id` | string | — | Foreign key. |
| `emit_ts_utc` | timestamp[ns, UTC] | — | Emission time of this trajectory point. **Primary key** with `(video_id, flight_id, emit_ts_utc)`. |
| `emit_lat`, `emit_lon` | float64 | ° | Position at emission (from `trajectories/`). |
| `emit_alt_baro_m`, `emit_alt_geom_m` | float32 | m | Altitude at emission. |
| `in_fov_first_age_s` | float32 | s | First contrail age (since emission) at which this point's advected position was inside the camera FOV. |
| `in_fov_last_age_s` | float32 | s | Last age inside FOV. |
| `n_in_fov_events` | int32 | — | Total frames where this point's advected position was inside the FOV (any detection or not). |
| `detect_first_age_s` | float32 | s | First age at which this point was attributed to a YOLO mask. |
| `detect_last_age_s` | float32 | s | Last attributed age. |
| `n_detection_events` | int32 | — | Total `(frame, mask)` attributions for this trajectory point. |
| `detection_rate` | float32 | — | `n_detection_events / n_in_fov_events`. Detector-loss proxy (1.0 = always detected when observable). |
| `censored_fov_exit` | bool | — | True iff `detect_last_age_s ≥ in_fov_last_age_s − frame_step_s`. Contrail may have lived longer; observation cut off by FOV exit. |
| `censored_video_end` | bool | — | True iff the trajectory point's contrail was still in-FOV at the last video frame. |

A future revision will add `censored_obstructed` for cases where the contrail is occluded by cloud, ground object, or detector dropout within an in-FOV window. v0.1 does not distinguish those from genuine dissipation.
| `source` | string | — | `gt` if the underlying flight assignments come from a complete-status ground truth; otherwise `pipeline`. |
| `pipeline_confidence_score` | float32 | — | Pipeline's per-flight confidence (informational). |
| `wind_correction_w_perp_ms` | float32 | m/s | Per-flight perpendicular wind-correction the pipeline applied to align the trace to its masks. |

### Censoring guidance

A row with `censored_fov_exit = True` is **right-censored**: its `detect_last_age_s` is a lower bound on contrail lifetime, not the true lifetime. Survival-analysis methods (Kaplan-Meier, Cox) handle this directly.

---

## Table 5 — `detection_events/` *(long format)*

One row per `(video_id, flight_id, emit_ts_utc, observed_ts_utc, mask_oid)` attribution. The atomic observation record.

**v0.1 contains contest winners only** (one attribution per `(video_id, mask_oid)`). A future schema revision will add a `confidence_category` column — values like `winner` / `cant_tell` / `too_close_to_tell` — and include the contest near-misses.

| Column | Type | Unit | Description |
|---|---|---|---|
| `video_id` | string | — | Foreign key. |
| `flight_id` | string | — | Foreign key. |
| `emit_ts_utc` | timestamp[ns, UTC] | — | When this contrail was emitted (matches `trajectory_point_observations.emit_ts_utc`). |
| `observed_ts_utc` | timestamp[ns, UTC] | — | Frame timestamp at which the contrail was detected. |
| `age_s` | float32 | s | `observed_ts_utc − emit_ts_utc`. |
| `frame_idx` | int32 | — | Video frame index. |
| `mask_oid` | int64 | — | Pipeline-assigned mask object_id (foreign key → `mask_polygons.mask_oid`). |
| `mask_centroid_x_px`, `mask_centroid_y_px` | float32 | px | Mask centroid in image space (top-left origin). |
| `mask_area_px` | int32 | px² | Mask polygon area in pixels. |
| `mask_yolo_confidence` | float32 | — | YOLO confidence for this mask (from `polygons.json`). |
| `trajectory_point_x_px`, `trajectory_point_y_px` | float32 | px | Image-space position of this point's advected contrail at `observed_ts_utc`. |
| `perp_dist_px` | float32 | px | Perpendicular distance from the projected trajectory point to its closest mask boundary point. Captures the wind-error magnitude (≥0). |
| `pipeline_dist_to_polyline_m` | float32 | m | Pipeline's mesh-association distance from the mask raster to the flight polyline. |
| `pipeline_mask_thresh_m` | float32 | m | Pipeline's adaptive inlier threshold at this mask (range/age-scaled). |
| `pipeline_angle_deg` | float32 | ° | PCA-axis angle between mask long-axis and polyline tangent. |
| `source` | string | — | `gt` / `pipeline`. |

---

## Table 6 — `mask_polygons/`

One row per detected mask polygon. Optional for analysis; needed for visual reconstruction.

| Column | Type | Unit | Description |
|---|---|---|---|
| `video_id` | string | — | Foreign key. |
| `mask_oid` | int64 | — | Mask object identifier (encodes frame + intra-frame index per `Loaders.py`). **Primary key with `video_id`.** |
| `frame_idx` | int32 | — | Video frame. |
| `observed_ts_utc` | timestamp[ns, UTC] | — | Frame timestamp. |
| `yolo_confidence` | float32 | — | YOLO detection confidence. |
| `polygon_wkt` | string (WKT) | — | Mask polygon geometry in WKT (Well-Known Text) format, e.g. `POLYGON ((x1 y1, x2 y2, ...))`. Single-contour polygons only; multi-contour stored as `MULTIPOLYGON`. |
| `polygon_area_px` | int32 | px² | Polygon area. |
| `polygon_n_vertices` | int32 | — | Vertex count (post-pipeline simplification, if any). |
| `pipeline_assigned_flight_id` | string | — | Foreign key → `trajectories.flight_id` (the contest winner). Null if unassigned. |
| `gt_assigned_flight_id` | string | — | Ground-truth-corrected assignment, if available; matches `pipeline_assigned_flight_id` where the human did not edit. Null where GT marked the mask as an artifact/unassociated. |
| `is_artifact_gt` | bool | — | True if GT flagged this mask as a non-contrail artifact (lens flare, cloud, etc.). |

---

## Joins

- `trajectory_point_observations ⨝ trajectories` on `(video_id, flight_id, ts_utc == emit_ts_utc)`
- `detection_events ⨝ trajectory_point_observations` on `(video_id, flight_id, emit_ts_utc)`
- `detection_events ⨝ mask_polygons` on `(video_id, mask_oid)`
- `videos ⨝ stations` on `station_id`

---

## Versioning

`vMAJOR.MINOR`:
- **MAJOR**: breaking schema changes (rename, remove, retype, repurpose).
- **MINOR**: additive (new columns, new stations, new videos).

This document describes **v0.1**. The version is recorded in `manifest.json` and on each parquet's metadata header.
