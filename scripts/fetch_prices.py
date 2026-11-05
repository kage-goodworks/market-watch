"""Fetch daily OHLC for many markets and keep per-symbol CSV history.

First run backfills ~10 years. Later runs fetch the last few weeks and merge.
Writes:
  data/daily/<name>.csv   date,open,high,low,close,volume
  data/latest.md          one table Claude reads each morning
  data/status.json        per-symbol fetch result
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
DAILY = ROOT / "data" / "daily"
JST = timezone(timedelta(hours=9))

# name -> (yahoo symbol, group, label)
SYMBOLS: dict[str, tuple[str, str, str]] = {
    # FX (cross yen)
    "USDJPY": ("USDJPY=X", "FX", "ドル円"),
    "EURJPY": ("EURJPY=X", "FX", "ユーロ円"),
    "GBPJPY": ("GBPJPY=X", "FX", "ポンド円"),
    "AUDJPY": ("AUDJPY=X", "FX", "豪ドル円"),
    "NZDJPY": ("NZDJPY=X", "FX", "NZドル円"),
    "CADJPY": ("CADJPY=X", "FX", "カナダドル円"),
    "CHFJPY": ("CHFJPY=X", "FX", "スイスフラン円"),
    "MXNJPY": ("MXNJPY=X", "FX", "メキシコペソ円"),
    "ZARJPY": ("ZARJPY=X", "FX", "南アランド円"),
    "TRYJPY": ("TRYJPY=X", "FX", "トルコリラ円"),
    # FX (dollar straights)
    "EURUSD": ("EURUSD=X", "FX", "ユーロドル"),
    "GBPUSD": ("GBPUSD=X", "FX", "ポンドドル"),
    "AUDUSD": ("AUDUSD=X", "FX", "豪ドル米ドル"),
    "NZDUSD": ("NZDUSD=X", "FX", "NZドル米ドル"),
    "USDCAD": ("USDCAD=X", "FX", "米ドルカナダドル"),
    "USDCHF": ("USDCHF=X", "FX", "米ドルスイスフラン"),
    "USDCNY": ("USDCNY=X", "FX", "米ドル人民元"),
    "DXY": ("DX-Y.NYB", "FX", "ドル指数"),
    # Equity indices
    "N225": ("^N225", "株価指数", "日経平均"),
    "SPX": ("^GSPC", "株価指数", "S&P500"),
    "NDX": ("^IXIC", "株価指数", "ナスダック総合"),
    "DJI": ("^DJI", "株価指数", "NYダウ"),
    "DAX": ("^GDAXI", "株価指数", "独DAX"),
    "FTSE": ("^FTSE", "株価指数", "英FTSE100"),
    "HSI": ("^HSI", "株価指数", "香港ハンセン"),
    "SSEC": ("000001.SS", "株価指数", "上海総合"),
    "VIX": ("^VIX", "株価指数", "VIX恐怖指数"),
    # Rates
    "US10Y": ("^TNX", "金利", "米10年債利回り"),
    "US13W": ("^IRX", "金利", "米3か月債利回り"),
    # Commodities
    "GOLD": ("GC=F", "商品", "金"),
    "SILVER": ("SI=F", "商品", "銀"),
    "WTI": ("CL=F", "商品", "WTI原油"),
    "NATGAS": ("NG=F", "商品", "天然ガス"),
    "COPPER": ("HG=F", "商品", "銅"),
    # Crypto
    "BTC": ("BTC-USD", "暗号資産", "ビットコイン"),
    "ETH": ("ETH-USD", "暗号資産", "イーサリアム"),
}

COLUMNS = ["open", "high", "low", "close", "volume"]


def load(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=COLUMNS)
    df = pd.read_csv(path, parse_dates=["date"], index_col="date")
    return df[COLUMNS]


def fetch(symbol: str, start: str | None) -> pd.DataFrame:
    kwargs = {"interval": "1d", "auto_adjust": False, "progress": False, "threads": False}
    if start:
        raw = yf.download(symbol, start=start, **kwargs)
    else:
        raw = yf.download(symbol, period="10y", **kwargs)
    if raw is None or raw.empty:
        return pd.DataFrame(columns=COLUMNS)
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    raw = raw.rename(columns=str.lower)
    df = raw[[c for c in COLUMNS if c in raw.columns]].copy()
    for c in COLUMNS:
        if c not in df.columns:
            df[c] = pd.NA
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df.index.name = "date"
    return df.dropna(subset=["close"])


def pct(a: float, b: float) -> str:
    if b in (0, None) or pd.isna(a) or pd.isna(b):
        return "-"
    return f"{(a / b - 1) * 100:+.2f}%"


def summarize(name: str, label: str, group: str, df: pd.DataFrame) -> dict | None:
    if len(df) < 2:
        return None
    c = df["close"].astype(float)
    last = c.iloc[-1]
    year = c[c.index >= c.index[-1] - pd.Timedelta(days=365)]
    lo, hi = year.min(), year.max()
    pos = "-" if hi == lo else f"{(last - lo) / (hi - lo) * 100:.0f}%"
    def back(n: int) -> float:
        return c.iloc[-1 - n] if len(c) > n else float("nan")
    return {
        "group": group, "name": name, "label": label,
        "date": c.index[-1].strftime("%Y-%m-%d"),
        "close": f"{last:,.4f}" if last < 10 else f"{last:,.2f}",
        "d1": pct(last, back(1)), "d5": pct(last, back(5)), "d20": pct(last, back(20)),
        "pos52w": pos,
    }


def main() -> int:
    DAILY.mkdir(parents=True, exist_ok=True)
    status: dict[str, str] = {}
    rows = []
    for name, (symbol, group, label) in SYMBOLS.items():
        path = DAILY / f"{name}.csv"
        old = load(path)
        start = None
        if not old.empty:
            start = (old.index.max() - pd.Timedelta(days=21)).strftime("%Y-%m-%d")
        try:
            new = fetch(symbol, start)
        except Exception as e:  # keep going on one bad symbol
            status[name] = f"error: {type(e).__name__}: {e}"[:200]
            new = pd.DataFrame(columns=COLUMNS)
        else:
            status[name] = "ok" if not new.empty else "empty"
        merged = pd.concat([old, new])
        merged = merged[~merged.index.duplicated(keep="last")].sort_index()
        if not merged.empty:
            merged.to_csv(path, index_label="date", float_format="%.6f")
        s = summarize(name, label, group, merged)
        if s:
            rows.append(s)

    now = datetime.now(JST).strftime("%Y-%m-%dT%H:%M:%S+09:00")
    lines = [
        "# 相場データ（最新の終値）",
        "",
        f"取得: {now}　出典: Yahoo Finance（yfinance）。週末・祝日は直近の営業日の値。",
        "",
        "| 区分 | 銘柄 | 日付 | 終値 | 前日比 | 5日 | 20日 | 52週レンジ内の位置 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['group']} | {r['label']}（{r['name']}） | {r['date']} | {r['close']} | "
            f"{r['d1']} | {r['d5']} | {r['d20']} | {r['pos52w']} |"
        )
    bad = {k: v for k, v in status.items() if v != "ok"}
    lines += ["", f"取得できなかった銘柄: {', '.join(bad) if bad else 'なし'}", ""]
    (ROOT / "data" / "latest.md").write_text("\n".join(lines), encoding="utf-8")
    (ROOT / "data" / "status.json").write_text(
        json.dumps({"fetched_at": now, "status": status}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    ok = sum(1 for v in status.values() if v == "ok")
    print(f"ok {ok}/{len(status)}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
