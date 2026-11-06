# Static extraction specification, v0.3

This document defines what the v0.3 extractor is intended to observe. It is the
reference for the regression tests and for the reference-based audit: expected
outcomes are derived from the source files and this text, not from the output of
any parser.

All observations are static. Archive members are read as bytes and parsed as
text or syntax; nothing is imported, installed, built or executed.

## 1. Archive view

- The archive root is the archive's top level after removing a single common
  top directory, if every member sits below one.
- Members under `__MACOSX/` and the basenames `.DS_Store` and `Thumbs.db` are
  ignored.
- A text member larger than 2 MiB, or one that would take the cumulative text
  budget above 128 MiB, is not read. It still appears in the member listing.
- A text member that is a symbolic link (or a tar hard link) is read as the
  regular member it points to, as it would appear after unpacking. A symbolic
  link target is taken relative to the link's directory, a hard link target
  relative to the archive; chains of up to 8 links are followed. A link whose
  target is not a regular member of the same archive (missing, outside the
  archive, a directory, or a longer chain) is listed but not read. In a zip
  archive a symbolic link stores its target as member content; that content is
  the link target, never the text of the member.
- The member listing is complete for every snapshot that is extracted. Archives
  with more than 100,000 members are rejected rather than partially listed.
- For every member file the extractor records the normalized path, byte size
  and SHA-256 of the member content. A symbolic link is hashed as the UTF-8
  bytes `link:` followed by its target. The **content tree digest** is the
  SHA-256 of the UTF-8 text formed by sorting members by path and joining one
  line per member, `path<TAB>size<TAB>sha256<LF>`. Two archives with the same
  content tree digest contain the same files at the same paths, even when the
  archive bytes differ (for example, when a hosting service re-creates a
  tarball).

## 2. Declaration sources

Only files at the archive root are declaration sources. File names are
matched without regard to case (`pipfile` is a Pipfile, `Requirements.txt` a
requirements file). The supported files and fields are listed below. Fields are named by their path in the file.

| File | Field | Dependency group |
|---|---|---|
| `pyproject.toml` | `build-system.requires` | `build` |
| | `build-system.build-backend` | (build backend, not a dependency) |
| | `project.dependencies` | `runtime` |
| | `project.optional-dependencies.<extra>` | `optional:<extra>` |
| | `project.requires-python` | (Python constraint) |
| | `project.dynamic` | (marks other `project.*` fields as build-time) |
| | `tool.poetry.dependencies` | `runtime`, or `optional:<extra>` for entries with `optional = true` (Section 3.3) |
| | `tool.poetry.dependencies.python` | (Python constraint) |
| | `tool.poetry.dev-dependencies` | `development` |
| | `tool.poetry.group.<name>.dependencies` | see Section 3.4 |
| | `tool.poetry.extras` | (assigns optional Poetry entries to extras) |
| | `tool.flit.metadata.requires` | `runtime` |
| | `tool.flit.metadata.requires-extra.<extra>` | `optional:<extra>` |
| | `tool.flit.metadata.requires-python` | (Python constraint) |
| | `tool.setuptools.dynamic.dependencies`, `.optional-dependencies` | build-time reference |
| | `dependency-groups.<name>` (PEP 735) | as for Poetry groups (Section 3.4); an `{include-group = ...}` entry is a directive, because the included group is a field of its own |
| `setup.cfg` | `options.install_requires` | `runtime` |
| | `options.extras_require.<extra>` (section `[options.extras_require]`) | `optional:<extra>` |
| | `options.setup_requires` | `build` |
| | `options.tests_require` | `test` |
| | `options.python_requires` | (Python constraint) |
| `setup.py` | `setup(install_requires=...)` | `runtime` |
| | `setup(extras_require=...)` | `optional:<extra>` |
| | `setup(setup_requires=...)` | `build` |
| | `setup(tests_require=...)` | `test` |
| | `setup(python_requires=...)` | (Python constraint) |
| `requirements*.txt`, `requirements*.in` | each line | `test` if the file name contains `test`; `development` if it contains `dev`, `lint`, `docs` or `doc`; otherwise `runtime` |
| `environment.yml`, `environment.yaml` | `dependencies` entries; nested `pip:` lists | `runtime` |
| | `dependencies` entry `python...` | (Python constraint) |
| `Pipfile` | `packages` | `runtime` |
| | `dev-packages` | `development` |
| | `requires.python_version`, `requires.python_full_version` | (Python constraint) |

Files in subdirectories, `tox.ini`, `noxfile.py`, conda recipes (`meta.yaml`)
and CI files are not declaration sources.

### 2.1 Static evaluation of `setup.py`

`setup.py` is parsed with the Python `ast` module. The setup call is the last
call whose callee is the name `setup` or an attribute named `setup`. A setup
call that appears only inside a function body counts.

The value of a keyword argument is statically evaluable when it is:

1. a Python literal (string, number, list, tuple, set or dict of literals);
2. a name bound by a top-level assignment to a statically evaluable value, where
   the assignment precedes the top-level statement that contains the setup call
   (any top-level assignment counts when the call sits inside a function), the
   last such assignment applies, and the name is not rebound or mutated
   anywhere else (augmented assignment, assignment inside a block, a
   `global` rebinding inside a function, a `for` target, or a call to a
   mutating method such as `append`, `extend` or `update`);
3. a `dict(...)` call with only statically evaluable keyword arguments; or
4. a list or tuple concatenation (`a + b`) of statically evaluable lists or
   tuples.

Keyword arguments supplied through `**mapping` are expanded when `mapping` is
statically evaluable to a dict with string keys. For any name bound to a dict,
a top-level assignment of the form `name["key"] = value` before the setup call
updates that dict; when `value` is not statically evaluable, the name as a
whole is not statically evaluable.
When an explicit keyword and an expanded `**mapping` both supply the same
field, the later one in the call wins, as in Python.

A string value for `install_requires`, `setup_requires` or `tests_require` is
split into lines. Each non-empty line is one declaration.

When a list or tuple literal contains some elements that are not statically
evaluable, the evaluable elements are extracted and the field is
`extracted_partial`. When a dict literal for `extras_require` has some
non-evaluable values, each extra is a separate field.

## 3. Entries

### 3.1 Requirement strings

A requirement string is a PEP 508 string from PEP 621, flit, setuptools,
requirements files or `pip:` lists.

- In requirements files, a line ending with `\` is joined with the next line.
- Blank lines and lines starting with `#` are skipped. Text after ` #` is a
  comment. Per-requirement options introduced by ` --` (for example
  `--hash=...`) are removed before parsing.
- `-r <file>` / `--requirement <file>` in a requirements file is recorded as a
  directive and the referenced file, resolved relative to the including file,
  is read with the group of the including file (source kind
  `requirements_include`). A referenced file that is itself a root-level
  requirements file is not read again. If the referenced file is not in the
  archive, the field is `extracted_partial`.
- Lines starting with `-r`, `--requirement`, `-c`, `--constraint`,
  `-i`, `--index-url`, `--extra-index-url`, `-f`, `--find-links`,
  `--trusted-host`, `--pre`, `--no-binary`, `--only-binary`,
  `--prefer-binary` or `--use-feature` are **directives**, not dependencies.
- `-e <target>` / `--editable <target>` is a directive when the target is a
  local path. When it is a URL with `#egg=<name>`, it declares `<name>`.
- A bare URL with `#egg=<name>` declares `<name>` (status `parsed_url`).
- Otherwise the string is parsed as PEP 508. The normalized name is the PEP 503
  canonical name. The specifier, marker and sorted extras are recorded
  (status `parsed`). The specifier is written in canonical form: the individual
  specifiers sorted and joined by commas without spaces (`flit_core >=2,<4`
  gives `<4,>=2`).
- If PEP 508 parsing fails and the string has the conda form (an optional
  `<channel>::` prefix, a name, then optionally a comparison operator and a
  version or a space and a version, for example `numpy=1.21` or
  `conda-forge::numpy 1.21`), the name and the text after it are recorded
  (status `parsed_non_pep508`). The specifier is that text without surrounding
  space, operator included (`numpy=1.13.0=py35_0` gives `=1.13.0=py35_0`).
- Anything else is recorded with status `unparsed` and the parser error.

### 3.2 Poetry and Pipfile tables

Each key of a Poetry dependency table or a Pipfile package table is one entry.
The normalized name is the canonical form of the key. The key `python` in
`tool.poetry.dependencies` is a Python constraint, not a dependency.

| Value form | Specifier | Other information |
|---|---|---|
| string, for example `"^1.2"`, `"1.2.3"`, `">=1,<2"` | the string verbatim, empty for `"*"` | none |
| inline table | the `version` value verbatim, empty for `"*"` or when absent | all other keys are kept in `attributes`; `markers` is also copied to `marker` and `extras` to `extras` |
| array of inline tables (multiple constraints) | the `version` values joined with ` || ` | the full array is kept in `attributes.constraints` |

The status is `parsed_poetry` or `parsed_pipfile`. Poetry and Pipfile
constraint syntax is not PEP 440 and is not converted. The raw declaration is
`<key> = <value as compact JSON>`.

### 3.3 Optional Poetry entries

A `tool.poetry.dependencies` entry with `optional = true` belongs to every
extra in `tool.poetry.extras` that lists its name, with the group
`optional:<extra>`. An optional entry that no extra lists has the group
`optional:unassigned`.

### 3.4 Poetry groups

`tool.poetry.group.<name>.dependencies` maps to `development` when `<name>` is
`dev`, `docs`, `doc` or `lint`; to `test` when it is `test`, `tests` or
`testing`; and to `group:<name>` otherwise.

## 4. Field observation status

Every declaration field that is present, plus the absent core fields of every
parsed `setup.py` and `setup.cfg`, receives one row in
`declaration_source_records.csv` with one of these statuses.

| Status | Meaning |
|---|---|
| `extracted` | The field was read and every entry received a status other than `unparsed`. |
| `extracted_partial` | The field was read but at least one entry is `unparsed` or not statically evaluable, or a `-r` include is missing. |
| `confirmed_absent` | The file was parsed and the field is not present. |
| `dynamic_metadata` | The file delegates the field to build time: listed in `project.dynamic`, given as `file:` or `attr:` in `setup.cfg`, or declared under `tool.setuptools.dynamic`. |
| `unsupported_syntax` | The field is present but its value is outside the statically evaluable subset (Section 2.1), or has an unexpected type. |
| `parse_failure` | The whole file could not be parsed (Python syntax error, invalid TOML, INI or YAML), could not be read because of the size limit (detail `text_not_read_size_limit`) or because it is a link that cannot be followed (detail `link_target_unresolved`; Section 1), or (`setup.py` only) contains no setup call. |

For `setup.py`, a `**mapping` that cannot be statically evaluated produces a
row for the field `setup(**...)` with status `unsupported_syntax`, because any
field could be hidden in it.

### 4.1 Field names in `declaration_source_records.csv`

| Source | Field names |
|---|---|
| `setup.py` | `setup.install_requires`, `setup.setup_requires`, `setup.tests_require`, `setup.python_requires`; `setup.extras_require` when absent, empty or not evaluable as a whole, otherwise one field `setup.extras_require.<extra>` per extra; `setup(**...)` for an unresolvable mapping |
| `setup.cfg` | `options.install_requires`, `options.setup_requires`, `options.tests_require`, `options.python_requires`; `options.extras_require` when the section is absent, otherwise `options.extras_require.<extra>` |
| `pyproject.toml` | `dependency-groups.<name>`, `build-system.requires`, `project.dependencies`, `project.optional-dependencies.<extra>` (or `project.optional-dependencies` when dynamic or malformed), `project.requires-python`, `tool.poetry.dependencies`, `tool.poetry.dependencies.python`, `tool.poetry.dev-dependencies`, `tool.poetry.group.<name>.dependencies`, `tool.flit.metadata.requires`, `tool.flit.metadata.requires-extra.<extra>`, `tool.flit.metadata.requires-python`, `tool.setuptools.dynamic.dependencies`, `tool.setuptools.dynamic.optional-dependencies` |
| requirements file | `requirements` (one field per file; included files count towards the including file's field) |
| conda environment | `dependencies`, `dependencies.python` |
| `Pipfile` | `packages`, `dev-packages`, `requires.python_version` |
| any file | `(file)` for a file-level parse failure |

Rows for `build-system.requires` and the `project.*`, `tool.poetry.*` and
`tool.flit.*` fields exist only when the corresponding table is present. For a
parsed `setup.py` without an unresolvable mapping, all five core fields have a
row; for a parsed `setup.cfg`, all five `options.*` fields have a row.

## 5. Snapshot-level observation status

| Column | Values |
|---|---|
| `dependency_observation_status` | `complete`: every declaration field row is `extracted` or `confirmed_absent`. `no_declaration_file`: no supported declaration file exists at the root. `incomplete`: at least one field row has another status. |
| `python_constraint_observation_status` | `complete` when every field that can carry a Python constraint (Section 2) is `extracted` or `confirmed_absent` and no declaration file has `parse_failure`; `incomplete` otherwise. |
| `build_backend_observation_status` | `complete` when there is no root `pyproject.toml` or it was parsed; `incomplete` when it was not parsed. |
| `source_line_observation_status` | `complete` when every package source file was read; `incomplete` otherwise. |
| `api_observation_status` | `complete` when every package source file was read and parsed by `ast`; `incomplete` otherwise. |
| `archive_listing_status` | `complete` for every extracted snapshot (Section 1). |

Fields derived only from the member listing (`packaging_files`,
`ci_providers`, `ci_workflow_file_count`, `test_python_file_count`,
`source_python_file_count`, `archive_member_count` and the governance-file
indicators) depend only on `archive_listing_status`.

A zero count, an empty list or a `false` indicator is a measurement only when
the corresponding observation status is `complete`. Otherwise it is a lower
bound or unknown.

## 6. Pairwise comparison

Each delta row compares one **feature key** between the publication snapshot
and the endpoint snapshot of the same project.

| Family / feature group | Feature key | Compared value |
|---|---|---|
| `dependency` / `declared_dependency` | `<dependency group>|<normalized name>` | the sorted set of `{specifier, marker, extras, attributes}` over all entries in the snapshot with that key, across all declaration files |
| `api` / `syntactic_symbol` | `<module>|<symbol kind>|<qualified name>` | `{signature, decorators}` |
| `structure` / `packaging_files`, `build_backends`, `python_constraints`, `ci_providers` | the list element | presence |
| `structure` / `scalar` | the field name | the value |

Only entries with a non-empty normalized name and a status of `parsed`,
`parsed_poetry`, `parsed_pipfile`, `parsed_non_pep508` or `parsed_url` form
dependency keys.

Consequences of these rules:

- A dependency that moves from `requirements.txt` to `setup.py` in the same
  group, with the same specifier, is `unchanged`; the evidence columns show the
  move.
- A dependency declared in two files with different specifiers has a value set
  with two elements. If either specifier changes, the key is `changed`.
- A dependency that moves between groups (for example from `runtime` to
  `optional:test`) is one `removed` and one `added` row.

`delta_type` is `added`, `removed`, `changed` or `unchanged`. `comparison_status`
is `comparable` when the observation status that governs the feature is
complete in both snapshots, and `incomplete_observation` otherwise. The
governing statuses are:

| Feature | Governing status |
|---|---|
| `declared_dependency` keys, `declared_dependency_record_count` | `dependency_observation_status` (`no_declaration_file` counts as complete) |
| `syntactic_symbol` keys, `syntactic_api_symbol_count` | `api_observation_status` |
| `python_constraints` | `python_constraint_observation_status` |
| `build_backends` | `build_backend_observation_status` |
| `source_python_line_count` | `source_line_observation_status` |
| listing-derived fields | `archive_listing_status` |

An `added`, `removed` or `changed` row under `incomplete_observation` may
reflect a change in what could be observed rather than a change in the
software.

## 7. Package source files and interface symbols

### 7.1 Package source files

A member listing path ending in `.py` is a package source file when none of
its path components is, ignoring case, one of `.git`, `.github`, `.tox`,
`.venv`, `venv`, `build`, `dist`, `docs`, `doc`, `examples`, `example`,
`benchmarks`, `benchmark`, `scripts`, `tools`, `vendor`, `vendored`, `tests`,
`test`, `testing` or `site-packages`, and one of the following holds:

1. it sits at the archive root and is not `setup.py`, `conftest.py`, `conf.py`,
   `manage.py` or `noxfile.py`; or
2. it lies below a **package root**. A package root is `src/<name>` or
   `python/<name>` for every `.py` file at depth three or more under `src/` or
   `python/`, and the top directory of every `__init__.py` at depth two or
   more elsewhere.

The module name is the path without a leading `src/` or `python/`, without the
`.py` suffix and without a final `__init__`, joined with dots.

### 7.2 Interface symbols

Only statements directly in the module body are read; definitions inside
`if`, `try`, functions or other blocks are not.

| Symbol kind | Recorded for | Qualified name |
|---|---|---|
| `function`, `async_function` | a top-level `def` / `async def` whose name does not start with `_` | the name |
| `class` | a top-level `class` whose name does not start with `_` | the name |
| `constructor` | `__init__` defined directly in the body of a recorded class | `<class>.__init__` |
| `method` | a `def` or `async def` directly in the body of a recorded class whose name does not start with `_` | `<class>.<name>` |

A literal `__all__` does not add or remove symbols; it only sets
`export_evidence` to `literal_all` for listed names (otherwise
`lexical_nonprivate`, or `class_member` for constructors and methods). The
`signature` is the argument list as written (Python `ast.unparse` of the
arguments), so its parameter names include `self` and `cls`. When the same key
is defined more than once in a module (for example a property getter and
setter), one row is kept, at the first definition, with the distinct
signatures joined by ` || ` in sorted order and the decorators merged.
