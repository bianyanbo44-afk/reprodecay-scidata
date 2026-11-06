import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from summarize_census_dataset import (  # noqa: E402
    BOOTSTRAP_REPLICATES,
    BOOTSTRAP_SEED,
    build_summary,
    sensitivity_project_sets,
)


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames=None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = fieldnames or list(rows[0])
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


class CensusSummaryTests(unittest.TestCase):
    def fixture(self, directory: str, *, executed: str = "false") -> dict[str, Path]:
        root = Path(directory)
        tables = root / "tables"
        summary_dir = root / "summary"
        projects = [
            {
                "project_id": "p1", "pair_id": "pair1", "scholarly_doi": "10.1/one",
                "selection_rank": "1", "endpoint_ref_type": "release",
            },
            {
                "project_id": "p2", "pair_id": "pair2", "scholarly_doi": "10.1/two",
                "selection_rank": "2", "endpoint_ref_type": "default_branch",
            },
            {
                "project_id": "p3", "pair_id": "pair3", "scholarly_doi": "10.1/three",
                "selection_rank": "3", "endpoint_ref_type": "release",
            },
        ]
        snapshots = []
        structures = []
        metric_values = {
            "p1": ((0, 1, 0, 1, 0, 0), (10, 2, 2, 2, 1, 1)),
            "p2": ((100, 10, 5, 4, 2, 1), (110, 12, 4, 8, 2, 2)),
            "p3": ((1000, 100, 20, 10, 5, 4), (900, 90, 22, 12, 4, 4)),
        }
        for project_id, roles in metric_values.items():
            for role, values in zip(("publication", "endpoint"), roles):
                snapshot_id = f"{project_id}_{role}"
                snapshots.append(
                    {
                        "snapshot_id": snapshot_id,
                        "project_id": project_id,
                        "pair_id": f"pair{project_id[-1]}",
                        "snapshot_role": role,
                        "archive_bytes": str({"p1": 10, "p2": 100, "p3": 1000}[project_id]),
                        "third_party_code_executed": executed,
                    }
                )
                structures.append(
                    {
                        "snapshot_id": snapshot_id,
                        "project_id": project_id,
                        "snapshot_role": role,
                        "source_python_line_count": str(values[0]),
                        "source_python_file_count": str(values[1]),
                        "syntactic_api_symbol_count": str(values[2]),
                        "declared_dependency_record_count": str(values[3]),
                        "test_python_file_count": str(values[4]),
                        "ci_workflow_file_count": str(values[5]),
                        "archive_listing_status": "complete",
                        "build_backend_observation_status": "complete",
                        "python_constraint_observation_status": "complete",
                        "source_line_observation_status": "complete",
                        "api_observation_status": "complete",
                        "dependency_observation_status": "incomplete" if project_id == "p2" and role == "endpoint" else "complete",
                        "third_party_code_executed": executed,
                    }
                )
        deltas = [
            {
                "pair_id": "pair1", "project_id": "p1", "family": "api",
                "feature_group": "syntactic_symbol", "feature_key": "a", "delta_type": "added",
                "comparison_status": "comparable",
            },
            {
                "pair_id": "pair1", "project_id": "p1", "family": "api",
                "feature_group": "syntactic_symbol", "feature_key": "b", "delta_type": "added",
                "comparison_status": "comparable",
            },
            {
                "pair_id": "pair2", "project_id": "p2", "family": "api",
                "feature_group": "syntactic_symbol", "feature_key": "a", "delta_type": "unchanged",
                "comparison_status": "comparable",
            },
            {
                "pair_id": "pair3", "project_id": "p3", "family": "api",
                "feature_group": "syntactic_symbol", "feature_key": "a", "delta_type": "changed",
                "comparison_status": "comparable",
            },
            {
                "pair_id": "pair1", "project_id": "p1", "family": "dependency",
                "feature_group": "declared_dependency", "feature_key": "x", "delta_type": "added",
                "comparison_status": "comparable",
            },
            {
                "pair_id": "pair2", "project_id": "p2", "family": "dependency",
                "feature_group": "declared_dependency", "feature_key": "x", "delta_type": "removed",
                "comparison_status": "incomplete_observation",
            },
        ]
        write_csv(tables / "project_records.csv", projects)
        write_csv(tables / "snapshot_records.csv", snapshots)
        write_csv(tables / "structure_records.csv", structures)
        write_csv(
            tables / "dependency_records.csv", [],
            ["snapshot_id", "project_id", "snapshot_role"],
        )
        write_csv(
            tables / "api_symbol_records.csv", [],
            ["snapshot_id", "project_id", "snapshot_role"],
        )
        write_csv(tables / "pair_delta_records.csv", deltas)
        write_csv(
            tables / "declaration_source_records.csv",
            [
                {
                    "snapshot_id": "p2_endpoint", "project_id": "p2", "snapshot_role": "endpoint",
                    "source_file": "setup.py", "source_kind": "setup_py", "field": "setup.install_requires",
                    "field_kind": "dependency", "observation_status": "unsupported_syntax",
                    "entry_count": "0", "unparsed_entry_count": "0", "detail": "name:reqs",
                }
            ],
        )
        write_csv(
            tables / "validation_records.csv",
            [
                {
                    "snapshot_id": "p1_publication", "project_id": "p1",
                    "snapshot_role": "publication", "check_name": "archive_sha256",
                    "status": "pass", "evidence": "x", "detail": "ok",
                }
            ],
        )
        screening = [
            {"doi": "10.1/one", "screen_status": "eligible_metadata", "reason_code": "", "third_party_code_executed": "false"},
            {"doi": "10.1/two", "screen_status": "eligible_metadata", "reason_code": "", "third_party_code_executed": "false"},
            {"doi": "10.1/three", "screen_status": "eligible_metadata", "reason_code": "", "third_party_code_executed": "false"},
            {"doi": "10.1/four", "screen_status": "excluded", "reason_code": "no_packaging", "third_party_code_executed": "false"},
            {"doi": "10.1/five", "screen_status": "unresolved", "reason_code": "not_found", "third_party_code_executed": "false"},
        ]
        archive = [
            {"scholarly_doi": "10.1/one", "final_eligibility": "eligible_archive", "reason_code": "", "third_party_code_executed": "false"},
            {"scholarly_doi": "10.1/two", "final_eligibility": "eligible_archive", "reason_code": "", "third_party_code_executed": "false"},
            {"scholarly_doi": "10.1/three", "final_eligibility": "eligible_archive", "reason_code": "", "third_party_code_executed": "false"},
        ]
        endpoints = [
            {"scholarly_doi": "10.1/one", "resolution_status": "resolved", "resolution_error": "", "endpoint_ref_type": "release", "third_party_code_executed": "false"},
            {"scholarly_doi": "10.1/two", "resolution_status": "resolved", "resolution_error": "", "endpoint_ref_type": "default_branch", "third_party_code_executed": "false"},
            {"scholarly_doi": "10.1/three", "resolution_status": "resolved", "resolution_error": "", "endpoint_ref_type": "release", "third_party_code_executed": "false"},
        ]
        screening_path = root / "screening.csv"
        archive_path = root / "archive.csv"
        endpoint_path = root / "endpoint.csv"
        write_csv(screening_path, screening)
        write_csv(archive_path, archive)
        write_csv(endpoint_path, endpoints)
        return {
            "tables": tables,
            "summary_dir": summary_dir,
            "output": root / "summary.json",
            "screening": screening_path,
            "archive": archive_path,
            "endpoint": endpoint_path,
        }

    def run_fixture(self, paths: dict[str, Path]) -> dict[str, object]:
        return build_summary(
            paths["tables"], paths["output"], paths["summary_dir"],
            paths["screening"], paths["archive"], paths["endpoint"],
        )

    def test_uses_projects_not_delta_rows_as_family_denominator(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = self.fixture(directory)
            self.run_fixture(paths)
            rows = read_csv(paths["summary_dir"] / "family_project_summary.csv")
            api_added = next(
                row for row in rows
                if row["family"] == "api" and row["delta_type"] == "added"
                and row["scope"] == "complete_family_comparisons"
            )
            self.assertEqual(api_added["projects_with_at_least_one"], "1")
            self.assertEqual(api_added["project_denominator"], "3")
            self.assertEqual(api_added["project_proportion"], "0.333333")
            api_added_all = next(
                row for row in rows
                if row["family"] == "api" and row["delta_type"] == "added"
                and row["scope"] == "all_extracted_projects_observed"
            )
            self.assertEqual(api_added_all["projects_missing_or_incomplete"], "0")
            dependency_all = next(
                row for row in rows
                if row["family"] == "dependency" and row["delta_type"] == "added"
                and row["scope"] == "all_extracted_projects_observed"
            )
            self.assertEqual(dependency_all["projects_missing_or_incomplete"], "1")

    def test_emits_attrition_parsing_endpoint_and_paired_denominators(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = self.fixture(directory)
            result = self.run_fixture(paths)
            attrition = read_csv(paths["summary_dir"] / "selection_attrition.csv")
            self.assertEqual([row["retained_projects"] for row in attrition], ["5", "3", "3", "3", "3"])
            parsing = read_csv(paths["summary_dir"] / "parsing_completeness_summary.csv")
            paired_dependency = next(
                row for row in parsing
                if row["family"] == "dependency" and row["scope"] == "paired_snapshots"
            )
            self.assertEqual(paired_dependency["complete_projects"], "2")
            self.assertEqual(paired_dependency["incomplete_projects"], "1")
            endpoint = read_csv(paths["summary_dir"] / "endpoint_type_summary.csv")
            extracted_release = next(
                row for row in endpoint
                if row["scope"] == "extracted_project_pairs" and row["endpoint_ref_type"] == "release"
            )
            self.assertEqual(extracted_release["project_count"], "2")
            self.assertEqual(extracted_release["project_denominator"], "3")
            paired = read_csv(paths["summary_dir"] / "publication_endpoint_paired_changes.csv")
            zero_baseline = next(
                row for row in paired
                if row["project_id"] == "p1" and row["metric"] == "source_python_line_count"
            )
            self.assertEqual(zero_baseline["proportional_change_from_publication"], "")
            self.assertEqual(
                zero_baseline["proportional_change_status"],
                "undefined_zero_publication_denominator",
            )
            self.assertEqual(result["analysis_design"]["bootstrap_seed"], BOOTSTRAP_SEED)
            self.assertEqual(result["analysis_design"]["bootstrap_replicates"], BOOTSTRAP_REPLICATES)
            self.assertFalse(result["analysis_design"]["significance_tests_performed"])
            self.assertIn("source_python_lines", result["median_change"])

    def test_record_volume_and_adequately_observed_summaries_are_separate(self):
        # Reviewer 1: numerical availability must not be reported as parsing completeness.
        with tempfile.TemporaryDirectory() as directory:
            paths = self.fixture(directory)
            self.run_fixture(paths)
            rows = read_csv(paths["summary_dir"] / "paired_change_summary.csv")
            dependency = {
                row["summary_basis"]: row for row in rows if row["metric"] == "declared_dependency_record_count"
            }
            self.assertEqual(dependency["all_available_values"]["included_pair_projects"], "3")
            self.assertEqual(dependency["adequately_observed_pairs"]["included_pair_projects"], "2")
            self.assertEqual(
                json.loads(dependency["adequately_observed_pairs"]["exclusion_reason_counts"]),
                {"incomplete_endpoint": 1},
            )
            lines = {row["summary_basis"]: row for row in rows if row["metric"] == "source_python_line_count"}
            self.assertEqual(lines["adequately_observed_pairs"]["included_pair_projects"], "3")
            fields = read_csv(paths["summary_dir"] / "field_observation_summary.csv")
            paired = next(row for row in fields
                          if row["status_field"] == "dependency_observation_status" and row["scope"] == "paired_snapshots")
            self.assertEqual((paired["complete_projects"], paired["incomplete_projects"]), ("2", "1"))
            declarations = read_csv(paths["summary_dir"] / "declaration_status_summary.csv")
            self.assertEqual(declarations[0]["observation_status"], "unsupported_syntax")

    def test_large_project_scenarios_use_explicit_ceil_exclusion(self):
        rows = [
            {
                "project_id": f"p{number:03d}",
                "publication_archive_bytes": number,
            }
            for number in range(1, 101)
        ]
        scenarios = sensitivity_project_sets(rows, "publication_archive_bytes")
        by_name = {row["scenario"]: row for row in scenarios}
        top_one = by_name["exclude_top_1_percent_by_publication_archive_bytes"]
        top_five = by_name["exclude_top_5_percent_by_publication_archive_bytes"]
        self.assertEqual(top_one["excluded_projects"], 1)
        self.assertEqual(top_five["excluded_projects"], 5)
        self.assertEqual(top_one["excluded_project_ids"], "p100")

    def test_bootstrap_outputs_are_byte_deterministic(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            first_paths = self.fixture(first)
            second_paths = self.fixture(second)
            self.run_fixture(first_paths)
            self.run_fixture(second_paths)
            first_bytes = (first_paths["summary_dir"] / "large_project_sensitivity.csv").read_bytes()
            second_bytes = (second_paths["summary_dir"] / "large_project_sensitivity.csv").read_bytes()
            self.assertEqual(first_bytes, second_bytes)

    def test_refuses_input_that_does_not_confirm_static_only_protocol(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = self.fixture(directory, executed="true")
            with self.assertRaisesRegex(ValueError, "third_party_code_execution_not_false"):
                self.run_fixture(paths)


if __name__ == "__main__":
    unittest.main()
