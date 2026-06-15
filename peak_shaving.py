#!/usr/bin/env python3
"""Peak shaving opportunity analyzer.

Reads 5-minute energy consumption CSVs (one per customer site), finds the top
N peak windows (up to L contiguous 15-minute intervals each) per customer per
month, computes demand shaved and energy delivered against a floor, and writes
a JSON results file plus a self-contained HTML heatmap report.
"""

import argparse
import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd

INTERVAL = pd.Timedelta(minutes=15)
TIME_FMT = "%Y-%m-%d %H:%M"
TS_FMT = "%Y-%m-%dT%H:%M:%S"


def load_site_meta(meta_path: Path) -> dict:
    """Map each site ID to its timezone, industry, sub-industry, and area."""
    meta = pd.read_csv(meta_path)
    out = {}
    for _, row in meta.iterrows():
        sq_ft = row.get("SQ_FT")
        out[str(row["SITE_ID"])] = {
            "time_zone": row["TIME_ZONE"],
            "industry": row["INDUSTRY"],
            "sub_industry": row["SUB_INDUSTRY"],
            "sq_ft": None if pd.isna(sq_ft) else int(sq_ft),
        }
    return out


def load_raw(csv_path: Path) -> pd.DataFrame:
    """Load a site CSV as a UTC-indexed 5-minute frame.

    Returns a frame with `value` (anomalies set to NaN) and `estimated`
    (boolean) columns, sorted and de-duplicated on the timestamp index.
    """
    df = pd.read_csv(csv_path, dtype={"anomaly": str})
    values = df["value"].astype(float)
    anomaly = df["anomaly"].fillna("").str.strip() != ""
    values = values.mask(anomaly)
    estimated = pd.to_numeric(df["estimated"], errors="coerce").fillna(0) > 0
    idx = pd.to_datetime(df["dttm_utc"], utc=True)
    out = pd.DataFrame(
        {"value": values.to_numpy(), "estimated": estimated.to_numpy()},
        index=idx,
    ).sort_index()
    out = out[~out.index.duplicated(keep="first")]
    return out


def local_series_15min(raw: pd.DataFrame, tz: str) -> pd.Series:
    """5-minute UTC readings → 15-minute local-time energy series.

    A bin is valid only when all three 5-minute readings are present and
    non-NaN; min_count makes incomplete or gappy bins NaN.
    """
    s = pd.Series(raw["value"].to_numpy(), index=raw.index).tz_convert(tz)
    return s.resample("15min").sum(min_count=3)


def build_timeseries(raw: pd.DataFrame) -> list:
    """Build the UTC-stored 15-minute chart series from raw 5-minute readings.

    Returns a list of `[ts, kWh, estimated]` at 15-minute intervals.
    Timestamps are UTC strings `YYYY-MM-DDTHH:mm:ss` (no `Z` suffix). Bins
    that are incomplete (missing 5-minute readings) are dropped.
    """
    value = pd.Series(raw["value"].to_numpy(), index=raw.index)
    estimated = pd.Series(
        raw["estimated"].astype(int).to_numpy(), index=raw.index
    )
    r15 = value.resample("15min").sum(min_count=3)
    est15 = estimated.resample("15min").max()

    return [
        [ts.strftime(TS_FMT), round(float(v), 4), bool(est15.loc[ts])]
        for ts, v in r15.items()
        if pd.notna(v)
    ]


def analyze_month(e: np.ndarray, index: pd.DatetimeIndex, N: int, L: int):
    """Run the greedy peak-window selection on one customer-month.

    e: 15-minute energies (kWh), index: corresponding local start times.
    Returns the month result dict, or None if the month has no valid data.
    """
    n = len(e)
    valid = ~np.isnan(e)
    if not valid.any():
        return None

    # Indices sorted by energy descending, ties broken by earlier time.
    sort_keys = -np.where(valid, e, -np.inf)
    order = [int(i) for i in np.lexsort((np.arange(n), sort_keys)) if valid[i]]

    cumsum = np.concatenate([[0.0], np.cumsum(np.where(valid, e, 0.0))])
    claimed = np.zeros(n, dtype=bool)
    windows = []  # (start_idx, end_idx) inclusive, pre-trim
    pos = 0

    while len(windows) < N:
        while pos < len(order) and claimed[order[pos]]:
            pos += 1
        if pos >= len(order):
            break
        peak = order[pos]

        # Contiguous run of valid, unclaimed intervals containing the peak.
        lo = peak
        while lo - 1 >= 0 and valid[lo - 1] and not claimed[lo - 1]:
            lo -= 1
        hi = peak
        while hi + 1 < n and valid[hi + 1] and not claimed[hi + 1]:
            hi += 1

        # Best placement of a window of size min(L, run length) that contains
        # the peak: highest total energy, earliest start on ties.
        w = min(L, hi - lo + 1)
        best_a, best_sum = None, -np.inf
        for a in range(max(lo, peak - w + 1), min(peak, hi - w + 1) + 1):
            total = cumsum[a + w] - cumsum[a]
            if total > best_sum:
                best_sum, best_a = total, a
        start, end = best_a, best_a + w - 1
        claimed[start : end + 1] = True
        windows.append((start, end))

    # Floor: highest remaining unclaimed valid interval, 0 if none.
    while pos < len(order) and claimed[order[pos]]:
        pos += 1
    floor = float(e[order[pos]]) if pos < len(order) else 0.0

    pre_trim_ends = {b for _, b in windows}
    peaks_out = []
    total_delivered = 0.0
    for a0, b0 in sorted(windows):
        contiguous = (a0 - 1) in pre_trim_ends
        a, b = a0, b0
        while a <= b and e[a] < floor:
            a += 1
        while b >= a and e[b] < floor:
            b -= 1
        # The window always contains its peak interval, which is >= floor,
        # so it can never be fully trimmed away.
        delivered = float(np.clip(e[a : b + 1] - floor, 0.0, None).sum())
        total_delivered += delivered
        peaks_out.append(
            {
                "start": index[a].strftime(TIME_FMT),
                "end": (index[b] + INTERVAL).strftime(TIME_FMT),
                "delivered_energy_kwh": round(delivered, 3),
                "contiguous": contiguous,
            }
        )

    peak = float(np.nanmax(e))
    demand_shaved = (peak - floor) * 4.0  # kWh/15min -> kW
    # Demand shaved relative to the month's peak demand; the 4x factors cancel.
    demand_pct = (peak - floor) / peak * 100.0 if peak > 0 else 0.0
    # Energy delivered relative to the month's total energy (valid intervals).
    month_energy = float(np.nansum(e))
    energy_pct = total_delivered / month_energy * 100.0 if month_energy > 0 else 0.0
    return {
        "month": index[0].strftime("%Y-%m"),
        "total_demand_shaved_kw": round(demand_shaved, 3),
        "total_energy_delivered_kwh": round(total_delivered, 3),
        "total_demand_shaved_pct": round(demand_pct, 4),
        "total_energy_delivered_pct": round(energy_pct, 4),
        "peaks": peaks_out,
    }


def process_customer(raw: pd.DataFrame, tz: str, N: int, L: int) -> list:
    s = local_series_15min(raw, tz)
    # Drop a leading partial month (e.g. the Dec 2011 spillover created by
    # converting UTC-aligned 2012 data to local time).
    if len(s):
        first = s.index[0]
        if not (first.day == 1 and first.hour == 0 and first.minute == 0):
            cutoff = (first + pd.offsets.MonthBegin(1)).normalize()
            s = s[s.index >= cutoff]
    month_key = s.index.year * 100 + s.index.month
    months = []
    for _, sub in s.groupby(month_key):
        result = analyze_month(sub.to_numpy(), sub.index, N, L)
        if result is not None:
            months.append(result)
    return months


def build_report(template_path: Path, results: dict, out_path: Path) -> None:
    template = template_path.read_text()
    html = template.replace("__DATA_JSON__", json.dumps(results))
    out_path.write_text(html)


def write_site_chart_data(
    out_dir: Path,
    site_id: str,
    raw: pd.DataFrame,
    info: dict,
    months: list,
    params: dict,
) -> None:
    """Write a self-contained per-site JSON consumed by the chart tab."""
    readings = build_timeseries(raw)
    payload = {
        "site_id": site_id,
        "unit": "kWh",
        "interval_minutes": 15,
        "timezone": info.get("time_zone", "UTC"),
        "params": params,
        "meta": {
            "industry": info.get("industry", "Unknown"),
            "sub_industry": info.get("sub_industry", "Unknown"),
            "sq_ft": info.get("sq_ft"),
        },
        "months": months,
        "readings": readings,
    }
    sites_dir = out_dir / "data" / "sites"
    sites_dir.mkdir(parents=True, exist_ok=True)
    (sites_dir / f"{site_id}.json").write_text(json.dumps(payload))


def site_sort_key(path: Path):
    return (0, int(path.stem)) if path.stem.isdigit() else (1, path.stem)


def main():
    ap = argparse.ArgumentParser(description="Peak shaving opportunity analyzer")
    ap.add_argument("--csv-dir", default="csv-only/csv", type=Path,
                    help="Directory with per-site 5-minute CSVs")
    ap.add_argument("--meta", default="csv-only/meta/all_sites.csv", type=Path,
                    help="Site metadata CSV (for TIME_ZONE)")
    ap.add_argument("-N", "--num-peaks", default=3, type=int,
                    help="Number of peaks to shave per month")
    ap.add_argument("-L", "--max-window", default=3, type=int,
                    help="Maximum shaving window length in 15-minute periods")
    ap.add_argument("--out", default="output", type=Path,
                    help="Output directory for results.json and report.html")
    ap.add_argument("--sites", nargs="*", default=None,
                    help="Optional subset of site IDs to process")
    ap.add_argument("--no-site-charts", action="store_true",
                    help="Skip writing per-site time-series JSON for the charts")
    args = ap.parse_args()

    site_meta = load_site_meta(args.meta)
    csv_files = sorted(args.csv_dir.glob("*.csv"), key=site_sort_key)
    if args.sites:
        wanted = set(args.sites)
        csv_files = [p for p in csv_files if p.stem in wanted]

    results = {
        "params": {"N": args.num_peaks, "L": args.max_window},
        "customers": {},
        "meta": {},
    }
    params = {"N": args.num_peaks, "L": args.max_window}
    args.out.mkdir(parents=True, exist_ok=True)
    for csv_path in csv_files:
        site_id = csv_path.stem
        info = site_meta.get(site_id, {})
        tz = info.get("time_zone", "UTC")
        raw = load_raw(csv_path)
        months = process_customer(raw, tz, args.num_peaks, args.max_window)
        results["customers"][site_id] = months
        results["meta"][site_id] = {
            "industry": info.get("industry", "Unknown"),
            "sub_industry": info.get("sub_industry", "Unknown"),
            "sq_ft": info.get("sq_ft"),
        }
        if not args.no_site_charts:
            write_site_chart_data(args.out, site_id, raw, info, months, params)
        print(f"processed site {site_id} ({tz})")

    json_path = args.out / "results.json"
    json_path.write_text(json.dumps(results, indent=2))
    print(f"wrote {json_path}")

    src_dir = Path(__file__).parent
    report_path = args.out / "report.html"
    build_report(src_dir / "report_template.html", results, report_path)
    print(f"wrote {report_path}")

    chart_dst = args.out / "site_chart.html"
    shutil.copyfile(src_dir / "site_chart_template.html", chart_dst)
    print(f"wrote {chart_dst}")


if __name__ == "__main__":
    main()
