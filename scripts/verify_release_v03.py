"""Verify a v0.3.0 derived-data package without downloading source archives."""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def verify(root: Path, require_published: bool = False, strict_files: bool = False) -> dict:
    root = root.resolve()
    failures = []
    package = json.loads((root / 'package_manifest.json').read_text(encoding='utf-8'))
    managed = set()
    for item in package['files']:
        relative = item['path']
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            failures.append('missing_or_unsafe:' + relative)
        elif path.stat().st_size != item['bytes'] or sha256(path) != item['sha256']:
            failures.append('hash_mismatch:' + relative)
        managed.add(relative)
    actual = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()
              and not {'__pycache__', '.pytest_cache'}.intersection(p.relative_to(root).parts)
              and p.suffix not in {'.pyc', '.pyo'}}
    if strict_files:
        for name in sorted(actual - managed - {'package_manifest.json', 'checksums.sha256'}):
            failures.append('unmanaged_file:' + name)
    if any('raw' in Path(name).parts or 'sources' in Path(name).parts for name in actual):
        failures.append('third_party_archive_or_source_present')
    if failures:
        return {'status':'FAIL','files_verified':len(managed),'failures':failures}
    dataset = root / 'data/census_v0.3'
    manifest = json.loads((dataset / 'tables/extraction_manifest.json').read_text(encoding='utf-8'))
    counts = {}
    for table in manifest['tables']:
        path = dataset / 'tables' / table
        with path.open(encoding='utf-8-sig', newline='') as stream:
            counts[table] = sum(1 for _ in csv.DictReader(stream))
        count_key = {'project_records.csv':'projects','snapshot_records.csv':'snapshots'}.get(table, table.removesuffix('.csv'))
        if counts[table] != manifest[count_key]:
            failures.append('row_count:' + table)
        if sha256(path) != manifest['table_sha256'][table]:
            failures.append('table_hash:' + table)
    if manifest['third_party_code_executed'] is not False:
        failures.append('execution_boundary')
    report = json.loads((dataset / 'census_dataset_validation.json').read_text(encoding='utf-8'))
    if report['status'] != 'PASS' or not report.get('deterministic_rerun_byte_identical'):
        failures.append('canonical_validation')
    independent = json.loads((dataset / 'independent_semantic_audit.json').read_text(encoding='utf-8'))
    if independent['status'] != 'PASS':
        failures.append('independent_audit')
    reference = json.loads((dataset / 'reference_audit_summary.json').read_text(encoding='utf-8'))
    if reference['discrepancies'] or reference['verified_snapshots'] != reference['sampled_snapshots']:
        failures.append('reference_audit')
    descriptor = json.loads((root / 'schema/datapackage.json').read_text(encoding='utf-8'))
    if len(descriptor['resources']) != 8:
        failures.append('schema_resource_count')
    for resource in descriptor['resources']:
        with (root / 'schema' / resource['path']).open(encoding='utf-8-sig', newline='') as stream:
            reader = csv.DictReader(stream)
            headers = reader.fieldnames
            enums = {f['name']:set(f.get('constraints',{}).get('enum',[]))
                     for f in resource['schema']['fields'] if f.get('constraints',{}).get('enum')}
            unknown = {name:set() for name in enums}
            for row in reader:
                for name, allowed in enums.items():
                    if row[name] not in allowed:unknown[name].add(row[name])
            for name, values in unknown.items():
                if values:failures.append('schema_enum:'+resource['name']+'.'+name+':'+','.join(sorted(values)))
        if headers != [f['name'] for f in resource['schema']['fields']]:
            failures.append('schema_headers:' + resource['name'])
    pending = not package.get('dataset_doi')
    if require_published and pending:
        failures.append('archival_doi_pending')
    return {'status': 'PASS' if not failures else 'FAIL', 'files_verified':len(managed),
            'table_rows':counts, 'dataset_doi_in_package':package.get('dataset_doi'),
            'failures':failures}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path('.'))
    parser.add_argument('--require-published', action='store_true')
    parser.add_argument('--strict-files', action='store_true', help='reject files outside the derived-release manifest')
    args = parser.parse_args()
    result = verify(args.root, args.require_published, args.strict_files)
    print(json.dumps(result, indent=2))
    raise SystemExit(result['status'] != 'PASS')
