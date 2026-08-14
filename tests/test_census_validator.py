import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from validate_census_frame_v02 import (  # noqa: E402
    ALLOWED_EXCLUSION_REASONS,
    ALLOWED_UNRESOLVED_REASONS,
)


class CensusValidatorTests(unittest.TestCase):
    def test_reason_vocabularies_do_not_overlap(self):
        self.assertTrue(ALLOWED_EXCLUSION_REASONS)
        self.assertTrue(ALLOWED_UNRESOLVED_REASONS)
        self.assertFalse(ALLOWED_EXCLUSION_REASONS & ALLOWED_UNRESOLVED_REASONS)


if __name__ == "__main__":
    unittest.main()
