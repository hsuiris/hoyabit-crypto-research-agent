"""Download five years of public daily OHLCV for all competition coins.

This creates a clearly labeled fallback dataset. It does not impersonate the
organizer-provided competition package.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


COINS = ("BTC", "ETH", "SOL", "BNB", "XRP")
ENDPOINT = "https://data-api.binance.vision/api/v3/klines"


def download(coin: str) -> tuple[list[dict], list[str]]:
    symbol = f"{coin}USDT"
    end_time = int(datetime.now(timezone.utc).timestamp() * 1000)
    raw_rows = []
    source_urls = []
    while len(raw_rows) < 1826:
        query = urlencode({"symbol": symbol, "interval": "1d", "limit": 1000, "endTime": end_time})
        url = f"{ENDPOINT}?{query}"
        source_urls.append(url)
        request = Request(url, headers={"User-Agent": "hoyabit-agent-mvp/1.0"})
        with urlopen(request, timeout=30) as response:
            batch = json.loads(response.read().decode("utf-8"))
        if not isinstance(batch, list) or not batch:
            break
        raw_rows.extend(batch)
        end_time = int(batch[0][0]) - 1
        if len(batch) < 1000:
            break
    unique = {int(row[0]): row for row in raw_rows}
    output = []
    for open_time, row in sorted(unique.items())[-1826:]:
        output.append({
            "date": datetime.fromtimestamp(open_time / 1000, timezone.utc).strftime("%Y-%m-%d"),
            "open": row[1], "high": row[2], "low": row[3], "close": row[4], "volume": row[5],
        })
    if len(output) < 365:
        raise RuntimeError(f"Insufficient OHLCV rows for {coin}: {len(output)}")
    return output, source_urls


def main():
    project = Path(__file__).parents[1]
    data_dir = project / "data"
    data_dir.mkdir(exist_ok=True)
    metadata = {"dataset_type": "public_fallback_not_organizer_package", "provider": "Binance public market-data klines", "quote_asset": "USDT", "fetched_at": datetime.now(timezone.utc).isoformat(), "assets": {}}
    for coin in COINS:
        rows, urls = download(coin)
        path = data_dir / f"{coin}.csv"
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=["date", "open", "high", "low", "close", "volume"])
            writer.writeheader()
            writer.writerows(rows)
        metadata["assets"][coin] = {"rows": len(rows), "date_start": rows[0]["date"], "date_end": rows[-1]["date"], "source_urls": urls, "file": path.name}
    (data_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
