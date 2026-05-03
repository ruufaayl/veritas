# Forensic probe scripts

One-off scripts used to reverse-engineer registry endpoints during the
initial implementation. Kept around as living documentation of what works
and what doesn't.

| Script              | What it tested                                                                 |
| ------------------- | ------------------------------------------------------------------------------ |
| `probe_verra.py`    | Pagination + filter params on `myrpt.asp?r=206`, sweep of report IDs 200–210.  |
| `probe_verra2.py`   | Contents of `r=209` (Buffer Pool, 296 AFOLU projects), pagination via mypage.  |
| `probe_gs.py`       | Whether Gold Standard's project page yields useful static HTML (it doesn't).   |

## Findings (locked in here so we don't re-investigate)

- **Verra `/uiapi/resource/resourceSummary`** — returns hard 404 today across all
  param/header/method permutations. Endpoint was retired or moved behind auth.
- **Verra `/app/projectDetail/VCS/{id}`** — JS SPA. Static scrape only yields
  the page title (`"Verra Search Page"`). No useful metadata.
- **Verra `/mymodule/rpt/myrpt.asp?r={id}`** — Legacy ASP report. Renders
  server-side HTML tables. **`r=209`** lists 296 AFOLU projects (the high-fraud
  category). **`r=206`** lists 299,274 issuance rows but pagination is
  session-bound (only the first 50 are accessible without a search/print flow).
- **Verra rptdownload.asp** — looks like a CSV exporter but is server-stateful;
  needs the `c16e/myFilter/Data/Title/...` fields populated by an upstream
  `search.asp` POST. Not worth wiring up vs just parsing the HTML tables.
- **Verra has aggressive WAF/bot protection.** A burst of ~25 requests will
  get the source IP blocked for hours with HTTP 403 across the board,
  regardless of User-Agent. Throttle aggressively in production
  (≥10s between requests, single concurrent connection).
- **Gold Standard `/projects/details/{id}`** — Also a JS SPA. 2 KB shell, no
  data in static HTML.
