from __future__ import annotations

import csv
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DESCRIPTOR = ROOT / "schema" / "datapackage.json"


class DataPackageSchemaTests(unittest.TestCase):
    def test_all_core_tables_have_exact_machine_readable_schemas(self) -> None:
        descriptor = json.loads(DESCRIPTOR.read_text(encoding="utf-8"))
        resources = descriptor["resources"]
        self.assertEqual(len(resources), 7)
        names = {resource["name"] for resource in resources}
        self.assertEqual(names, {
            "project-records", "snapshot-records", "structure-records",
            "dependency-records", "api-symbol-records", "pair-delta-records",
            "validation-records",
        })
        for resource in resources:
            path = (DESCRIPTOR.parent / resource["path"]).resolve()
            self.assertTrue(path.is_file(), path)
            with path.open(encoding=resource["encoding"], newline="") as handle:
                reader = csv.DictReader(handle)
                actual_fields = reader.fieldnames
            declared_fields = [field["name"] for field in resource["schema"]["fields"]]
            self.assertEqual(actual_fields, declared_fields, resource["name"])

    def test_declared_enums_cover_observed_values(self) -> None:
        descriptor = json.loads(DESCRIPTOR.read_text(encoding="utf-8"))
        for resource in descriptor["resources"]:
            fields = {
                field["name"]: field.get("constraints", {}).get("enum")
                for field in resource["schema"]["fields"]
                if field.get("constraints", {}).get("enum") is not None
            }
            path = (DESCRIPTOR.parent / resource["path"]).resolve()
            with path.open(encoding=resource["encoding"], newline="") as handle:
                rows = csv.DictReader(handle)
                for row in rows:
                    for name, allowed in fields.items():
                        value: object = row[name]
                        if allowed and isinstance(allowed[0], bool):
                            value = row[name].lower() == "true"
                        self.assertIn(value, allowed, f"{resource['name']}.{name}={row[name]}")

    def test_legacy_execution_schemas_are_not_released(self) -> None:
        self.assertFalse((ROOT / "schema" / "execution_record.schema.json").exists())
        self.assertFalse((ROOT / "schema" / "failure_annotation.schema.json").exists())
        self.assertFalse((ROOT / "schema" / "project_record.schema.json").exists())


if __name__ == "__main__":
    unittest.main()
