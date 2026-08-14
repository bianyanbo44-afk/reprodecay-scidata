import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from resolve_endpoint_snapshots import (  # noqa: E402
    canonical_repository_coordinates,
    download_archive,
    resolve_endpoint_metadata,
    resolve_git_ref,
    resolve_via_github_web,
    request_json,
    retryable_resolution_error,
    summarize_resolved_rows,
    stable_resolution_error,
)


class EndpointResolutionTests(unittest.TestCase):
    @patch("resolve_endpoint_snapshots.resolve_via_github_web")
    @patch("resolve_endpoint_snapshots.resolve_via_github_api")
    def test_rate_limit_uses_official_web_git_fallback(self, api, web):
        api.side_effect = urllib.error.HTTPError(
            "https://api.github.com/repos/o/r",
            403,
            "rate limited",
            {"x-ratelimit-remaining": "0"},
            None,
        )
        web.return_value = {"resolution_method": "github_web_git_refs"}

        result = resolve_endpoint_metadata({"repository_owner": "o", "repository_name": "r"}, None)

        self.assertEqual(result["resolution_method"], "github_web_git_refs")
        web.assert_called_once()

    @patch("resolve_endpoint_snapshots.git_ls_remote")
    def test_annotated_release_tag_uses_peeled_commit(self, ls_remote):
        ls_remote.return_value = (
            "a" * 40 + "\trefs/tags/v1.0\n" + "b" * 40 + "\trefs/tags/v1.0^{}\n"
        )
        self.assertEqual(resolve_git_ref("https://github.com/o/r.git", "v1.0", release=True), "b" * 40)

    @patch("resolve_endpoint_snapshots.resolve_git_ref", return_value="c" * 40)
    @patch("resolve_endpoint_snapshots.run_curl_effective_url")
    def test_web_fallback_preserves_canonical_migration_and_latest_release(self, effective, _resolve):
        effective.side_effect = [
            ("https://github.com/new-owner/new-repo", 200),
            ("https://github.com/new-owner/new-repo/releases/tag/v2.0", 200),
        ]
        result = resolve_via_github_web(
            {
                "repository_url": "https://github.com/old-owner/old-repo",
                "github_default_branch": "main",
            }
        )
        self.assertEqual(result["resolved_full_name"], "new-owner/new-repo")
        self.assertEqual(result["endpoint_ref_type"], "release")
        self.assertEqual(result["endpoint_ref_name"], "v2.0")
        self.assertEqual(result["endpoint_commit_sha"], "c" * 40)

    def test_timeout_error_does_not_disclose_command(self):
        error = subprocess.TimeoutExpired(
            ["curl", "https://example.invalid/?X-Amz-Signature=secret"],
            timeout=1,
        )
        self.assertEqual(stable_resolution_error(error), "download_timeout")

    def test_unknown_error_only_records_type(self):
        self.assertEqual(
            stable_resolution_error(ValueError(str(Path("C:/private/path")))),
            "ValueError_unspecified",
        )

    def test_manifest_endpoint_counts_ignore_unresolved_partial_metadata(self):
        rows = [
            {
                "resolution_status": "resolved",
                "endpoint_ref_type": "release",
                "resolution_method": "github_rest_api",
            },
            {
                "resolution_status": "resolved",
                "endpoint_ref_type": "default_branch",
                "resolution_method": "github_web_git_refs",
            },
            {
                "resolution_status": "unresolved",
                "endpoint_ref_type": "release",
                "resolution_method": "github_rest_api",
            },
            {
                "resolution_status": "unresolved",
                "endpoint_ref_type": "default_branch",
                "resolution_method": "github_web_git_refs",
            },
        ]

        self.assertEqual(
            summarize_resolved_rows(rows),
            {
                "projects_resolved": 2,
                "stable_release_endpoints": 1,
                "default_branch_endpoints": 1,
                "resolution_methods": {
                    "github_rest_api": 1,
                    "github_web_git_refs": 1,
                },
            },
        )

    @patch("resolve_endpoint_snapshots.shutil.which", return_value="curl")
    @patch("resolve_endpoint_snapshots.subprocess.run")
    def test_request_json_follows_redirect_and_parses_final_response(self, run, _which):
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=(
                b"HTTP/1.1 301 Moved Permanently\r\n"
                b"location: https://api.github.com/repositories/123\r\n"
                b"x-ratelimit-remaining: 50\r\n\r\n"
                b"HTTP/2 200\r\n"
                b"content-type: application/json\r\n"
                b"x-ratelimit-remaining: 49\r\n\r\n"
                b'{"full_name":"new-owner/new-repo","html_url":"https://github.com/new-owner/new-repo"}'
            ),
        )

        payload, headers = request_json("https://api.github.com/repos/old-owner/old-repo", None, attempts=1)

        self.assertEqual(payload["full_name"], "new-owner/new-repo")
        self.assertEqual(headers["x-ratelimit-remaining"], "49")
        self.assertIn("--location", run.call_args.args[0])

    def test_canonical_repository_coordinates_use_final_metadata(self):
        full_name, html_url, slug, api_url = canonical_repository_coordinates(
            {
                "full_name": "new owner/new repo",
                "html_url": "https://github.com/new owner/new repo",
            }
        )
        self.assertEqual(full_name, "new owner/new repo")
        self.assertEqual(html_url, "https://github.com/new owner/new repo")
        self.assertEqual(slug, "new%20owner/new%20repo")
        self.assertEqual(api_url, "https://api.github.com/repos/new%20owner/new%20repo")

    @patch("resolve_endpoint_snapshots.shutil.which", return_value="curl")
    @patch("resolve_endpoint_snapshots.subprocess.run")
    def test_curl_33_retries_once_without_resume_and_only_removes_target_part(self, run, _which):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            destination = directory / "archive.tar.gz"
            partial = directory / "archive.tar.gz.part"
            neighbor = directory / "neighbor.tar.gz.part"
            partial.write_bytes(b"stale partial")
            neighbor.write_bytes(b"keep me")

            def curl_result(command, **_kwargs):
                if run.call_count == 1:
                    self.assertTrue(partial.exists())
                    return subprocess.CompletedProcess(command, 33)
                self.assertFalse(partial.exists())
                partial.write_bytes(b"complete archive")
                return subprocess.CompletedProcess(command, 0)

            run.side_effect = curl_result
            download_archive("https://codeload.github.com/o/r/tar.gz/abc", destination)

            self.assertEqual(run.call_count, 2)
            first_command = run.call_args_list[0].args[0]
            second_command = run.call_args_list[1].args[0]
            self.assertIn("--continue-at", first_command)
            self.assertNotIn("--continue-at", second_command)
            self.assertEqual(destination.read_bytes(), b"complete archive")
            self.assertEqual(neighbor.read_bytes(), b"keep me")

    def test_runtime_error_unspecified_is_transient(self):
        self.assertTrue(retryable_resolution_error("RuntimeError_unspecified"))
        self.assertTrue(retryable_resolution_error("github_web_curl_failed:28"))
        self.assertTrue(retryable_resolution_error("github_web_curl_failed:52"))
        self.assertTrue(retryable_resolution_error("github_web_curl_failed:56"))
        self.assertTrue(retryable_resolution_error("git_ls_remote_failed"))
        self.assertFalse(retryable_resolution_error("ValueError_unspecified"))

    def test_endpoint_local_cache_is_not_a_public_output_field(self):
        source = Path(__file__).resolve().parents[1] / "scripts" / "resolve_endpoint_snapshots.py"
        text = source.read_text(encoding="utf-8")
        self.assertNotIn('"endpoint_local_cache": local.as_posix()', text)


if __name__ == "__main__":
    unittest.main()
