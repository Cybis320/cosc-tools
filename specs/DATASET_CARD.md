# COSC v0.1 — Dataset Card

**Contrail Observations from Sky Cameras**, version 0.1.

Status: **draft for review.**

## Summary

A research dataset of contrail observations from ground-based sky cameras,
paired with per-trajectory-point empirical lifetimes. Built to let researchers
benchmark contrail-evolution models (CoCiP, RFM, custom) against detected-from-
the-ground reality, with explicit censoring metadata so survival analysis is
honest.

- **Scope (v0.1)**: 1 station (NL000C, Netherlands), 1 video (2025-10-01,
  13 h), 123 associated flights, ~14k mask polygons, ~37k attribution events.
- **Format**: Apache Parquet, partitioned `country=*/station=*/date=*`.
- **License**: CC-BY-NC-4.0 (research / non-commercial use). Commercial use requires a separate written agreement — see the **License** section below.

## Citation

```
Busquin, L. (2026). COSC v0.1: Contrail Observations from Sky Cameras.
[DOI to be assigned at v0.1.0 tagged release.]
```

## License

Released under [Creative Commons Attribution-NonCommercial 4.0 International (CC-BY-NC-4.0)](https://creativecommons.org/licenses/by-nc/4.0/) for research and non-commercial use.

**You may**, with attribution:
- Use the data in academic research, theses, and publications.
- Share and adapt the dataset for non-commercial purposes.
- Combine with other CC-BY-NC-compatible datasets.
- Cite results in conference / journal papers, including industry-affiliated authors, provided the work is not primarily directed toward commercial advantage.

**You may not**, without a separate written agreement with the dataset maintainer:
- Sell the dataset or any substantial derivative of it.
- Build a commercial product, paid service, or revenue-generating tool that depends on the dataset (or on a model trained against it).
- Use the dataset in support of a commercial flight-operations / fuel-optimisation / contrail-avoidance decision system intended for paid deployment.

Contact for commercial-use agreements: **Luc Busquin** &lt;[luc.busquin@contrailcast.com](mailto:luc.busquin@contrailcast.com)&gt;.

Full license text: `LICENSE` (CC-BY-NC-4.0 verbatim).

### Attribution

When citing the dataset in a paper or derivative work, include:

```
COSC v0.1 (Contrail Observations from Sky Cameras). [DOI TBD]. [Year].
CC-BY-NC-4.0.
```

## Data hosting

> ⚠️ **No public download URL yet.** v0.1 is being prepared; the schema and
> producer in this repo are stable enough to discuss and audit, but the
> built parquet artefacts aren't online yet.
>
> When v0.1.0 is tagged, the dataset will be hosted at:
> - **Bucket** (rolling, Hive-partitioned): TBD (likely an S3-compatible
>   public-read object store such as Cloudflare R2 or AWS S3).
> - **Snapshot release** (immutable, DOI-citable): Zenodo and / or Hugging
>   Face Datasets, mirroring the bucket contents at the tagged version.
>
> Until then, researchers interested in early access can build the dataset
> locally from a Janus pipeline output directory (see the *Quickstart*
> section of the [repo README](../README.md)), or contact the maintainer.

### Operating scale

This dataset is designed to grow rolling 24/7 across many camera stations.
Order-of-magnitude per-day footprint per station ≈ 5-20 MB compressed
parquet (depends on traffic density and contrail prevalence); at the
~thousand-stations target deployment that's a few TB/year. The Hive-
partitioned layout (`country=/station=/date=`) is chosen so consumers can
pull just the slice they need (a country, a station, a month) without
materialising the whole dataset.

## What's in the dataset

Six parquet tables. The full column-level contract is in `SCHEMA.md`; here's
the conceptual map:

| Table | Grain | Purpose |
|---|---|---|
| `stations/` | per station | Camera calibration metadata. |
| `videos/` | per video | Video bounds, pipeline / GT provenance. |
| `trajectories/` | per (flight, time) | Cleaned ADS-B trajectory points. |
| `trajectory_point_observations/` | per (flight, emission time) | Per-emission summary: when observed, how long, censoring. |
| `detection_events/` | per (flight, mask, frame) | Atomic attribution rows for time-resolved comparison. |
| `mask_polygons/` | per mask polygon | Raw YOLO mask geometry. |

## How to compare a model

1. Run your contrail-evolution model on each `(flight_id, emit_ts_utc, emit_lat,
   emit_lon, emit_alt_geom_m)` row in `trajectory_point_observations/`.
2. Compare your predicted dissipation age to `detect_last_age_s`, handling
   `censored_fov_exit = True` rows as right-censored.
3. For higher-resolution comparison, use `detection_events/` to compare your
   model's per-time predicted opacity/visibility to the empirical detection
   density over the observed window.

## ADS-B cleaning specification (`cleaning_version = v0.1`)

**This section describes how Spire ADS-B messages are cleaned before becoming
the `trajectories/` table.** The pipeline applies the steps below in order.
Parameter defaults are listed; per-build values are recorded in `manifest.json`.

> ⚠️ **Draft.** This spec is being collated from the upstream pipeline code
> and operator notes. The published v0.1.0 will replace this draft with the
> definitive spec; expect minor additions in the validation-step list before
> release.

### Stage 1 — Source-side dedup and column normalisation

- Drop messages with `altitude_baro == altitude_gnss` (Spire data-quality
  sentinel — indicates barometric altitude unreported and replaced by GNSS).
- Convert all altitude columns from feet → metres (factor 0.3048).
- Normalise timestamps to UTC.
- Sort each flight chronologically (stable mergesort, preserves order at
  equal timestamps).
- Drop pycontrails-defined duplicate timestamps inside each flight.

### Stage 2 — Altitude band filter

Drop trajectory points outside `[min_alt_ft = 20000, max_alt_ft = 51000]`
(FL200-FL510). Eliminates climb-out / descent / GA traffic that doesn't
produce persistent contrails.

### Stage 3 — Geometric altitude (ERA5)

For each surviving message, compute geometric altitude `geom_m` from
`altitude_baro` using ERA5 pressure profiles at the message's
`(lat, lon, time)`. Pressure levels considered:
`(500, 450, 400, 350, 300, 250, 225, 200, 175, 150, 125, 100)` hPa.

This produces a column `geom_m` alongside `altitude_baro` / `altitude_gnss`.

### Stage 4 — Geometric-vs-GNSS sanity check

- Fill missing `altitude_gnss` from `geom_m` where ERA5 returned a value.
- Drop rows where `|geom_m − altitude_gnss| > QC_THRESH_M = 300 m` (ERA5
  baro-to-geom and GNSS-reported altitudes should agree to within atmospheric
  variability; larger deviations indicate corrupted readings).
- Hard-fail the video if ALL rows have `geom_m = null` (incomplete ERA5
  coverage); log a warning if some rows have it.

### Stage 5 — Catchment filter

Drop entire flights whose **closest** position to the camera is farther than
`max_adv_wind_speed × max_contrail_age × 3600 = 180 km` (defaults
100 m/s × 0.5 h). At larger ranges no contrail produced by the flight could
realistically advect into the camera's FOV.

### Stage 6 — Range-adaptive resampling

For each retained flight, generate an irregular time grid where the interval
is `range_km × fine_range_scale_factor`, clamped to
`[range_interval_min = 5 s, range_interval_max = 60 s]`. Sampling is therefore
denser for flights close to the camera and sparser for distant ones (`5 s` at
≤10 km from camera, `60 s` at ≥120 km). The per-point interval is preserved
as `sampling_interval_s` in `trajectories/`.

### Stage 7 — Interpolation onto the resampling grid

Per flight and per column (`lat`, `lon`, `alt_baro_m`, `alt_geom_m`,
`alt_gnss_m`):

- ≥5 valid points → Akima spline interpolation
- 4 valid points → univariate spline (k=3, smoothing s=N·0.001)
- 2-3 valid points → linear

Categorical fields (`flight_number`, `callsign`, `tail_number`,
`aircraft_type_icao`, `icao_address`, `dep_apt_icao`, `arr_apt_icao`) are
carried forward from the first source waypoint (assumed constant per flight).

### Stage 8 — Minimum waypoints filter

Drop any flight that has fewer than `min_waypoints = 2` retained points after
all of the above.

### Stage 9 — Long-gap interpolation drop (default: OFF)

When enabled via `max_interp_gap_seconds`, interpolated rows whose underlying
ADS-B coverage gap exceeds the threshold are removed (avoids linear
interpolation across turns during Spire coverage outages). Default disabled
to maximise coverage; set to a positive value (e.g. 60 s) for stations with
known coverage gaps.

### Stage 10 — Pressure-range fallback

During downstream advection (not directly affecting `trajectories/`, but
relevant for understanding `alt_geom_m`), pressure values outside the
configured `pressure_levels` range fall back to the ISA pressure-to-altitude
formula instead of ERA5. This shouldn't affect cruise trajectories
(20-51 kft is solidly within the 100-500 hPa band).

---

## Conventions

- All timestamps are UTC. Sub-second precision preserved.
- Coordinates are WGS-84 decimal degrees.
- Distances and altitudes are SI metres, suffixed `_m` (or `_px` for image
  pixels, `_ft` for ADS-B-native feet).
- Aircraft identification (`tail_number`, `icao_address`) is **included
  in clear**. ADS-B is technically public, but consumers redistributing this
  dataset should be aware some jurisdictions or aircraft operators
  (military, blocked tails) may have separate considerations.
- Privacy/ethics: this dataset contains no personal data. Aircraft are
  operational platforms, not persons.

### Station-location privacy

Station coordinates in `stations.parquet` are **rounded to 2 decimal
places of arc** (~1.1 km horizontal resolution at temperate latitudes).
The producer's internal camera calibration retains full precision (it
needs it to project sky positions into the image), but only the rounded
coordinates reach the parquet — and `range_to_camera_km` in
`trajectories.parquet` is computed from those same rounded coordinates
so it doesn't carry sub-rounding precision either. This is to deter
casual lookup of equipment locations; it isn't intended to defeat
motivated geometric reverse-engineering.

## Known limitations

- **Single-station, single-video** in v0.1. Cross-station generalisation
  pending v0.2.
- **YOLO detector** is the source of all `mask_polygons/`. Detector misses
  thin/young contrails and over-fires on cirrus; researchers should treat
  detection rate as a *coupled* signal of contrail visibility AND detector
  recall.
- **`flight_id`** is the Spire-assigned identifier from the Contrails API,
  preserved unchanged. It is stable across re-fetches from Spire. For
  researchers comparing against non-Spire ADS-B sources, use
  `(icao_address, ts_utc)` as a bridge key.
- **Censoring** is handled for FOV exit and video end, but NOT for
  detector-loss within an in-FOV window. Researchers wanting full survival
  analysis should treat `detection_rate < 1.0` as a separate censoring class.
- **Range-adaptive trajectory cadence** means a 60 s gap in `trajectories/`
  for a distant flight is *expected*, not missing data. Interpolate using
  `sampling_interval_s` to recover the original spacing.
- **GT-as-truth** only for videos with `gt_status = complete`. For others,
  `source` columns will read `pipeline`.

## Provenance

Each row carries:

- `pipeline_version` + `pipeline_commit_sha` on `videos/`.
- `cleaning_version` on `videos/` and `manifest.json`.
- `source = gt | pipeline` on observation/event rows so researchers can
  filter to human-verified subsets.

The producer code (`cosc/build.py`) reads the upstream Janus pipeline's
output parquets / JSON for a single video and emits the COSC parquet set.
Reruns are stable given identical pipeline output + GT.

## Acknowledgements

ADS-B data: [Spire Aviation](https://spire.com/).
Meteorological data: ECMWF ERA5 reanalysis.
Contrail simulation: [pycontrails](https://github.com/contrailcirrus/pycontrails).
Camera calibration: RMS / RPiMeteorStation lineage.

## Contact

**Luc Busquin** &lt;[luc.busquin@contrailcast.com](mailto:luc.busquin@contrailcast.com)&gt;.
Repository: <https://github.com/Cybis320/cosc-tools>. DOI assigned at the v0.1.0 tagged release.
