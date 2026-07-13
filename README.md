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
`--sites 8 14` to process a subset of site IDs. Pass `--no-site-charts` to
skip writing the per-site time-series JSON (faster; disables the detail
charts).

Outputs:

- `output/results.json` — full results (parameters, per-customer monthly
  records with shaved peak windows)
- `output/report.html` — customers x months heatmap with a toggle between
  demand shaved (kW) and energy delivered (kWh)
- `output/site_chart.html` — single-site detail page (time-series chart plus
  monthly peak-shaving summary), opened from the report
- `output/data/sites/{id}.json` — per-site 15-minute time series and monthly
  results consumed by the detail page

## Single-site detail charts

Each customer ID in `report.html` is a link that opens `site_chart.html` in a
new tab for that site. The detail page shows:

- A Plotly line chart of consumption over time at 15-minute resolution, with
  start/end range controls and span presets (All, 1 year, 1 month, 1 week, 3
  days, 1 day, 1 hour). Times are always shown in the site's local timezone.
  A range slider under the x-axis acts as a horizontal scroller: it shows the
  full loaded range as an overview and lets you drag/resize a window to scroll
  across it. Zooming/panning syncs back to the range inputs.
- A units toggle (kW / kWh). The chart defaults to average demand in **kW**
  (interval energy x 4, since a 15-minute interval is a quarter hour); switch
  to **kWh** to see the raw interval energy.
- A **Temperature overlay** checkbox (on by default) plots Open-Meteo ERA5 air
  temperature on a secondary right axis when weather data is present. Uncheck
  to hide the overlay.
- Shaved peak windows are flagged as shaded bands on the chart: green for a
  standalone window and orange (dashed) for a window that is adjacent to
  (immediately follows) another shaved window.
- Summary stats for the visible range (total kWh, peak interval kWh, avg daily
  kWh).
- A month-by-month peak-shaving table with an averages row.

The detail page loads its data with `fetch()`, so the output must be served
over HTTP (not opened via `file://`):

```bash
cd output && python3 -m http.server
# then open http://localhost:8000/report.html
```

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

## Weather data (Open-Meteo)

Companion temperature CSVs can be generated from the [Open-Meteo ERA5
archive](https://open-meteo.com/en/docs/historical-weather-api) using each
site's lat/lng from the metadata file. The API returns hourly `temperature_2m`;
the script linearly interpolates to a 15-minute UTC grid aligned with the
energy readings.

```bash
.venv/bin/python fetch_weather.py \
  --meta csv-only/meta/all_sites.csv \
  --out csv-only/weather \
  --start 2012-01-01 --end 2012-12-31
```

Use `--sites 8 14` to fetch a subset. Output is one file per site:

- `csv-only/weather/{id}.csv` — columns `dttm_utc`, `temperature_c`

## Data

The `csv-only/` folder contains the EnerNOC GreenButton dataset: anonymized
5-minute kWh readings for 100 commercial/industrial sites for 2012, with site
metadata (industry, square footage, timezone, lat/lng) in `csv-only/meta/`.
Temperature CSVs (when generated) live in `csv-only/weather/`.
