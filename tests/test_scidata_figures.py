import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from make_scidata_figures_v03 import dependency_status_snapshot_counts


class DeclarationStatusFigureTests(unittest.TestCase):
    def test_snapshot_union_across_field_kinds_and_roles(self):
        rows = [
            {"snapshot_id":"p1","snapshot_role":"publication","field_kind":"dependency","observation_status":"unsupported_syntax"},
            {"snapshot_id":"p1","snapshot_role":"publication","field_kind":"setup_kwargs","observation_status":"unsupported_syntax"},
            {"snapshot_id":"p2","snapshot_role":"publication","field_kind":"setup_kwargs","observation_status":"unsupported_syntax"},
            {"snapshot_id":"e1","snapshot_role":"endpoint","field_kind":"file","observation_status":"parse_failure"},
            {"snapshot_id":"p3","snapshot_role":"publication","field_kind":"python_constraint","observation_status":"unsupported_syntax"},
        ]
        counts = dependency_status_snapshot_counts(rows)
        self.assertEqual(counts[("publication","unsupported_syntax")], 2)
        self.assertEqual(counts[("endpoint","parse_failure")], 1)
        self.assertEqual(counts[("publication","parse_failure")], 0)


if __name__ == "__main__":
    unittest.main()
