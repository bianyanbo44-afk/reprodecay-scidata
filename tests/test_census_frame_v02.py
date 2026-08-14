import csv
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from build_candidate_frame import ScreenRecord  # noqa: E402
from build_census_frame_v02 import (  # noqa: E402
    atomic_write_csv,
    read_existing,
    recover_temporary_checkpoint,
    retryable,
)


class CensusFrameTests(unittest.TestCase):
    def test_atomic_csv_round_trip_preserves_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.csv"
            rows = [
                ScreenRecord(
                    rank=1,
                    selection_hash="abc",
                    doi="10.1/example",
                    title="Example",
                    publication_date="2020-01-01",
                    joss_url="https://example.invalid",
                    screen_status="excluded",
                    reason_code="no_repository_link",
                )
            ]
            atomic_write_csv(path, rows)
            loaded = read_existing(path)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].doi, "10.1/example")
            self.assertFalse(path.with_suffix(".csv.tmp").exists())

            with path.open(encoding="utf-8-sig", newline="") as handle:
                raw = list(csv.DictReader(handle))
            self.assertEqual(raw[0]["reason_code"], "no_repository_link")

    def test_only_transient_unresolved_records_are_retryable(self):
        transient = ScreenRecord(
            rank=1, selection_hash="a", doi="10.1/a", title="A",
            publication_date="2020-01-01", joss_url="https://example.invalid",
            screen_status="unresolved", reason_code="network_or_parse_TimeoutError",
        )
        permanent = ScreenRecord(
            rank=2, selection_hash="b", doi="10.1/b", title="B",
            publication_date="2020-01-01", joss_url="https://example.invalid",
            screen_status="unresolved", reason_code="repository_or_article_not_found",
        )
        self.assertTrue(retryable(transient))
        self.assertFalse(retryable(permanent))

    def test_newer_temporary_checkpoint_is_promoted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.csv"
            first = ScreenRecord(
                rank=1, selection_hash="a", doi="10.1/a", title="A",
                publication_date="2020-01-01", joss_url="https://example.invalid/a",
                screen_status="excluded", reason_code="no_repository_link",
            )
            second = ScreenRecord(
                rank=2, selection_hash="b", doi="10.1/b", title="B",
                publication_date="2020-01-02", joss_url="https://example.invalid/b",
                screen_status="eligible_metadata", selected_role="census_provisional",
            )
            atomic_write_csv(path, [first])
            atomic_write_csv(path.with_suffix(".csv.tmp"), [first, second])
            self.assertEqual(recover_temporary_checkpoint(path), 1)
            self.assertEqual([record.doi for record in read_existing(path)], ["10.1/a", "10.1/b"])
            self.assertFalse(path.with_suffix(".csv.tmp").exists())

    def test_divergent_temporary_checkpoint_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.csv"
            first = ScreenRecord(
                rank=1, selection_hash="a", doi="10.1/a", title="A",
                publication_date="2020-01-01", joss_url="https://example.invalid/a",
                screen_status="excluded", reason_code="no_repository_link",
            )
            changed = ScreenRecord(
                rank=1, selection_hash="a", doi="10.1/a", title="changed",
                publication_date="2020-01-01", joss_url="https://example.invalid/a",
                screen_status="excluded", reason_code="no_repository_link",
            )
            second = ScreenRecord(
                rank=2, selection_hash="b", doi="10.1/b", title="B",
                publication_date="2020-01-02", joss_url="https://example.invalid/b",
                screen_status="eligible_metadata", selected_role="census_provisional",
            )
            atomic_write_csv(path, [first])
            atomic_write_csv(path.with_suffix(".csv.tmp"), [changed, second])
            with self.assertRaisesRegex(ValueError, "temporary_checkpoint_diverges"):
                recover_temporary_checkpoint(path)


if __name__ == "__main__":
    unittest.main()
