# Calling rule

Committed before any hit-rate file exists. `runway/calendar.py` reads the threshold from this file and writes its sha256 as `rule_hash` on every call and on `handoff/hit_flags.json`. A change to this file after `handoff/hit_flags.json` exists fails `tests/test_calendar.py`.

A calendar day in America/Chicago, in June, July, August, or September 2025, is a candidate when that day's maximum day-ahead ERCOT system forecast is at least 0.97 times the maximum daily day-ahead peak from the first of that month through that day, inclusive. One call per candidate day. The forecast is hour-ending, so the forecast peak instant is the middle of the peak hour. The call starts 45 minutes before that instant and lasts 90 minutes. `peak_odds` is that ratio clipped to `[0, 1]`. It is a score, not a probability. The call hits when a published 4CP interval (15 minutes, interval-ending) overlaps the call window.

This is ERCOT system load (EIA-930 demand and day-ahead demand forecast for ERCO). It is a proxy for the Austin Energy, GVEC, and CoServ hunts. Published 4CP intervals come from ERCOT report NP9-83-M. Sensitivity at 0.96 and 0.98 may be written only as a pair, in a side file.

```rule
timezone: America/Chicago
year: 2025
months: 6,7,8,9
threshold: 0.97
start_offset_min: -45
duration_min: 90
source: 4cp_candidate
utility: Austin Energy
mw_requested: 40.0
```
