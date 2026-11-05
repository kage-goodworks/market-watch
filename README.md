# market-watch

Daily closing prices for FX, equity indices, rates, commodities and crypto, kept as CSV.

- `scripts/fetch_prices.py`: fetches daily bars from Yahoo Finance via yfinance. The first run backfills about 10 years; later runs fetch the last three weeks and merge.
- `data/daily/<NAME>.csv`: one file per market (`date,open,high,low,close,volume`).
- `data/latest.md`: one table of the latest close with 1/5/20-day change and position in the 52-week range.
- `data/status.json`: which symbols were fetched in the last run.

Runs every day at 06:47 JST from GitHub Actions (`.github/workflows/fetch.yml`). To add a market, add a line to `SYMBOLS` in the script.

The data is for personal study and demo-account verification only. It is not investment advice.
