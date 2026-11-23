# Wealth Management Engine: Context (v2)

A FastAPI app that reads bank-statement CSVs and shows a small cash dashboard with Total Income, Total Spending, Sent to Wealthsimple and Liquid Cash. All of the code is in `src/main.py` and the tests are in `tests/test_upload.py`.

## Request flow

1. The user uploads one or more CSVs to `POST /upload`. `POST /sample` runs the same steps on the three bundled sample CSVs.
2. Each file is read in memory and checked against the 1 MB cap, then decoded and parsed with `parse_csv`. A bad file rejects the whole upload with a friendly 400 page.
3. `deduplicate` removes transactions that appear in more than one overlapping export.
4. `transactions.sort(key=lambda t: t.date)` runs Python's built-in Timsort. It is stable, so rows from the same day keep their file order and the last balance of the day wins.
5. `FinancialTotals.transaction_processor` adds up the totals, and `render_dashboard` returns the HTML.

Each request has only its own state. Nothing is shared between requests or kept at module level.

## Routes

| Route | Purpose |
|---|---|
| `GET /` | 307 redirect to `/dashboard` |
| `GET /dashboard` | Upload form, privacy notice, "Tested on TD and Wealthsimple" note and an upload tip |
| `POST /upload` | Parse the uploaded CSVs and render the totals |
| `POST /sample` | Render totals for the bundled sample files |

`GET /api/metrics` and the wildcard CORS middleware were removed in v2.

## Supported statement formats

- **TD:** no header row. The columns are date, description, withdrawal, deposit, balance (`TD_COLUMNS`). A file counts as TD when its first cell parses as a date.
- **Wealthsimple:** a header row of `date, transaction, description, amount, balance, currency`.
- **Scotiabank:** a header row of `Filter, Date, Description, Sub-description, Type of Transaction, Amount, Balance`.

`_map_header` matches column names by substring:
- It always keeps the first "Description" column. A column whose name starts with "sub", such as "Sub-description", is ignored, so it can never replace Description.
- A column named with "transaction" is used as the transaction type. If the file has no description column, the type column is used as the description too.
- `date`, `description`, `amount` and `balance` are required. If one is missing, the error page names it.

`DATE_FORMATS` accepts `%Y-%m-%d`, `%m-%d-%Y`, `%m/%d/%Y` and `%Y/%m/%d`. Dates written with slashes are always read month first, so a day-first DD/MM/YYYY export would be read wrong.

## Money

All amounts are `decimal.Decimal`, never float:
- `_parse_amount` strips commas and parses the text with `Decimal`.
- It rejects NaN and infinity with `.is_finite()`.
- It rounds to cents with `quantize(Decimal("0.01"))`. Values too large to round, such as `1e30`, are rejected.
- The `FinancialTotals` fields start at `Decimal("0")`.
- The dashboard formats values with `:,.2f`.

## Classification

- **Internal transfer:** a Wealthsimple type of `EFT`/`EFTOUT`, or a description containing `TFR-FR`, `TFR-TO` or `EFTOUT`. These count toward neither income nor spending.
- **Sent to Wealthsimple:** a description containing `WS INVESTMENTS`. These are tracked separately and do not count as spending.
- **Everything else:** deposits count as income and withdrawals count as spending.
- Every row updates its account's latest balance, transfers included. Liquid Cash is the sum of each account's final balance.

## Deduplication (`deduplicate`)

- **Key:** `(date, description with spacing collapsed and in upper case, deposit − withdrawal, balance)`. The filename is not part of the key.
- **Counting:** each file gets its own `collections.Counter` of keys. When a key appears n times in one file, those are real repeats, such as two identical coffees on the same day. Across files, the result keeps the highest count, taken from the one file that had the most. Overlapping exports therefore count once.
- **Union-find:** files that share any transaction are merged into one account. Overlapping exports with different filenames then give one ending balance, not two.
- **Known limitation:** two exports of the same account that don't overlap can't be linked, so that account's balance is counted twice. The upload tip asks users to export overlapping date ranges.

## Privacy and limits

- **Size cap:** `MAX_FILE_BYTES = 1_000_000`. This is below Starlette's 1 MiB `SpooledTemporaryFile` threshold, so an accepted file stays in memory and is never written to disk. Larger files get a 413 page. Note that a rejected oversized upload may still be spooled to disk briefly before it is refused.
- **No cell values leak:** a `StatementFormatError` message names only the file and row number. The `ValueError`s it wraps are re-raised `from None`, and logs record only the HTTP status or the exception type.
- **Errors:** the 400, 413 and 500 pages each have their own heading. The 500 page says "Something went wrong on our end", and filenames are HTML-escaped.

## Tests

Install with `pip install -r requirements-dev.txt` and run `python -m pytest tests -q`. There are 38 tests, which cover:
- routes and the redirect;
- the notices on the dashboard;
- that `/api/metrics` and CORS are gone;
- sample totals: income 1,101.85, spending 603.35, Sent to Wealthsimple 425.00, liquid cash 1,098.50;
- that requests don't share state;
- overlapping-export dedup: same file twice, everything twice, renamed and reversed overlaps;
- same-day duplicates and the Counter max rule;
- Scotiabank slash dates and Sub-description handling;
- exact Decimal sums;
- 13 malformed files, each checked for leaks into the response, logs, stdout and stderr;
- the 413 page and a file of exactly 1,000,000 bytes;
- HTML escaping;
- the 500 page.

## Deploy (Render)

- Install: `pip install -r requirements.txt` (fastapi, uvicorn, python-multipart).
- Start: `uvicorn main:app --app-dir src --host 0.0.0.0 --port $PORT`
