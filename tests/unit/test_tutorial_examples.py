"""The code displayed in a checked box must be the code executed by CI."""
import importlib.util
from pathlib import Path

import pytest

from fastdyn.tutorial_examples import decorate, examples, suite

ROOT = Path(__file__).resolve().parents[2]


def book(tmp_path, code="printf 'ready\\n'", language="bash", options=""):
    source = tmp_path / "docs/book/chapter.md"
    source.parent.mkdir(parents=True)
    source.write_text(f"```bash\nexit 99 # unmarked example, never executed\n```\n\n"
                      f"<!-- fastdyn-check: first -->\n```{language}\n{code}\n```\n")
    config = tmp_path / "tests/integration/tutorial.toml"
    config.parent.mkdir(parents=True)
    config.write_text('[execution]\noutput = "out/check"\ntimeout_s = 2\n'
                      '[[steps]]\nid = "first"\n' + options)
    return source


@pytest.fixture
def runner(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "utils"))
    spec = importlib.util.spec_from_file_location("verify_tutorial", ROOT / "utils/verify_tutorial.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runs_the_displayed_block_and_observes_source_edits(tmp_path, runner):
    source = book(tmp_path, options='contains = ["ready"]\n')
    logs = tmp_path / "logs"
    logs.mkdir()
    settings, found = suite(tmp_path)
    runner.run_step(tmp_path, settings["steps"][0], found["first"], logs, 2)
    assert (logs / "first.log").read_text() == "ready\n"
    source.write_text(source.read_text().replace("ready", "changed"))
    with pytest.raises(AssertionError, match="Missing expected output"):
        runner.run_step(tmp_path, settings["steps"][0], examples(tmp_path)["first"], logs, 2)


@pytest.mark.parametrize("command", ["false", "false | true", "sleep 10"])
def test_failed_or_timed_out_commands_fail_the_check(tmp_path, runner, command):
    book(tmp_path, command)
    logs = tmp_path / "logs"
    logs.mkdir()
    settings, found = suite(tmp_path)
    with pytest.raises((RuntimeError, TimeoutError)):
        runner.run_step(tmp_path, settings["steps"][0], found["first"], logs, .1)


def test_zero_exit_without_the_documented_artifact_fails(tmp_path, runner):
    book(tmp_path, options='artifacts = ["out/mission.tlog"]\n')
    logs = tmp_path / "logs"
    logs.mkdir()
    settings, found = suite(tmp_path)
    with pytest.raises(AssertionError, match="Missing or empty artifact"):
        runner.run_step(tmp_path, settings["steps"][0], found["first"], logs, 2)


def test_file_example_uses_the_included_source(tmp_path, runner):
    book(tmp_path, "{{#include ../sample.py}}", "python", 'write = "out/example.py"\n')
    (tmp_path / "docs/sample.py").write_text("print('from the book')\n")
    logs = tmp_path / "logs"
    logs.mkdir()
    settings, found = suite(tmp_path)
    runner.run_step(tmp_path, settings["steps"][0], found["first"], logs, 2)
    assert (tmp_path / "out/example.py").read_text() == "print('from the book')\n"


@pytest.mark.parametrize("replacement", [
    "<!-- fastdyn-check: second -->",
    "<!-- fastdyn-check: first -->\ntext between the marker and code",
    "<!-- fastdyn-check: first -->\n```bash\ntrue\n```\n<!-- fastdyn-check: first -->",
])
def test_markers_cannot_silently_lose_coverage(tmp_path, replacement):
    source = book(tmp_path)
    source.write_text(source.read_text().replace("<!-- fastdyn-check: first -->", replacement))
    with pytest.raises(ValueError):
        suite(tmp_path)


def test_boxes_preserve_copyable_code_and_link_to_the_accepting_repository(tmp_path):
    source = book(tmp_path)
    chapter = {"content": source.read_text(), "sub_items": []}
    context = {"root": str(tmp_path / "docs"), "config": {"output": {"html": {
        "git-repository-url": "https://github.com/PSecLab/FastDyn"}}}}
    decorate({"items": [{"Chapter": chapter}]}, context)
    assert '<div class="fd-verified" data-example="first">' in chapter["content"]
    assert "https://github.com/PSecLab/FastDyn/actions/workflows/dev-container.yml" in chapter["content"]
    assert "```bash\nprintf 'ready\\n'\n```" in chapter["content"]
    assert chapter["content"].count("fd-verified-label") == 1


def test_every_marked_book_example_has_an_execution_step():
    settings, found = suite(ROOT)
    assert len(settings["steps"]) == len(found) > 20
