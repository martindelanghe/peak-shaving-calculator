#!/usr/bin/env python3
"""Fetch companion 15-minute temperature CSVs from Open-Meteo (ERA5).

Open-Meteo's historical archive provides hourly temperature_2m. This script
downloads hourly values for each site location, linearly interpolates to a
15-minute UTC grid for 2012, and writes one CSV per site:

    <out-dir>/<site_id>.csv  with columns dttm_utc, temperature_c
"""

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

OPEN_METEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
DEFAULT_START = "2012-01-01"
DEFAULT_END = "2012-12-31"


def load_site_locations(meta_path: Path) -> dict:
    """Map site ID to lat, lng, and timezone from the metadata CSV."""
    meta = pd.read_csv(meta_path)
    out = {}
    for _, row in meta.iterrows():
        out[str(row["SITE_ID"])] = {
            "lat": float(row["LAT"]),
            "lng": float(row["LNG"]),
            "time_zone": row["TIME_ZONE"],
        }
    return out


def fetch_hourly(lat: float, lng: float, start: str, end: str) -> pd.Series:
    """Download hourly temperature_2m (°C) from Open-Meteo ERA5 archive."""
    params = urllib.parse.urlencode(
        {
            "latitude": lat,
            "longitude": lng,
            "start_date": start,
            "end_date": end,
            "hourly": "temperature_2m",
            "timezone": "UTC",
        }
    )
    url = f"{OPEN_METEO_ARCHIVE}?{params}"
    with urllib.request.urlopen(url, timeout=120) as resp:
        payload = json.load(resp)
    hourly = payload["hourly"]
    idx = pd.to_datetime(hourly["time"], utc=True)
    return pd.Series(hourly["temperature_2m"], index=idx, name="temperature_c", dtype=float)


def hourly_to_15min(hourly: pd.Series, grid_start: str, grid_end: str) -> pd.Series:
    """Linearly interpolate hourly ERA5 temperatures onto a 15-minute UTC grid."""
    grid_end_ts = pd.Timestamp(f"{grid_end} 23:45:00", tz="UTC")
    idx = pd.date_range(
        pd.Timestamp(f"{grid_start} 00:00:00", tz="UTC"),
        grid_end_ts,
        freq="15min",
    )
    hourly = hourly.sort_index()
    combined = hourly.reindex(hourly.index.union(idx)).sort_index()
    interpolated = combined.interpolate(method="time")
    return interpolated.reindex(idx)


def write_site_csv(site_id: str, series: pd.Series, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(
        {
            "dttm_utc": series.index.strftime("%Y-%m-%d %H:%M:%S"),
            "temperature_c": series.round(2),
        }
    )
    df.to_csv(out_dir / f"{site_id}.csv", index=False)


def site_sort_key(site_id: str):
    return (0, int(site_id)) if site_id.isdigit() else (1, site_id)


def main():
    ap = argparse.ArgumentParser(
        description="Fetch Open-Meteo ERA5 temperatures at 15-minute intervals"
    )
    ap.add_argument(
        "--meta",
        default="csv-only/meta/all_sites.csv",
        type=Path,
        help="Site metadata CSV with LAT, LNG columns",
    )
    ap.add_argument(
        "--out",
        default="csv-only/weather",
        type=Path,
        help="Output directory for per-site temperature CSVs",
    )
    ap.add_argument(
        "--start",
        default=DEFAULT_START,
        help="First date to include (YYYY-MM-DD)",
    )
    ap.add_argument(
        "--end",
        default=DEFAULT_END,
        help="Last date to include (YYYY-MM-DD)",
    )
    ap.add_argument(
        "--sites",
        nargs="*",
        default=None,
        help="Optional subset of site IDs to fetch",
    )
    ap.add_argument(
        "--delay",
        type=float,
        default=0.2,
        help="Seconds to wait between API requests",
    )
    args = ap.parse_args()

    # Fetch one extra day so the last 15-minute bins can interpolate cleanly.
    fetch_end = (pd.Timestamp(args.end) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")

    sites = load_site_locations(args.meta)
    site_ids = sorted(sites.keys(), key=site_sort_key)
    if args.sites:
        wanted = set(args.sites)
        site_ids = [sid for sid in site_ids if sid in wanted]

    for i, site_id in enumerate(site_ids, start=1):
        info = sites[site_id]
        print(
            f"[{i}/{len(site_ids)}] site {site_id} "
            f"({info['lat']:.4f}, {info['lng']:.4f})"
        )
        try:
            hourly = fetch_hourly(info["lat"], info["lng"], args.start, fetch_end)
            s15 = hourly_to_15min(hourly, args.start, args.end)
            write_site_csv(site_id, s15, args.out)
            print(f"  wrote {len(s15)} rows -> {args.out / f'{site_id}.csv'}")
        except urllib.error.URLError as err:
            print(f"  ERROR fetching site {site_id}: {err}")
        if i < len(site_ids) and args.delay > 0:
            time.sleep(args.delay)

    print(f"done ({len(site_ids)} sites)")


if __name__ == "__main__":
    main()
