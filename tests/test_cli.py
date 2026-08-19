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


# ---------- --doctor self-check ----------

def test_doctor_reports_and_passes_from_source(capsys):
    assert cli.doctor() == 0
    out = capsys.readouterr().out
    assert f'Projx Diff {cli.__version__}' in out
    assert 'pyodbc' in out


def test_doctor_fails_when_frozen_windows_build_lacks_pyodbc(capsys, monkeypatch):
    # The 1.5.1 regression class: a packaged Windows exe without pyodbc
    # fails soft at runtime (raw GUIDs), so --doctor is the only loud
    # signal — it must exit nonzero for the release workflow to catch.
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setitem(sys.modules, 'pyodbc', None)  # import raises
    assert cli.doctor() == 1
    assert 'FAIL' in capsys.readouterr().out


def test_doctor_tolerates_missing_pyodbc_elsewhere(capsys, monkeypatch):
    # From source (any OS) pyodbc is an optional extra — never a failure.
    monkeypatch.setitem(sys.modules, 'pyodbc', None)
    assert cli.doctor() == 0
    assert 'MISSING' in capsys.readouterr().out


def test_doctor_flag_dispatches(monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['dw_compare', '--doctor'])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0


# ---------- project auto-detect ----------

def test_find_project_folders_prefers_old_new_names(tmp_path, monkeypatch):
    for name in ('MyProj old', 'MyProj new', 'unrelated'):
        (tmp_path / name).mkdir()
    monkeypatch.chdir(tmp_path)
    old, new = cli.find_project_folders()
    assert old.name == 'MyProj old' and new.name == 'MyProj new'


def test_find_project_folders_matches_projx_files_by_pattern(tmp_path, monkeypatch):
    (tmp_path / 'Widget_v1.driveprojx').touch()
    (tmp_path / 'Widget_v2.driveprojx').touch()
    (tmp_path / 'notes.txt').touch()
    monkeypatch.chdir(tmp_path)
    old, new = cli.find_project_folders()
    assert (old.name, new.name) == ('Widget_v1.driveprojx', 'Widget_v2.driveprojx')


def test_find_project_folders_exactly_two_folders_wins_by_sort(tmp_path, monkeypatch):
    (tmp_path / 'Bravo').mkdir()
    (tmp_path / 'Alpha').mkdir()
    monkeypatch.chdir(tmp_path)
    old, new = cli.find_project_folders()
    assert (old.name, new.name) == ('Alpha', 'Bravo')


def test_find_project_folders_none_when_ambiguous(tmp_path, monkeypatch):
    for name in ('A', 'B', 'C'):
        (tmp_path / name).mkdir()
    monkeypatch.chdir(tmp_path)
    assert cli.find_project_folders() is None


def test_find_project_folders_ignores_hidden_and_build_dirs(tmp_path, monkeypatch):
    (tmp_path / '.git').mkdir()
    (tmp_path / 'old_proj').mkdir()
    (tmp_path / 'new_proj').mkdir()
    monkeypatch.chdir(tmp_path)
    old, new = cli.find_project_folders()
    assert (old.name, new.name) == ('old_proj', 'new_proj')


# ---------- CLI database wiring: env-var password rules ----------

def _db_args(**over):
    from types import SimpleNamespace
    base = dict(old_db_server='S', old_db_database='D',
                old_db_user='u', old_db_sql_auth=False)
    base.update(over)
    return SimpleNamespace(**base)


def _capture_resolve(monkeypatch):
    seen = {}

    def fake(label, server, database, index, user='', password='', sql_auth=False):
        seen.update(label=label, password=password, sql_auth=sql_auth)
        return {}, {}, {}, None

    monkeypatch.setattr(cli, 'resolve_db_names', fake)
    return seen


def test_side_password_env_var_wins(monkeypatch):
    seen = _capture_resolve(monkeypatch)
    monkeypatch.setenv('DW_SQL_PASSWORD_OLD', 'side-secret')
    monkeypatch.setenv('DW_SQL_PASSWORD', 'shared-secret')
    cli.resolve_side_names('old', _db_args(old_db_sql_auth=True), index=None)
    assert seen['password'] == 'side-secret'


def test_shared_password_env_var_is_the_fallback(monkeypatch):
    # README documents DW_SQL_PASSWORD as the single-password path;
    # REGRESSION: the code only read the _OLD/_NEW forms.
    seen = _capture_resolve(monkeypatch)
    monkeypatch.delenv('DW_SQL_PASSWORD_OLD', raising=False)
    monkeypatch.setenv('DW_SQL_PASSWORD', 'shared-secret')
    cli.resolve_side_names('old', _db_args(old_db_sql_auth=True), index=None)
    assert seen['password'] == 'shared-secret'


def test_sql_auth_with_no_password_env_skips_with_error(monkeypatch, capsys):
    _capture_resolve(monkeypatch)
    monkeypatch.delenv('DW_SQL_PASSWORD_OLD', raising=False)
    monkeypatch.delenv('DW_SQL_PASSWORD', raising=False)
    resolved, props, types_, error = cli.resolve_side_names(
        'old', _db_args(old_db_sql_auth=True), index=None)
    assert resolved == {} and error and 'DW_SQL_PASSWORD' in error


def test_windows_auth_never_reads_password_env(monkeypatch):
    seen = _capture_resolve(monkeypatch)
    monkeypatch.setenv('DW_SQL_PASSWORD', 'should-not-be-used')
    cli.resolve_side_names('old', _db_args(old_db_sql_auth=False), index=None)
    assert seen['password'] == ''


def test_unconfigured_side_is_silent_no_op(monkeypatch):
    seen = _capture_resolve(monkeypatch)
    result = cli.resolve_side_names('old', _db_args(old_db_server=''), index=None)
    assert result == ({}, {}, {}, None)
    assert seen == {}  # resolve_db_names never called


# ------------------------------------------------------- packaged entry ----

def _entry_point():
    """Import run_dw_compare.py (the PyInstaller entry script) by path — it
    lives at the repo root, not in the package."""
    import importlib.util
    path = Path(__file__).resolve().parents[1] / 'run_dw_compare.py'
    spec = importlib.util.spec_from_file_location('_run_dw_compare_probe', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize('exe, is_cli', [
    (r'C:\Program Files\Projx Diff\ProjxDiff-cli.exe', True),
    (r'C:\Program Files\Projx Diff\ProjxDiff.exe', False),
    ('/usr/local/bin/python3', False),
])
def test_the_console_build_is_recognised_by_its_own_filename(monkeypatch, exe, is_cli):
    """The two Windows exes are one build differing only in subsystem, so the
    only thing the running process can tell them apart by is its own name. A
    bare ProjxDiff.exe means a double-click and should open the GUI; a bare
    ProjxDiff-cli.exe means someone at a prompt, and should not."""
    mod = _entry_point()
    monkeypatch.setattr(sys, 'executable', exe)
    assert mod._is_cli_build() is is_cli
