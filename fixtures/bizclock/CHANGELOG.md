# Changelog

## Unreleased

- `formatCutoff(date)`: format a cutoff as `"Fri 17:30"` for dashboards.

## 2.3.1 (2025-04-02)

- `nextCutoff()` now throws a `RangeError` for cutoffs like `"24:00"` instead of silently rolling over.

## 2.3.0 (2025-02-03)

- First public release of the 2.x line: `nextCutoff()` and `isBusinessDay()`.
