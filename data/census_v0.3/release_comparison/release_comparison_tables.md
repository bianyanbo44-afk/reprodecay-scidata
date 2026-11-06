Cohort: 345 projects present in both releases; 154 affected, 191 unaffected.

## Snapshot observation status, v0.2.4 to v0.3

| Measure | Snapshot | v0.2.4 | v0.3 | Snapshots |
|---|---|---|---|---|
| dependency | endpoint | complete | complete | 246 |
| dependency | endpoint | complete | incomplete | 19 |
| dependency | endpoint | incomplete | complete | 7 |
| dependency | endpoint | incomplete | incomplete | 73 |
| dependency | publication | complete | complete | 221 |
| dependency | publication | complete | incomplete | 29 |
| dependency | publication | incomplete | complete | 7 |
| dependency | publication | incomplete | incomplete | 88 |
| api | endpoint | complete | complete | 320 |
| api | endpoint | incomplete | complete | 14 |
| api | endpoint | incomplete | incomplete | 11 |
| api | publication | complete | complete | 322 |
| api | publication | incomplete | complete | 10 |
| api | publication | incomplete | incomplete | 13 |
| structure_python_constraints | endpoint | complete (row exists) | complete | 330 |
| structure_python_constraints | endpoint | complete (row exists) | incomplete | 15 |
| structure_python_constraints | publication | complete (row exists) | complete | 312 |
| structure_python_constraints | publication | complete (row exists) | incomplete | 33 |
| structure_build_backends | endpoint | complete (row exists) | complete | 345 |
| structure_build_backends | publication | complete (row exists) | complete | 345 |
| structure_source_line_count | endpoint | complete (row exists) | complete | 344 |
| structure_source_line_count | endpoint | complete (row exists) | incomplete | 1 |
| structure_source_line_count | publication | complete (row exists) | complete | 344 |
| structure_source_line_count | publication | complete (row exists) | incomplete | 1 |

## Dependency records

```json
{
  "key_comparison": {
    "key_only_in_v0_2_4": 70,
    "key_only_in_v0_3": 803,
    "key_value_changed": 34,
    "unchanged": 7571
  },
  "parse_status_v0_2_4": {
    "directive": 11,
    "parsed": 8864,
    "parsed_non_pep508": 130,
    "parsed_poetry": 20,
    "unparsed": 49
  },
  "parse_status_v0_3": {
    "directive": 48,
    "parsed": 9472,
    "parsed_non_pep508": 128,
    "parsed_pipfile": 71,
    "parsed_poetry": 130,
    "parsed_url": 5,
    "unparsed": 32
  },
  "snapshots_with_changed_row_count": 175,
  "v0_2_4_rows": 9074,
  "v0_3_rows": 9886
}
```

## Delta rows

| Family / feature group | Difference | Rows |
|---|---|---|
| api | row_only_in_v0_3 | 6 |
| api | status_incomplete_observation_to_comparable | 2146 |
| api | unchanged | 82742 |
| dependency | delta_type_changed | 26 |
| dependency | row_only_in_v0_2_4 | 50 |
| dependency | row_only_in_v0_3 | 610 |
| dependency | status_comparable_to_incomplete_observation | 366 |
| dependency | status_incomplete_observation_to_comparable | 182 |
| dependency | unchanged | 4701 |
| structure:build_backends | unchanged | 108 |
| structure:ci_providers | unchanged | 461 |
| structure:packaging_files | delta_type_changed | 1 |
| structure:packaging_files | row_only_in_v0_3 | 5 |
| structure:packaging_files | unchanged | 940 |
| structure:python_constraints | row_only_in_v0_3 | 11 |
| structure:python_constraints | status_comparable_to_incomplete_observation | 21 |
| structure:python_constraints | unchanged | 248 |
| structure:scalar | status_comparable_to_incomplete_observation | 1 |
| structure:scalar | unchanged | 3794 |

## Summary values

| Table | Item | v0.2.4 | v0.3 | Status |
|---|---|---|---|---|
| parsing_completeness_summary | api:endpoint_snapshot:complete_projects | 320 | 334 | changed |
| parsing_completeness_summary | api:paired_snapshots:complete_projects | 317 | 330 | changed |
| parsing_completeness_summary | api:publication_snapshot:complete_projects | 322 | 332 | changed |
| parsing_completeness_summary | dependency:endpoint_snapshot:complete_projects | 265 | 253 | changed |
| parsing_completeness_summary | dependency:paired_snapshots:complete_projects | 238 | 217 | changed |
| parsing_completeness_summary | dependency:publication_snapshot:complete_projects | 250 | 228 | changed |
| parsing_completeness_summary | structure:endpoint_snapshot:complete_projects | 345 | 329 | changed |
| parsing_completeness_summary | structure:paired_snapshots:complete_projects | 345 | 311 | changed |
| parsing_completeness_summary | structure:publication_snapshot:complete_projects | 345 | 311 | changed |
| paired_change_summary | source_python_line_count:all_available_values:included_pair_projects | 345 | 345 | unchanged |
| paired_change_summary | source_python_line_count:all_available_values:absolute_change_median | 193.0 | 193.0 | unchanged |
| paired_change_summary | source_python_line_count:all_available_values:bootstrap_median_95_lower | 87.0 | 87.0 | unchanged |
| paired_change_summary | source_python_line_count:all_available_values:bootstrap_median_95_upper | 370.0 | 370.0 | unchanged |
| paired_change_summary | source_python_line_count:adequately_observed_pairs:included_pair_projects | 345 | 344 | changed |
| paired_change_summary | source_python_line_count:adequately_observed_pairs:absolute_change_median | 193.0 | 203.5 | changed |
| paired_change_summary | source_python_line_count:adequately_observed_pairs:bootstrap_median_95_lower | 87.0 | 89.975 | changed |
| paired_change_summary | source_python_line_count:adequately_observed_pairs:bootstrap_median_95_upper | 370.0 | 370.0125 | changed |
| paired_change_summary | source_python_file_count:all_available_values:included_pair_projects | 345 | 345 | unchanged |
| paired_change_summary | source_python_file_count:all_available_values:absolute_change_median | 0.0 | 0.0 | unchanged |
| paired_change_summary | source_python_file_count:all_available_values:bootstrap_median_95_lower | 0.0 | 0.0 | unchanged |
| paired_change_summary | source_python_file_count:all_available_values:bootstrap_median_95_upper | 0.0 | 0.0 | unchanged |
| paired_change_summary | source_python_file_count:adequately_observed_pairs:included_pair_projects | 345 | 345 | unchanged |
| paired_change_summary | source_python_file_count:adequately_observed_pairs:absolute_change_median | 0.0 | 0.0 | unchanged |
| paired_change_summary | source_python_file_count:adequately_observed_pairs:bootstrap_median_95_lower | 0.0 | 0.0 | unchanged |
| paired_change_summary | source_python_file_count:adequately_observed_pairs:bootstrap_median_95_upper | 0.0 | 0.0 | unchanged |
| paired_change_summary | syntactic_api_symbol_count:all_available_values:included_pair_projects | 345 | 345 | unchanged |
| paired_change_summary | syntactic_api_symbol_count:all_available_values:absolute_change_median | 3.0 | 3.0 | unchanged |
| paired_change_summary | syntactic_api_symbol_count:all_available_values:bootstrap_median_95_lower | 1.0 | 1.0 | unchanged |
| paired_change_summary | syntactic_api_symbol_count:all_available_values:bootstrap_median_95_upper | 7.0 | 7.0 | unchanged |
| paired_change_summary | syntactic_api_symbol_count:adequately_observed_pairs:included_pair_projects | 345 | 330 | changed |
| paired_change_summary | syntactic_api_symbol_count:adequately_observed_pairs:absolute_change_median | 3.0 | 3.0 | unchanged |
| paired_change_summary | syntactic_api_symbol_count:adequately_observed_pairs:bootstrap_median_95_lower | 1.0 | 1.0 | unchanged |
| paired_change_summary | syntactic_api_symbol_count:adequately_observed_pairs:bootstrap_median_95_upper | 7.0 | 7.0 | unchanged |
| paired_change_summary | declared_dependency_record_count:all_available_values:included_pair_projects | 345 | 345 | unchanged |
| paired_change_summary | declared_dependency_record_count:all_available_values:absolute_change_median | 0.0 | 0.0 | unchanged |
| paired_change_summary | declared_dependency_record_count:all_available_values:bootstrap_median_95_lower | 0.0 | 0.0 | unchanged |
| paired_change_summary | declared_dependency_record_count:all_available_values:bootstrap_median_95_upper | 0.0 | 0.0 | unchanged |
| paired_change_summary | declared_dependency_record_count:adequately_observed_pairs:included_pair_projects | 345 | 217 | changed |
| paired_change_summary | declared_dependency_record_count:adequately_observed_pairs:absolute_change_median | 0.0 | 0.0 | unchanged |
| paired_change_summary | declared_dependency_record_count:adequately_observed_pairs:bootstrap_median_95_lower | 0.0 | 0.0 | unchanged |
| paired_change_summary | declared_dependency_record_count:adequately_observed_pairs:bootstrap_median_95_upper | 0.0 | 0.0 | unchanged |
| paired_change_summary | test_python_file_count:all_available_values:included_pair_projects | 345 | 345 | unchanged |
| paired_change_summary | test_python_file_count:all_available_values:absolute_change_median | 0.0 | 0.0 | unchanged |
| paired_change_summary | test_python_file_count:all_available_values:bootstrap_median_95_lower | 0.0 | 0.0 | unchanged |
| paired_change_summary | test_python_file_count:all_available_values:bootstrap_median_95_upper | 0.0 | 0.0 | unchanged |
| paired_change_summary | test_python_file_count:adequately_observed_pairs:included_pair_projects | 345 | 345 | unchanged |
| paired_change_summary | test_python_file_count:adequately_observed_pairs:absolute_change_median | 0.0 | 0.0 | unchanged |
| paired_change_summary | test_python_file_count:adequately_observed_pairs:bootstrap_median_95_lower | 0.0 | 0.0 | unchanged |
| paired_change_summary | test_python_file_count:adequately_observed_pairs:bootstrap_median_95_upper | 0.0 | 0.0 | unchanged |
| paired_change_summary | ci_workflow_file_count:all_available_values:included_pair_projects | 345 | 345 | unchanged |
| paired_change_summary | ci_workflow_file_count:all_available_values:absolute_change_median | 0.0 | 0.0 | unchanged |
| paired_change_summary | ci_workflow_file_count:all_available_values:bootstrap_median_95_lower | 0.0 | 0.0 | unchanged |
| paired_change_summary | ci_workflow_file_count:all_available_values:bootstrap_median_95_upper | 0.0 | 0.0 | unchanged |
| paired_change_summary | ci_workflow_file_count:adequately_observed_pairs:included_pair_projects | 345 | 345 | unchanged |
| paired_change_summary | ci_workflow_file_count:adequately_observed_pairs:absolute_change_median | 0.0 | 0.0 | unchanged |
| paired_change_summary | ci_workflow_file_count:adequately_observed_pairs:bootstrap_median_95_lower | 0.0 | 0.0 | unchanged |
| paired_change_summary | ci_workflow_file_count:adequately_observed_pairs:bootstrap_median_95_upper | 0.0 | 0.0 | unchanged |
| family_project_summary | api:added:all_extracted_projects_observed:projects_with_at_least_one | 219 | 219 | unchanged |
| family_project_summary | api:added:all_extracted_projects_observed:project_denominator | 345 | 345 | unchanged |
| family_project_summary | api:added:all_extracted_projects_observed:project_proportion | 0.634783 | 0.634783 | unchanged |
| family_project_summary | api:added:complete_family_comparisons:projects_with_at_least_one | 202 | 210 | changed |
| family_project_summary | api:added:complete_family_comparisons:project_denominator | 317 | 330 | changed |
| family_project_summary | api:added:complete_family_comparisons:project_proportion | 0.637224 | 0.636364 | changed |
| family_project_summary | api:changed:all_extracted_projects_observed:projects_with_at_least_one | 179 | 179 | unchanged |
| family_project_summary | api:changed:all_extracted_projects_observed:project_denominator | 345 | 345 | unchanged |
| family_project_summary | api:changed:all_extracted_projects_observed:project_proportion | 0.518841 | 0.518841 | unchanged |
| family_project_summary | api:changed:complete_family_comparisons:projects_with_at_least_one | 168 | 173 | changed |
| family_project_summary | api:changed:complete_family_comparisons:project_denominator | 317 | 330 | changed |
| family_project_summary | api:changed:complete_family_comparisons:project_proportion | 0.529968 | 0.524242 | changed |
| family_project_summary | api:removed:all_extracted_projects_observed:projects_with_at_least_one | 169 | 169 | unchanged |
| family_project_summary | api:removed:all_extracted_projects_observed:project_denominator | 345 | 345 | unchanged |
| family_project_summary | api:removed:all_extracted_projects_observed:project_proportion | 0.489855 | 0.489855 | unchanged |
| family_project_summary | api:removed:complete_family_comparisons:projects_with_at_least_one | 156 | 162 | changed |
| family_project_summary | api:removed:complete_family_comparisons:project_denominator | 317 | 330 | changed |
| family_project_summary | api:removed:complete_family_comparisons:project_proportion | 0.492114 | 0.490909 | changed |
| family_project_summary | api:unchanged:all_extracted_projects_observed:projects_with_at_least_one | 315 | 315 | unchanged |
| family_project_summary | api:unchanged:all_extracted_projects_observed:project_denominator | 345 | 345 | unchanged |
| family_project_summary | api:unchanged:all_extracted_projects_observed:project_proportion | 0.913043 | 0.913043 | unchanged |
| family_project_summary | api:unchanged:complete_family_comparisons:projects_with_at_least_one | 291 | 302 | changed |
| family_project_summary | api:unchanged:complete_family_comparisons:project_denominator | 317 | 330 | changed |
| family_project_summary | api:unchanged:complete_family_comparisons:project_proportion | 0.917981 | 0.915152 | changed |
| family_project_summary | dependency:added:all_extracted_projects_observed:projects_with_at_least_one | 179 | 182 | changed |
| family_project_summary | dependency:added:all_extracted_projects_observed:project_denominator | 345 | 345 | unchanged |
| family_project_summary | dependency:added:all_extracted_projects_observed:project_proportion | 0.518841 | 0.527536 | changed |
| family_project_summary | dependency:added:complete_family_comparisons:projects_with_at_least_one | 121 | 111 | changed |
| family_project_summary | dependency:added:complete_family_comparisons:project_denominator | 238 | 217 | changed |
| family_project_summary | dependency:added:complete_family_comparisons:project_proportion | 0.508403 | 0.511521 | changed |
| family_project_summary | dependency:changed:all_extracted_projects_observed:projects_with_at_least_one | 125 | 127 | changed |
| family_project_summary | dependency:changed:all_extracted_projects_observed:project_denominator | 345 | 345 | unchanged |
| family_project_summary | dependency:changed:all_extracted_projects_observed:project_proportion | 0.362319 | 0.368116 | changed |
| family_project_summary | dependency:changed:complete_family_comparisons:projects_with_at_least_one | 79 | 78 | changed |
| family_project_summary | dependency:changed:complete_family_comparisons:project_denominator | 238 | 217 | changed |
| family_project_summary | dependency:changed:complete_family_comparisons:project_proportion | 0.331933 | 0.359447 | changed |
| family_project_summary | dependency:removed:all_extracted_projects_observed:projects_with_at_least_one | 123 | 123 | unchanged |
| family_project_summary | dependency:removed:all_extracted_projects_observed:project_denominator | 345 | 345 | unchanged |
| family_project_summary | dependency:removed:all_extracted_projects_observed:project_proportion | 0.356522 | 0.356522 | unchanged |
| family_project_summary | dependency:removed:complete_family_comparisons:projects_with_at_least_one | 74 | 74 | unchanged |
| family_project_summary | dependency:removed:complete_family_comparisons:project_denominator | 238 | 217 | changed |
| family_project_summary | dependency:removed:complete_family_comparisons:project_proportion | 0.310924 | 0.341014 | changed |
| family_project_summary | dependency:unchanged:all_extracted_projects_observed:projects_with_at_least_one | 253 | 261 | changed |
| family_project_summary | dependency:unchanged:all_extracted_projects_observed:project_denominator | 345 | 345 | unchanged |
| family_project_summary | dependency:unchanged:all_extracted_projects_observed:project_proportion | 0.733333 | 0.756522 | changed |
| family_project_summary | dependency:unchanged:complete_family_comparisons:projects_with_at_least_one | 179 | 173 | changed |
| family_project_summary | dependency:unchanged:complete_family_comparisons:project_denominator | 238 | 217 | changed |
| family_project_summary | dependency:unchanged:complete_family_comparisons:project_proportion | 0.752101 | 0.797235 | changed |
| family_project_summary | structure:added:all_extracted_projects_observed:projects_with_at_least_one | 183 | 183 | unchanged |
| family_project_summary | structure:added:all_extracted_projects_observed:project_denominator | 345 | 345 | unchanged |
| family_project_summary | structure:added:all_extracted_projects_observed:project_proportion | 0.530435 | 0.530435 | unchanged |
| family_project_summary | structure:added:complete_family_comparisons:projects_with_at_least_one | 183 | 157 | changed |
| family_project_summary | structure:added:complete_family_comparisons:project_denominator | 345 | 311 | changed |
| family_project_summary | structure:added:complete_family_comparisons:project_proportion | 0.530435 | 0.504823 | changed |
| family_project_summary | structure:changed:all_extracted_projects_observed:projects_with_at_least_one | 242 | 242 | unchanged |
| family_project_summary | structure:changed:all_extracted_projects_observed:project_denominator | 345 | 345 | unchanged |
| family_project_summary | structure:changed:all_extracted_projects_observed:project_proportion | 0.701449 | 0.701449 | unchanged |
| family_project_summary | structure:changed:complete_family_comparisons:projects_with_at_least_one | 242 | 214 | changed |
| family_project_summary | structure:changed:complete_family_comparisons:project_denominator | 345 | 311 | changed |
| family_project_summary | structure:changed:complete_family_comparisons:project_proportion | 0.701449 | 0.688103 | changed |
| family_project_summary | structure:removed:all_extracted_projects_observed:projects_with_at_least_one | 169 | 169 | unchanged |
| family_project_summary | structure:removed:all_extracted_projects_observed:project_denominator | 345 | 345 | unchanged |
| family_project_summary | structure:removed:all_extracted_projects_observed:project_proportion | 0.489855 | 0.489855 | unchanged |
| family_project_summary | structure:removed:complete_family_comparisons:projects_with_at_least_one | 169 | 145 | changed |
| family_project_summary | structure:removed:complete_family_comparisons:project_denominator | 345 | 311 | changed |
| family_project_summary | structure:removed:complete_family_comparisons:project_proportion | 0.489855 | 0.466238 | changed |
| family_project_summary | structure:unchanged:all_extracted_projects_observed:projects_with_at_least_one | 345 | 345 | unchanged |
| family_project_summary | structure:unchanged:all_extracted_projects_observed:project_denominator | 345 | 345 | unchanged |
| family_project_summary | structure:unchanged:all_extracted_projects_observed:project_proportion | 1.0 | 1.0 | unchanged |
| family_project_summary | structure:unchanged:complete_family_comparisons:projects_with_at_least_one | 345 | 311 | changed |
| family_project_summary | structure:unchanged:complete_family_comparisons:project_denominator | 345 | 311 | changed |
| family_project_summary | structure:unchanged:complete_family_comparisons:project_proportion | 1.0 | 1.0 | unchanged |
