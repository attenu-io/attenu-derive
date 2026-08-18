"""T22: the shared repo-tool surface — one tested implementation of what every harness gives its agents."""
import json
from pathlib import Path

import pytest

from attenu_derive.sample.repo_tools import MAX_LINES, MAX_LIST, RepoTools


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"; (r / "src").mkdir(parents=True); (r / ".git").mkdir(); (r / "node_modules").mkdir()
    (r / "README.md").write_text("hello\nworld\n")
    (r / "src" / "app.py").write_text("def main():\n    TODO = 1\n    return TODO\n")
    (r / ".git" / "config").write_text("secret")
    (r / "node_modules" / "x.js").write_text("junk")
    return r


def test_list_read_search_and_skip_dirs(tmp_path, repo):
    rt = RepoTools(repo, tmp_path / "art")
    files = rt.list_files("**/*.py")["files"]
    assert files == ["src/app.py"]
    assert "README.md" in rt.list_files("*.md")["files"]
    assert all(".git" not in f and "node_modules" not in f for f in rt.list_files()["files"])   # skip dirs
    rd = rt.read_file("README.md"); assert rd["lines"] == ["hello", "world"] and rd["total_lines"] == 2
    m = rt.search_files("TODO", "**/*.py")["matches"]
    assert m and m[0]["path"] == "src/app.py" and m[0]["line"] == 2


def test_path_escape_and_missing_and_bad_regex(tmp_path, repo):
    rt = RepoTools(repo, tmp_path / "art")
    assert "escapes" in rt.read_file("../../etc/passwd")["error"]
    assert rt.read_file("nope.txt")["error"] == "not a file"
    assert "bad regex" in rt.search_files("(", "**/*")["error"]


def test_write_is_sandboxed_to_artifacts_never_the_repo(tmp_path, repo):
    art = tmp_path / "art"; rt = RepoTools(repo, art)
    out = rt.write_file("REPORT.md", "# report")
    assert out["written"] == "REPORT.md" and (art / "REPORT.md").read_text() == "# report"
    rt.write_file("../escape.md", "x")                                    # basename only -> stays in artifacts
    assert (art / "escape.md").exists() and not (repo / "escape.md").exists()


def test_caps(tmp_path):
    r = tmp_path / "big"; r.mkdir()
    for i in range(MAX_LIST + 20): (r / f"f{i}.txt").write_text("x")
    rt = RepoTools(r, tmp_path / "art")
    assert rt.list_files()["truncated"] and len(rt.list_files()["files"]) == MAX_LIST
    (r / "long.txt").write_text("\n".join(str(i) for i in range(MAX_LINES + 50)))
    assert len(rt.read_file("long.txt")["lines"]) == MAX_LINES


def test_json_variants(tmp_path, repo):
    rt = RepoTools(repo, tmp_path / "art")
    assert json.loads(rt.list_files_json("*.md"))["files"] == ["README.md"]
    assert json.loads(rt.write_file_json("R.md", "x"))["written"] == "R.md"


def test_repo_tool_harnesses_use_the_shared_surface():
    """Guard against re-duplication: harnesses that give agents a repo surface build it from RepoTools, not their own copies.
    (run_adk_app runs the sampled APP's own tools, so it has no repo tools of its own — not in scope here.)"""
    import inspect
    from attenu_derive.sample import run_adk, run_crewai
    for mod in (run_adk, run_crewai):
        src = inspect.getsource(mod)
        assert "RepoTools" in src, mod.__name__
        assert "_SKIP_DIRS = {" not in src and "def _skip(" not in src, f"{mod.__name__} still defines its own skip-list"
