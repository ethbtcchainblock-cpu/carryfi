import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

HEADERS = {"User-Agent": "CarryFi/1.0"}
MIN_VOLUME_USD = 5_000_000   # $5M 24h volume minimum
MIN_OI_USD     = 1_000_000   # $1M open interest minimum — ensures the market is actually farmable


def _annualize(rate: float, interval_hours: float) -> float:
    periods_per_year = (24 / interval_hours) * 365
    return round(rate * periods_per_year * 100, 4)


def _fmt_usd(v: float) -> str:
    if v >= 1_000_000_000:
        return f"${v/1_000_000_000:.1f}B"
    if v >= 1_000_000:
        return f"${v/1_000_000:.1f}M"
    if v >= 1_000:
        return f"${v/1_000:.0f}K"
    return f"${v:.0f}"


def fetch_hyperliquid() -> list[dict]:
    """Hyperliquid: 1h funding interval. dayNtlVlm = 24h USD vol. OI = openInterest * markPx."""
    try:
        r = requests.post(
            "https://api.hyperliquid.xyz/info",
            json={"type": "metaAndAssetCtxs"},
            headers=HEADERS,
            timeout=10,
        )
        r.raise_for_status()
        meta, ctxs = r.json()
        assets = meta.get("universe", [])
        results = []
        for asset, ctx in zip(assets, ctxs):
            rate = float(ctx.get("funding", 0))
            if rate == 0:
                continue
            vol_usd = float(ctx.get("dayNtlVlm", 0))
            if vol_usd < MIN_VOLUME_USD:
                continue
            mark_px = float(ctx.get("markPx", 0))
            oi_usd = float(ctx.get("openInterest", 0)) * mark_px
            if oi_usd < MIN_OI_USD:
                continue
            results.append({
                "coin": asset["name"],
                "exchange": "Hyperliquid",
                "rate_display": f"{rate*100:.5f}%/h",
                "apr": _annualize(rate, 1),
                "next_funding": "Top of hour",
                "volume_24h": _fmt_usd(vol_usd),
                "open_interest": _fmt_usd(oi_usd),
                "volume_usd": vol_usd,
            })
        return results
    except Exception:
        return []


def fetch_okx() -> list[dict]:
    """OKX: 8h funding. Batch-fetch tickers for vol/OI, then per-coin funding rates."""
    try:
        # Batch: tickers for vol24h in quote (USDT)
        t = requests.get(
            "https://www.okx.com/api/v5/market/tickers?instType=SWAP",
            headers=HEADERS, timeout=10,
        )
        ticker_map: dict[str, dict] = {}
        for item in t.json().get("data", []):
            if item.get("instId", "").endswith("-USDT-SWAP"):
                ticker_map[item["instId"]] = item

        # Batch: open interest for all USDT swaps
        oi_r = requests.get(
            "https://www.okx.com/api/v5/public/open-interest?instType=SWAP",
            headers=HEADERS, timeout=10,
        )
        oi_map: dict[str, float] = {}
        for item in oi_r.json().get("data", []):
            inst_id = item.get("instId", "")
            if inst_id.endswith("-USDT-SWAP"):
                oi_map[inst_id] = float(item.get("oiCcy", 0))

        # Filter to top 40 by volume, then fetch funding rate per coin
        ranked = sorted(
            [(k, float(v.get("volCcy24h", 0))) for k, v in ticker_map.items()],
            key=lambda x: x[1], reverse=True,
        )[:40]

        liquid = [(k, float(v.get("volCcy24h", 0))) for k, v in ticker_map.items()
                  if float(v.get("volCcy24h", 0)) >= MIN_VOLUME_USD]

        def _fetch_rate(inst_id: str, vol_usd: float):
            try:
                fr = requests.get(
                    f"https://www.okx.com/api/v5/public/funding-rate?instId={inst_id}",
                    headers=HEADERS, timeout=5,
                )
                data = fr.json().get("data", [])
                if not data:
                    return None
                d = data[0]
                rate = float(d.get("fundingRate", 0))
                if rate == 0:
                    return None
                next_ts = d.get("nextFundingTime")
                next_dt = datetime.fromtimestamp(int(next_ts) / 1000, tz=timezone.utc).strftime("%H:%M UTC") if next_ts else "—"
                coin = inst_id.replace("-USDT-SWAP", "")
                mark_px = float(ticker_map[inst_id].get("last", 1))
                oi_usd = oi_map.get(inst_id, 0) * mark_px
                if oi_usd < MIN_OI_USD:
                    return None
                return {
                    "coin": coin,
                    "exchange": "OKX",
                    "rate_display": f"{rate*100:.4f}%/8h",
                    "apr": _annualize(rate, 8),
                    "next_funding": next_dt,
                    "volume_24h": _fmt_usd(vol_usd),
                    "open_interest": _fmt_usd(oi_usd),
                    "volume_usd": vol_usd,
                }
            except Exception:
                return None

        results = []
        with ThreadPoolExecutor(max_workers=10) as ex:
            futures = {ex.submit(_fetch_rate, inst_id, vol): inst_id for inst_id, vol in liquid}
            for f in as_completed(futures):
                row = f.result()
                if row:
                    results.append(row)
        return results
    except Exception:
        return []


def fetch_gateio() -> list[dict]:
    """Gate.io: 8h funding. volume_24h_quote = USDT vol. OI ≈ total_size * last."""
    try:
        r = requests.get(
            "https://api.gateio.ws/api/v4/futures/usdt/tickers",
            headers=HEADERS, timeout=10,
        )
        r.raise_for_status()
        results = []
        for item in r.json():
            vol_usd = float(item.get("volume_24h_quote", 0))
            if vol_usd < MIN_VOLUME_USD:
                continue
            rate_str = item.get("funding_rate")
            if rate_str is None:
                continue
            rate = float(rate_str)
            if rate == 0:
                continue
            contract = item.get("contract", "")
            coin = contract.replace("_USDT", "")
            last = float(item.get("last", 1))
            total_size = float(item.get("total_size", 0))
            oi_usd = total_size * last
            if oi_usd < MIN_OI_USD:
                continue
            results.append({
                "coin": coin,
                "exchange": "Gate.io",
                "rate_display": f"{rate*100:.4f}%/8h",
                "apr": _annualize(rate, 8),
                "next_funding": "—",
                "volume_24h": _fmt_usd(vol_usd),
                "open_interest": _fmt_usd(oi_usd),
                "volume_usd": vol_usd,
            })
        return results
    except Exception:
        return []


def fetch_all() -> list[dict]:
    rows = fetch_hyperliquid() + fetch_okx() + fetch_gateio()
    rows.sort(key=lambda x: x["apr"], reverse=True)
    return rows
