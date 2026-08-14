from scripts.independent_semantic_audit import counter_agreement, dependency_field_agreement


def test_dependency_agreement_retains_duplicate_rows() -> None:
    row = ("requirements.txt", "requirements", "runtime", "numpy", "numpy>=1.20", ">=1.20", "", "[]", "complete")
    expected = [row, row]
    observed = [row]

    assert counter_agreement(expected, observed) == 1
    assert dependency_field_agreement(expected, observed, 4) == 1


def test_dependency_field_agreement_detects_a_changed_field() -> None:
    expected = [("requirements.txt", "requirements", "runtime", "numpy", "numpy>=1.20", ">=1.20", "", "[]", "complete")]
    observed = [("requirements.txt", "requirements", "runtime", "numpy", "numpy>=2.0", ">=1.20", "", "[]", "complete")]

    assert counter_agreement(expected, observed) == 0
    assert dependency_field_agreement(expected, observed, 4) == 0
