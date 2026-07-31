import csv
import json
import unittest
from pathlib import Path

from src.ohlcv import load_ohlcv


class DownloadedDataTest(unittest.TestCase):
    def test_all_five_public_fallback_datasets(self):
        data_dir = Path(__file__).parents[1] / "data"
        metadata = json.loads((data_dir / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["dataset_type"], "public_fallback_not_organizer_package")
        for coin in ("BTC", "ETH", "SOL", "BNB", "XRP"):
            path = data_dir / f"{coin}.csv"
            with path.open(encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 1826)
            self.assertEqual(len(load_ohlcv(path, coin, days=15)), 15)
