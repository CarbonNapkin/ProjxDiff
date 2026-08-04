"""Integration test for the CLI entry point. Covers the wiring in __main__.main
and is the regression guard for the Windows file:// URL fix (use as_uri())."""

import sys
import zipfile
from pathlib import Path

import pytest

import dw_compare.__main__ as cli


def test_main_writes_report_and_opens_valid_file_uri(tmp_path, monkeypatch):
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    out = tmp_path / "report.html"

    opened = {}
    # No network in tests, and capture the URL handed to the browser.
    monkeypatch.setattr(cli, "check_for_update", lambda *a, **k: None)
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: opened.setdefault("url", url))
    monkeypatch.setattr(sys, "argv",
                        ["dw_compare", str(old_dir), str(new_dir), "-o", str(out)])

    cli.main()

    assert out.exists()
    assert "<!DOCTYPE html>" in out.read_text(encoding="utf-8")

    url = opened["url"]
    # REGRESSION: a proper file URI, not f"file://{path}" (which breaks on
    # Windows drive letters / backslashes and leaves spaces unescaped).
    assert url == out.resolve().as_uri()
    assert url.startswith("file://")
    assert "\\" not in url


def test_main_no_open_skips_browser(tmp_path, monkeypatch):
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    out = tmp_path / "report.html"

    opened = {}
    monkeypatch.setattr(cli, "check_for_update", lambda *a, **k: None)
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: opened.setdefault("url", url))
    monkeypatch.setattr(sys, "argv",
                        ["dw_compare", str(old_dir), str(new_dir), "-o", str(out), "--no-open"])

    cli.main()

    assert out.exists()
    assert "url" not in opened  # --no-open must not launch a browser


def test_main_format_json_writes_json_and_never_opens_browser(tmp_path, monkeypatch):
    import json

    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    out = tmp_path / "diff.json"

    opened = {}
    monkeypatch.setattr(cli, "check_for_update", lambda *a, **k: None)
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: opened.setdefault("url", url))
    monkeypatch.setattr(sys, "argv",
                        ["dw_compare", str(old_dir), str(new_dir),
                         "-o", str(out), "--format", "json"])

    cli.main()

    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["schema"] == 1
    assert doc["old_project"] == "old" and doc["new_project"] == "new"
    # JSON-only runs are for scripting; no browser even without --no-open.
    assert "url" not in opened


def test_main_format_json_default_output_name(tmp_path, monkeypatch):
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "check_for_update", lambda *a, **k: None)
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: None)
    monkeypatch.setattr(sys, "argv",
                        ["dw_compare", str(old_dir), str(new_dir), "--format", "json"])

    cli.main()

    assert (tmp_path / "dw_comparison.json").exists()
    assert not (tmp_path / "dw_comparison.html").exists()


def test_main_format_both_writes_both_and_opens_html(tmp_path, monkeypatch):
    import json

    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    out = tmp_path / "report.html"

    opened = {}
    monkeypatch.setattr(cli, "check_for_update", lambda *a, **k: None)
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: opened.setdefault("url", url))
    monkeypatch.setattr(sys, "argv",
                        ["dw_compare", str(old_dir), str(new_dir),
                         "-o", str(out), "--format", "both"])

    cli.main()

    assert "<!DOCTYPE html>" in out.read_text(encoding="utf-8")
    doc = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert doc["schema"] == 1
    assert opened["url"] == out.resolve().as_uri()  # the HTML report still opens


def test_extract_driveprojx_unzips(tmp_path):
    # A .driveprojx is just a zip; extract_driveprojx should unpack it intact.
    archive = tmp_path / "p.driveprojx"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("driveProj/project.xml", "<x/>")
    try:
        out = cli.extract_driveprojx(archive)
        assert (out / "driveProj" / "project.xml").read_text() == "<x/>"
    finally:
        cli.cleanup_temp_dirs()


def test_extract_driveprojx_rejects_zip_slip(tmp_path):  # security regression
    archive = tmp_path / "evil.driveprojx"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../escape.txt", "pwned")  # path-traversal member
    try:
        with pytest.raises(ValueError):
            cli.extract_driveprojx(archive)
    finally:
        cli.cleanup_temp_dirs()


def _patch_home(monkeypatch, path):
    """Point the home directory at `path` on every platform: Path.home() reads
    HOME on POSIX but USERPROFILE on Windows — patching only HOME silently
    does nothing on a Windows runner and the real Downloads folder wins."""
    monkeypatch.setenv("HOME", str(path))
    monkeypatch.setenv("USERPROFILE", str(path))


def test_resolve_output_path_relative_anchors_to_writable_dir(tmp_path, monkeypatch):
    # REGRESSION: a double-clicked app runs with a read-only cwd ('/' on macOS).
    # A bare filename must resolve to a writable folder, NOT the cwd.
    _patch_home(monkeypatch, tmp_path)  # no Downloads -> falls back to home
    p = cli.resolve_output_path("dw_comparison.html")
    assert p.is_absolute()
    assert p == tmp_path / "dw_comparison.html"


def test_resolve_output_path_empty_uses_default_name(tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    assert cli.resolve_output_path("") == tmp_path / "dw_comparison.html"
    assert cli.resolve_output_path("   ") == tmp_path / "dw_comparison.html"


def test_resolve_output_path_prefers_downloads(tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    (tmp_path / "Downloads").mkdir()
    assert cli.resolve_output_path("r.html") == tmp_path / "Downloads" / "r.html"


def test_resolve_output_path_absolute_is_kept(tmp_path):
    target = tmp_path / "reports" / "out.html"
    assert cli.resolve_output_path(str(target)) == target


def test_build_version_source_is_line_scannable():
    # REGRESSION: the PyInstaller spec stamps the build version by line-scanning
    # dw_compare/_version.py for `__version__ = '...'`. If that literal moves or
    # changes shape, the build silently falls back to the spec default and the
    # bundle version stops matching the running app (the About-vs-build bug).
    import dw_compare
    vfile = Path(dw_compare.__file__).parent / "_version.py"
    scanned = None
    for line in vfile.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("__version__"):
            scanned = line.split("=", 1)[1].strip().strip("'\"")
            break
    assert scanned == dw_compare.__version__


def test_cleanup_temp_dirs_drains_the_list(tmp_path, monkeypatch):  # REGRESSION
    d = tmp_path / "extracted"
    d.mkdir()
    (d / "project.xml").write_text("<x/>", encoding="utf-8")
    monkeypatch.setattr(cli, "_temp_dirs", [str(d)])

    cli.cleanup_temp_dirs()

    assert not d.exists()        # the extraction is removed
    assert cli._temp_dirs == []  # and the list is drained, not left to grow across runs
