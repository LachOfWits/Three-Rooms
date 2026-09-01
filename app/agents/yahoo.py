"""Yahoo Finance close prices, fetched directly.

Why this exists: @focused's job in the research stage is to source the
month-end level of each market input INDEPENDENTLY, so that comparing it
against `assumptions/` means something. Reaching those levels through
`web_fetch` did not work — Yahoo's pages do not render for it, and the
agent fell back to whatever commentary page it could read, or gave up and
marked the factor unsourced. A direct call to Yahoo's public chart endpoint
is what a person would do, needs no key, and returns the close for a named
day.

Deliberately NOT part of the engine: this is the app reaching out to the
web on an agent's behalf. The model itself never touches the network.
"""
from __future__ import annotations

import datetime as dt
import json
import urllib.error
import urllib.parse
import urllib.request

# The tickers behind the model's market inputs. `scale` converts Yahoo's
# quote into the unit the assumptions file uses; all of these already come
# back in it (^TNX serves the 10-year yield as a percent, e.g. 4.311).
TICKERS: dict[str, dict] = {
    "ftse100": {"symbol": "^FTSE", "unit": "index", "scale": 1.0,
                "label": "FTSE 100"},
    "sp500": {"symbol": "^GSPC", "unit": "index", "scale": 1.0,
              "label": "S&P 500"},
    "sx5e": {"symbol": "^STOXX50E", "unit": "index", "scale": 1.0,
             "label": "EURO STOXX 50"},
    "gbpusd": {"symbol": "GBPUSD=X", "unit": "USD per GBP", "scale": 1.0,
               "label": "GBP/USD"},
    "ust_10y": {"symbol": "^TNX", "unit": "%", "scale": 1.0,
                "label": "US 10y Treasury"},
}

_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_TIMEOUT = 20


class YahooError(RuntimeError):
    """Yahoo could not be reached, or carried no close for the day asked."""


def _window(asof: str) -> tuple[int, int]:
    """A UTC epoch window that safely brackets `asof`.

    Widened either side: a month-end can be a weekend or a holiday, and the
    close we want is then the last trading day before it.
    """
    day = dt.date.fromisoformat(str(asof)[:10])
    start = dt.datetime.combine(day - dt.timedelta(days=10), dt.time.min,
                                tzinfo=dt.timezone.utc)
    end = dt.datetime.combine(day + dt.timedelta(days=2), dt.time.min,
                              tzinfo=dt.timezone.utc)
    return int(start.timestamp()), int(end.timestamp())


def _closes(symbol: str, asof: str) -> list[tuple[dt.date, float]]:
    p1, p2 = _window(asof)
    url = (f"{_CHART.format(symbol=urllib.parse.quote(symbol))}"
           f"?period1={p1}&period2={p2}&interval=1d")
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            payload = json.load(resp)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        raise YahooError(f"could not reach Yahoo for {symbol}: {e}") from e

    result = ((payload.get("chart") or {}).get("result") or [None])[0]
    if not result:
        raise YahooError(f"Yahoo returned no series for {symbol}")
    stamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    out = []
    for ts, close in zip(stamps, closes):
        if close is None:
            continue
        out.append((dt.datetime.fromtimestamp(ts, dt.timezone.utc).date(),
                    float(close)))
    if not out:
        raise YahooError(f"Yahoo returned no closes for {symbol}")
    return out


def close_on(factor: str, asof: str) -> dict:
    """The close for `factor` on `asof`, or the last trading day before it.

    Returns the value already converted into the unit the assumptions file
    uses, with the exact date it came from and the URL a reader can open to
    check it — so the figure carries its own provenance.
    """
    spec = TICKERS.get(factor)
    if spec is None:
        raise YahooError(f"no Yahoo ticker mapped for '{factor}'")
    target = dt.date.fromisoformat(str(asof)[:10])
    series = [(d, c) for d, c in _closes(spec["symbol"], asof) if d <= target]
    if not series:
        raise YahooError(
            f"no close for {spec['symbol']} on or before {target}")
    day, close = series[-1]
    return {
        "factor": factor,
        "label": spec["label"],
        "symbol": spec["symbol"],
        "value": round(close * spec["scale"], 6),
        "unit": spec["unit"],
        "asof": day.isoformat(),
        "requested": target.isoformat(),
        "source_url": f"https://finance.yahoo.com/quote/"
                      f"{urllib.parse.quote(spec['symbol'])}/history/",
    }


def close_all(asof: str) -> dict:
    """Every mapped factor for a month-end. A factor Yahoo cannot serve is
    reported as an error rather than dropped: an honest gap is the point."""
    levels, errors = {}, {}
    for factor in TICKERS:
        try:
            levels[factor] = close_on(factor, asof)
        except YahooError as e:
            errors[factor] = str(e)
    return {"asof": str(asof)[:10], "levels": levels, "errors": errors}
