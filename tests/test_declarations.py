"""Regression tests for dependency-declaration extraction.

Expected outcomes are written from docs/extraction_specification_v0.3.md
(cited per test), not from extractor output.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from declarations import extract_declarations  # noqa: E402


def run(files: dict[str, str], names: list[str] | None = None):
    snapshot = {"snapshot_id": "s", "project_id": "p", "snapshot_role": "publication"}
    return extract_declarations(snapshot, names if names is not None else list(files), files)


def deps(result) -> set[tuple[str, str, str]]:
    return {(row["dependency_group"], row["normalized_name"], row["specifier"])
            for row in result.dependency_rows if row["normalized_name"]}


def fields(result) -> dict[str, str]:
    return {f"{row['source_file']}|{row['field']}": row["observation_status"] for row in result.field_rows}


class SetupPyTests(unittest.TestCase):
    def test_unpacked_module_level_dict_is_expanded(self) -> None:
        # Spec 2.1: **mapping is expanded when the mapping is statically evaluable.
        result = run({"setup.py": (
            "from setuptools import setup\n"
            "KW = dict(name='x', install_requires=['numpy>=1.20'], python_requires='>=3.8')\n"
            "setup(**KW)\n"
        )})
        self.assertEqual(deps(result), {("runtime", "numpy", ">=1.20")})
        self.assertEqual(fields(result)["setup.py|setup.install_requires"], "extracted")
        self.assertEqual(result.python_constraints, ["setup.py:>=3.8"])
        self.assertEqual(result.dependency_observation_status()[0], "complete")

    def test_subscript_update_of_kwargs_dict_is_honoured(self) -> None:
        # Spec 2.1: top-level mapping["key"] = value updates the dict.
        result = run({"setup.py": (
            "from setuptools import setup\n"
            "kw = {'name': 'x'}\n"
            "kw['install_requires'] = ['scipy']\n"
            "setup(**kw)\n"
        )})
        self.assertEqual(deps(result), {("runtime", "scipy", "")})

    def test_unresolvable_unpacking_is_flagged_not_silent(self) -> None:
        # Spec 4: unresolvable **mapping -> setup(**...) unsupported_syntax; absent fields are not confirmed.
        result = run({"setup.py": "from setuptools import setup\nsetup(**get_config())\n"})
        status = fields(result)
        self.assertEqual(status["setup.py|setup(**...)"], "unsupported_syntax")
        self.assertNotIn("setup.py|setup.install_requires", status)
        self.assertEqual(result.dependency_observation_status()[0], "incomplete")
        self.assertEqual(result.python_constraint_observation_status(), "incomplete")

    def test_absent_fields_of_parsed_setup_py_are_confirmed_absent(self) -> None:
        # Spec 4: absent core fields of a parsed setup.py are confirmed_absent; a zero count is a measurement.
        result = run({"setup.py": "from setuptools import setup\nsetup(name='x')\n"})
        status = fields(result)
        for field in ("install_requires", "extras_require", "setup_requires", "tests_require", "python_requires"):
            self.assertEqual(status[f"setup.py|setup.{field}"], "confirmed_absent")
        self.assertEqual(result.dependency_rows, [])
        self.assertEqual(result.dependency_observation_status()[0], "complete")
        self.assertEqual(result.python_constraint_observation_status(), "complete")

    def test_nonliteral_python_requires_is_not_silently_dropped(self) -> None:
        result = run({"setup.py": "from setuptools import setup\nsetup(python_requires=get_py())\n"})
        self.assertEqual(fields(result)["setup.py|setup.python_requires"], "unsupported_syntax")
        self.assertEqual(result.python_constraint_observation_status(), "incomplete")

    def test_mutated_list_is_unsupported(self) -> None:
        # Spec 2.1: a name mutated by append is not statically evaluable.
        result = run({"setup.py": (
            "from setuptools import setup\n"
            "reqs = ['numpy']\n"
            "if True:\n    reqs.append('enum34')\n"
            "setup(install_requires=reqs)\n"
        )})
        self.assertEqual(fields(result)["setup.py|setup.install_requires"], "unsupported_syntax")
        self.assertEqual(deps(result), set())

    def test_list_with_some_nonliteral_elements_is_partial(self) -> None:
        result = run({"setup.py": "from setuptools import setup\nsetup(install_requires=['numpy', dep()])\n"})
        self.assertEqual(fields(result)["setup.py|setup.install_requires"], "extracted_partial")
        self.assertEqual(deps(result), {("runtime", "numpy", "")})

    def test_rebinding_after_setup_call_does_not_apply(self) -> None:
        result = run({"setup.py": (
            "from setuptools import setup\n"
            "REQS = ['numpy']\n"
            "setup(install_requires=REQS)\n"
            "REQS = ['pandas']\n"
        )})
        self.assertEqual(deps(result), {("runtime", "numpy", "")})

    def test_setup_and_tests_requires_and_string_value(self) -> None:
        result = run({"setup.py": (
            "from setuptools import setup\n"
            "setup(install_requires='numpy\\nscipy>=1', setup_requires=['cython'], tests_require=['pytest'])\n"
        )})
        self.assertEqual(deps(result), {
            ("runtime", "numpy", ""), ("runtime", "scipy", ">=1"),
            ("build", "cython", ""), ("test", "pytest", ""),
        })

    def test_syntax_error_is_parse_failure(self) -> None:
        result = run({"setup.py": "setup(install_requires=['numpy'\n"})
        self.assertEqual(fields(result)["setup.py|(file)"], "parse_failure")
        self.assertEqual(result.dependency_observation_status()[0], "incomplete")

    def test_setup_py_is_never_executed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            marker = Path(temp_dir) / "executed.txt"
            run({"setup.py": (
                "from pathlib import Path\n"
                f"Path({marker.as_posix()!r}).write_text('x')\n"
                "from setuptools import setup\nsetup(install_requires=['numpy'])\n"
            )})
            self.assertFalse(marker.exists())


class PyprojectTests(unittest.TestCase):
    def test_dynamic_dependencies_are_flagged(self) -> None:
        # Spec 4: a field listed in project.dynamic is dynamic_metadata, so the snapshot is incomplete.
        result = run({"pyproject.toml": (
            "[project]\nname='x'\ndynamic=['dependencies','version']\nrequires-python='>=3.9'\n"
            "[tool.setuptools.dynamic]\ndependencies={file=['requirements.txt']}\n"
        )})
        status = fields(result)
        self.assertEqual(status["pyproject.toml|project.dependencies"], "dynamic_metadata")
        self.assertEqual(status["pyproject.toml|tool.setuptools.dynamic.dependencies"], "dynamic_metadata")
        self.assertEqual(result.dependency_observation_status()[0], "incomplete")
        self.assertEqual(result.python_constraints, ["pyproject.toml:>=3.9"])

    def test_poetry_inline_table_keeps_markers_extras_and_attributes(self) -> None:
        # Spec 3.2: all keys other than version are kept; markers -> marker; extras -> extras.
        result = run({"pyproject.toml": (
            "[tool.poetry.dependencies]\n"
            "python='^3.8'\n"
            "requests={version='^2.25', extras=['socks'], markers=\"sys_platform == 'linux'\"}\n"
            "mylib={git='https://github.com/a/mylib.git', branch='main'}\n"
        )})
        rows = {row["normalized_name"]: row for row in result.dependency_rows}
        self.assertEqual(rows["requests"]["specifier"], "^2.25")
        self.assertEqual(rows["requests"]["extras"], "socks")
        self.assertEqual(rows["requests"]["marker"], "sys_platform == 'linux'")
        self.assertEqual(json.loads(rows["mylib"]["attributes"]), {"git": "https://github.com/a/mylib.git", "branch": "main"})
        self.assertEqual(rows["mylib"]["specifier"], "")
        self.assertEqual(result.python_constraints, ["pyproject.toml:^3.8"])

    def test_poetry_exact_version_keeps_the_package_name(self) -> None:
        # Spec 3.2: the name comes from the key; "1.2.3" is the specifier verbatim.
        result = run({"pyproject.toml": "[tool.poetry.dependencies]\nnumpy='1.2.3'\npandas='*'\nscipy='1.5.*'\n"})
        self.assertEqual(deps(result), {("runtime", "numpy", "1.2.3"), ("runtime", "pandas", ""), ("runtime", "scipy", "1.5.*")})
        self.assertTrue(all(row["parse_status"] == "parsed_poetry" for row in result.dependency_rows))

    def test_poetry_optional_entries_follow_extras(self) -> None:
        # Spec 3.3.
        result = run({"pyproject.toml": (
            "[tool.poetry.dependencies]\n"
            "numpy='^1.20'\n"
            "matplotlib={version='^3', optional=true}\n"
            "orphan={version='^1', optional=true}\n"
            "[tool.poetry.extras]\nplot=['matplotlib']\nall=['matplotlib']\n"
        )})
        self.assertEqual(deps(result), {
            ("runtime", "numpy", "^1.20"),
            ("optional:all", "matplotlib", "^3"), ("optional:plot", "matplotlib", "^3"),
            ("optional:unassigned", "orphan", "^1"),
        })

    def test_poetry_dev_dependencies_and_groups(self) -> None:
        # Spec 2 and 3.4.
        result = run({"pyproject.toml": (
            "[tool.poetry.dev-dependencies]\npytest='^6'\n"
            "[tool.poetry.group.test.dependencies]\ncoverage='^5'\n"
            "[tool.poetry.group.docs.dependencies]\nsphinx='^4'\n"
            "[tool.poetry.group.bench.dependencies]\nasv='*'\n"
        )})
        self.assertEqual(deps(result), {
            ("development", "pytest", "^6"), ("test", "coverage", "^5"),
            ("development", "sphinx", "^4"), ("group:bench", "asv", ""),
        })

    def test_poetry_multiple_constraints(self) -> None:
        result = run({"pyproject.toml": (
            "[tool.poetry.dependencies]\n"
            "numpy=[{version='<1.20', python='<3.7'}, {version='^1.20', python='>=3.7'}]\n"
        )})
        row = result.dependency_rows[0]
        self.assertEqual(row["specifier"], "<1.20 || ^1.20")
        self.assertEqual(len(json.loads(row["attributes"])["constraints"]), 2)

    def test_flit_legacy_metadata(self) -> None:
        result = run({"pyproject.toml": (
            "[tool.flit.metadata]\nmodule='x'\nrequires=['numpy>=1.16']\nrequires-python='>=3.6'\n"
            "[tool.flit.metadata.requires-extra]\ntest=['pytest']\n"
        )})
        self.assertEqual(deps(result), {("runtime", "numpy", ">=1.16"), ("optional:test", "pytest", "")})
        self.assertEqual(result.python_constraints, ["pyproject.toml:>=3.6"])

    def test_pep735_dependency_groups(self) -> None:
        # Spec Section 2: dependency-groups.<name> maps like Poetry groups; include-group is a directive.
        result = run({"pyproject.toml": (
            "[dependency-groups]\n"
            "test = ['pytest>=8']\n"
            "docs = ['sphinx']\n"
            "bench = ['asv']\n"
            "dev = [{include-group = 'test'}, 'ruff']\n"
        )})
        self.assertEqual(deps(result), {
            ("test", "pytest", ">=8"), ("development", "sphinx", ""), ("group:bench", "asv", ""), ("development", "ruff", ""),
        })
        self.assertEqual(sum(row["parse_status"] == "directive" for row in result.dependency_rows), 1)
        self.assertEqual(fields(result)["pyproject.toml|dependency-groups.dev"], "extracted")
        self.assertEqual(result.dependency_observation_status()[0], "complete")

    def test_build_system_and_invalid_toml(self) -> None:
        result = run({"pyproject.toml": "[build-system]\nrequires=['setuptools>=42','wheel']\nbuild-backend='setuptools.build_meta'\n"})
        self.assertEqual(deps(result), {("build", "setuptools", ">=42"), ("build", "wheel", "")})
        self.assertEqual(result.build_backends, ["setuptools.build_meta"])
        broken = run({"pyproject.toml": "[project\nname='x'\n"})
        self.assertEqual(fields(broken)["pyproject.toml|(file)"], "parse_failure")


class SetupCfgTests(unittest.TestCase):
    def test_file_reference_is_dynamic(self) -> None:
        result = run({"setup.cfg": "[options]\ninstall_requires = file: requirements.txt\npython_requires = >=3.7\n"})
        status = fields(result)
        self.assertEqual(status["setup.cfg|options.install_requires"], "dynamic_metadata")
        self.assertEqual(status["setup.cfg|options.extras_require"], "confirmed_absent")
        self.assertEqual(result.python_constraints, ["setup.cfg:>=3.7"])

    def test_extras_and_tests_require(self) -> None:
        result = run({"setup.cfg": (
            "[options]\ninstall_requires =\n    numpy\n    scipy>=1.4\ntests_require = pytest\n"
            "[options.extras_require]\nplot = matplotlib\n"
        )})
        self.assertEqual(deps(result), {
            ("runtime", "numpy", ""), ("runtime", "scipy", ">=1.4"),
            ("test", "pytest", ""), ("optional:plot", "matplotlib", ""),
        })
        self.assertEqual(result.dependency_observation_status()[0], "complete")


class RequirementsTests(unittest.TestCase):
    def test_editable_vcs_hash_and_continuation(self) -> None:
        # Spec 3.1.
        result = run({"requirements.txt": (
            "-e .\n"
            "-e git+https://github.com/a/b.git#egg=b-lib\n"
            "numpy==1.21 \\\n    --hash=sha256:abc\n"
            "--index-url https://pypi.org/simple\n"
            "https://example.org/pkg.zip#egg=pkg\n"
        )})
        self.assertEqual(deps(result), {("runtime", "b-lib", ""), ("runtime", "numpy", "==1.21"), ("runtime", "pkg", "")})
        statuses = [row["parse_status"] for row in result.dependency_rows]
        self.assertEqual(statuses.count("directive"), 2)
        self.assertEqual(fields(result)["requirements.txt|requirements"], "extracted")

    def test_include_of_subdirectory_file_is_followed(self) -> None:
        files = {"requirements.txt": "-r requirements/base.txt\n", "requirements/base.txt": "numpy\n"}
        result = run(files, names=["requirements.txt", "requirements/base.txt"])
        self.assertEqual(deps(result), {("runtime", "numpy", "")})
        self.assertEqual(fields(result)["requirements.txt|requirements"], "extracted")

    def test_missing_include_is_partial(self) -> None:
        result = run({"requirements.txt": "-r requirements/missing.txt\nnumpy\n"})
        self.assertEqual(fields(result)["requirements.txt|requirements"], "extracted_partial")

    def test_unparsed_line_makes_field_partial(self) -> None:
        result = run({"requirements-dev.txt": "numpy\n!!not a requirement\n"})
        self.assertEqual(fields(result)["requirements-dev.txt|requirements"], "extracted_partial")
        self.assertEqual(deps(result), {("development", "numpy", "")})


class OtherSourcesTests(unittest.TestCase):
    def test_environment_yml(self) -> None:
        result = run({"environment.yml": (
            "dependencies:\n  - python>=3.8\n  - conda-forge::numpy 1.21\n  - pip\n  - pip:\n    - requests>=2\n"
        )})
        self.assertEqual(deps(result), {("runtime", "numpy", "1.21"), ("runtime", "pip", ""), ("runtime", "requests", ">=2")})
        self.assertEqual(result.python_constraints, ["environment.yml:python>=3.8"])

    def test_pipfile(self) -> None:
        result = run({"Pipfile": (
            "[packages]\nnumpy='*'\nrequests={version='>=2', extras=['socks']}\n"
            "[dev-packages]\npytest='>=6'\n[requires]\npython_version='3.8'\n"
        )})
        self.assertEqual(deps(result), {("runtime", "numpy", ""), ("runtime", "requests", ">=2"), ("development", "pytest", ">=6")})
        self.assertEqual(result.python_constraints, ["Pipfile:3.8"])

    def test_no_declaration_file(self) -> None:
        result = run({"README.md": "x"}, names=["README.md"])
        self.assertEqual(result.dependency_observation_status()[0], "no_declaration_file")

    def test_unread_declaration_file_is_parse_failure(self) -> None:
        result = run({}, names=["setup.py"])
        self.assertEqual(fields(result)["setup.py|(file)"], "parse_failure")

    def test_subdirectory_files_are_not_sources(self) -> None:
        result = run({"pkg/setup.py": "from setuptools import setup\nsetup(install_requires=['x'])\n"})
        self.assertEqual(result.dependency_rows, [])
        self.assertEqual(result.dependency_observation_status()[0], "no_declaration_file")


if __name__ == "__main__":
    unittest.main()
