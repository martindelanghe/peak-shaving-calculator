# Peak Shaving Calculator

Analyzes peak shaving opportunities per customer per month from 5-minute energy
consumption data, and produces a JSON results file plus a self-contained HTML
heatmap report.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Usage

```bash
.venv/bin/python peak_shaving.py \
  --csv-dir csv-only/csv \
  --meta csv-only/meta/all_sites.csv \
  -N 5 -L 8 \
  --out output
```

All arguments are optional; the values above are the defaults. Use
`--sites 8 14` to process a subset of site IDs.

Outputs:

- `output/results.json` — full results (parameters, per-customer monthly
  records with shaved peak windows)
- `output/report.html` — open directly in a browser; customers x months
  heatmap with a toggle between demand shaved (kW) and energy delivered (kWh)

## Parameters

- `-N` — number of peaks to shave per customer per month
- `-L` — maximum shaving window length, in 15-minute periods

## Method

For each customer, readings are converted to the site's local timezone (from
the metadata file), anomalous readings are set to NaN, and the 5-minute series
is summed into aligned 15-minute intervals (an interval with any missing or
NaN reading becomes NaN). Each calendar month is processed independently:

1. Intervals are ranked by energy descending (ties broken by earlier time).
2. The highest unclaimed valid interval seeds a window: among contiguous runs
   of unclaimed, non-NaN intervals (NaN and month boundaries break runs), the
   window of up to L intervals containing the seed with the highest total
   energy is claimed. This repeats until N windows are claimed or no valid
   intervals remain.
3. The floor F is the highest remaining unclaimed interval (0 if none).
4. Each window is trimmed of border intervals below F; its delivered energy is
   the sum of positive differences above F.
5. Total demand shaved = (highest interval - F) x 4 (kW). Total energy
   delivered is the sum over windows (kWh). Windows contiguous to an earlier
   window are flagged.

Note: converting UTC-aligned data to local time creates a leading partial
month (e.g. Dec 2011); that partial month is dropped from the analysis.

## Output format

```json
{
  "params": {"N": 5, "L": 8},
  "customers": {
    "8": [
      {
        "month": "2012-07",
        "total_demand_shaved_kw": 223.486,
        "total_energy_delivered_kwh": 209.518,
        "peaks": [
          {
            "start": "2012-07-09 14:45",
            "end": "2012-07-09 15:00",
            "delivered_energy_kwh": 13.968,
            "contiguous": false
          }
        ]
      }
    ]
  }
}
```

Timestamps are in the site's local timezone. `contiguous` is true when the
window immediately follows another shaved window.

## Data

The `csv-only/` folder contains the EnerNOC GreenButton dataset: anonymized
5-minute kWh readings for 100 commercial/industrial sites for 2012, with site
metadata (industry, square footage, timezone) in `csv-only/meta/`.
