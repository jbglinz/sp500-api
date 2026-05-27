from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import yfinance as yf
from datetime import datetime, timezone
import time

app = FastAPI(title="S&P 500 Sector Heatmap API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

SECTOR_TICKERS = ["XLK","XLF","XLC","XLY","XLV","XLI","XLP","XLE","XLRE","XLB","XLU"]
INDEX_TICKERS  = {"SPY": "SPY", "QQQ": "QQQ", "IWM": "IWM"}

_cache: dict = {}

def cache_get(key: str, ttl: int = 60):
    entry = _cache.get(key)
    if entry and (time.time() - entry["ts"]) < ttl:
        return entry["data"]
    return None

def cache_set(key: str, data):
    _cache[key] = {"data": data, "ts": time.time()}

def safe_round(val, digits=2):
    try:
        return round(float(val), digits)
    except (TypeError, ValueError):
        return None

def get_ticker_data(symbol: str) -> dict:
    t = yf.Ticker(symbol)

    info = {}
    try:
        info = t.info
    except Exception:
        pass

    # Calculate YTD from price history (most reliable)
    ytd_pct = None
    try:
        raw_ytd = info.get("ytdReturn")
        if raw_ytd and raw_ytd != 0:
            ytd_pct = safe_round(float(raw_ytd) * 100)
        else:
            hist = t.history(period="ytd", interval="1d")
            if not hist.empty and len(hist) >= 2:
                first = float(hist["Close"].iloc[0])
                last  = float(hist["Close"].iloc[-1])
                if first > 0:
                    ytd_pct = safe_round((last - first) / first * 100)
    except Exception:
        pass

    price   = (info.get("currentPrice")
               or info.get("regularMarketPrice")
               or info.get("navPrice"))
    div_raw = (info.get("dividendYield")
               or info.get("trailingAnnualDividendYield")
               or 0)

    return {
        "price":    safe_round(price),
        "change1d": safe_round(info.get("regularMarketChangePercent")),
        "ytd":      ytd_pct,
        "high52":   safe_round(info.get("fiftyTwoWeekHigh")),
        "low52":    safe_round(info.get("fiftyTwoWeekLow")),
        "pe":       safe_round(info.get("forwardPE") or info.get("trailingPE")),
        "divYield": safe_round(float(div_raw) * 100) if div_raw else None,
        "volume":   info.get("averageVolume"),
    }


@app.get("/")
def root():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


@app.get("/sectors")
def get_sectors():
    cached = cache_get("sectors", ttl=60)
    if cached:
        return cached
    result = {}
    for ticker in SECTOR_TICKERS:
        try:
            result[ticker] = get_ticker_data(ticker)
        except Exception as e:
            result[ticker] = {"error": str(e)}
    out = {"quotes": result, "updated_at": datetime.now(timezone.utc).isoformat()}
    cache_set("sectors", out)
    return out


@app.get("/indices")
def get_indices():
    cached = cache_get("indices", ttl=60)
    if cached:
        return cached
    result = {}
    for name, sym in INDEX_TICKERS.items():
        try:
            result[name] = get_ticker_data(sym)
        except Exception as e:
            result[name] = {"error": str(e)}
    out = {"quotes": result, "updated_at": datetime.now(timezone.utc).isoformat()}
    cache_set("indices", out)
    return out


@app.get("/quote/{ticker}")
def get_quote(ticker: str):
    ticker = ticker.upper()
    key = f"quote:{ticker}"
    cached = cache_get(key, ttl=60)
    if cached:
        return cached
    try:
        data = get_ticker_data(ticker)
        data["ticker"] = ticker
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        cache_set(key, data)
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/history/{ticker}")
def get_history(ticker: str, period: str = "ytd", interval: str = "1d"):
    ticker = ticker.upper()
    key = f"history:{ticker}:{period}:{interval}"
    cached = cache_get(key, ttl=300)
    if cached:
        return cached

    valid_periods   = {"1d","5d","1mo","3mo","6mo","ytd","1y","2y","5y","10y","max"}
    valid_intervals = {"1m","5m","15m","30m","1h","1d","1wk","1mo"}
    if period not in valid_periods:
        raise HTTPException(status_code=400, detail=f"Invalid period. Use: {valid_periods}")
    if interval not in valid_intervals:
        raise HTTPException(status_code=400, detail=f"Invalid interval. Use: {valid_intervals}")

    try:
        t = yf.Ticker(ticker)
        hist = t.history(period=period, interval=interval)
        if hist.empty:
            raise HTTPException(status_code=404, detail=f"No data for {ticker}")
        records = []
        for ts, row in hist.iterrows():
            records.append({
                "date":   ts.isoformat(),
                "open":   safe_round(row.get("Open")),
                "high":   safe_round(row.get("High")),
                "low":    safe_round(row.get("Low")),
                "close":  safe_round(row.get("Close")),
                "volume": int(row.get("Volume", 0)),
            })
        out = {
            "ticker": ticker, "period": period, "interval": interval,
            "count": len(records), "data": records,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        cache_set(key, out)
        return out
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/market-data")
def get_market_data():
    cached = cache_get("market-data", ttl=60)
    if cached:
        return cached

    sectors_data = get_sectors()
    indices_data = get_indices()

    quotes = {}
    for ticker, d in sectors_data["quotes"].items():
        quotes[ticker] = {
            "ytd":      d.get("ytd"),
            "price":    d.get("price"),
            "high52":   d.get("high52"),
            "low52":    d.get("low52"),
            "pe":       d.get("pe"),
            "divYield": d.get("divYield"),
            "change1d": d.get("change1d"),
        }
    for name, d in indices_data["quotes"].items():
        quotes[name] = d

    out = {
        "success": True,
        "quotes": quotes,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    cache_set("market-data", out)
    return out
