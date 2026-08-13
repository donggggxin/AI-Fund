import tempfile
import unittest
from pathlib import Path

from storage import load_json, save_json


class StorageTests(unittest.TestCase):
    def test_atomic_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            save_json(path, {"中文": 1})
            self.assertEqual(load_json(path), {"中文": 1})
            self.assertEqual(list(path.parent.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
