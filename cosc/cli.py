"""`python -m cosc.cli build` — build COSC parquets from a staging dir.

Minimal CLI for v0.1. Multi-video / batch is left to a wrapper script.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from cosc.build import build_video


# Station metadata that the build needs (lat/lon/country/...).
#
# Privacy convention: station coordinates published in the dataset are
# rounded to 2 decimal places (~1.1 km precision) — sufficient for
# atmospheric / model comparison, deliberately coarse enough that the
# camera installation site is not pinpointed. The internal platepar
# retains full precision for the producer to project trajectories
# correctly; only the rounded values reach the parquet.
#
# For v0.1 this is hand-curated; v0.2+ should pull from a shared
# station registry.
STATION_META = {
    'NL000C': {
        'country': 'NL',
        'station_name': 'NL000C',
        'lat': 51.92,    # rounded from platepar lat=51.91954
        'lon': 5.83,     # rounded from platepar lon=5.82718
        'alt_m': 39.0,
        'tz': 'Europe/Amsterdam',
        'comment': 'High-traffic Schiphol approach corridor; NATO MMF tanker loiter zone',
    },
    'NL000Q': {
        'country': 'NL',
        'station_name': 'NL000Q',
        'lat': 52.80,    # rounded from platepar lat=52.79544
        'lon': 5.95,     # rounded from platepar lon=5.95459
        'alt_m': 4.0,
        'tz': 'Europe/Amsterdam',
        'comment': 'Dutch farmland sky camera; Schiphol approach + MMF MRTT loiter corridor',
    },
    # Add more as we expand.
}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='cmd', required=True)

    pb = sub.add_parser('build', help='Build COSC parquets for one video.')
    pb.add_argument('staging_dir', type=Path,
                    help='Janus staging directory (e.g. '
                         '/datapool/advect_staging/NL/NL000C/...).')
    pb.add_argument('--out_root', type=Path, default=Path('/tmp/cosc-v0.1'),
                    help='COSC output root (default: /tmp/cosc-v0.1).')

    args = p.parse_args()

    if args.cmd == 'build':
        manifest = build_video(args.staging_dir, args.out_root,
                               station_meta=STATION_META)
        manifest_path = args.out_root / 'manifest.json'
        existing = []
        if manifest_path.exists():
            try:
                existing = json.loads(manifest_path.read_text())
            except Exception:
                existing = []
        if not isinstance(existing, list):
            existing = [existing]
        existing = [m for m in existing if m.get('video_id') != manifest['video_id']]
        existing.append(manifest)
        manifest_path.write_text(json.dumps(existing, indent=2, default=str))
        print(f"\n[manifest] wrote {manifest_path}")
        print(json.dumps(manifest, indent=2, default=str))


if __name__ == '__main__':
    main()
