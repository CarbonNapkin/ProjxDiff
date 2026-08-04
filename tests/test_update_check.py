"""Tests for the free update check: version parsing/ordering, and the
notify-only decision (never network in the test — latest_release is stubbed)."""

import dw_compare.update_check as uc
from dw_compare._version import __version__


def test_as_tuple_strips_v_prefix_and_parses():
    assert uc._as_tuple("v1.2.3") == (1, 2, 3)
    assert uc._as_tuple("1.0.10") == (1, 0, 10)

def test_as_tuple_handles_noise_and_blanks():
    assert uc._as_tuple("") == (0,)
    assert uc._as_tuple("1.0.2-beta") == (1, 0, 2)   # digits-only per part

def test_as_tuple_ordering():
    assert uc._as_tuple("1.0.10") > uc._as_tuple("1.0.2")
    assert uc._as_tuple("2.0.0") > uc._as_tuple("1.9.9")

def test_check_for_update_returns_newer(monkeypatch):
    monkeypatch.setattr(uc, "latest_release", lambda timeout=2.5: "9.9.9")
    assert uc.check_for_update() == "9.9.9"

def test_check_for_update_none_when_current_is_latest(monkeypatch):
    monkeypatch.setattr(uc, "latest_release", lambda timeout=2.5: __version__)
    assert uc.check_for_update() is None

def test_check_for_update_none_when_current_is_newer(monkeypatch):
    monkeypatch.setattr(uc, "latest_release", lambda timeout=2.5: "0.0.1")
    assert uc.check_for_update() is None

def test_check_for_update_none_on_lookup_failure(monkeypatch):  # offline / rate-limited
    monkeypatch.setattr(uc, "latest_release", lambda timeout=2.5: None)
    assert uc.check_for_update() is None


# ---- one-click update: download + verify ----

import hashlib
import io


class _FakeResponse(io.BytesIO):
    """Minimal stand-in for urlopen's response (context manager + headers)."""

    def __init__(self, payload: bytes):
        super().__init__(payload)
        self.headers = {"Content-Length": str(len(payload))}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _serve(monkeypatch, responses: dict):
    """Map URL substring -> payload bytes; anything else raises."""
    def fake_urlopen(req, timeout=0):
        url = req.full_url
        for frag, payload in responses.items():
            if frag in url:
                return _FakeResponse(payload)
        raise OSError(f"unexpected URL {url}")
    monkeypatch.setattr(uc.urllib.request, "urlopen", fake_urlopen)


def test_platform_asset_mapping():
    assert uc.platform_asset("win32") == "ProjxDiff-setup.exe"
    assert uc.platform_asset("darwin") == "ProjxDiff-macos.zip"
    assert uc.platform_asset("linux") is None  # servers update themselves


def test_asset_url_shape():
    url = uc.asset_url("9.9.9", "ProjxDiff-setup.exe")
    assert url.endswith("/releases/download/v9.9.9/ProjxDiff-setup.exe")


def test_fetch_checksums_parses_sha256sum_format(monkeypatch):
    _serve(monkeypatch, {"SHA256SUMS.txt": b"abc123  ProjxDiff-setup.exe\n"
                                           b"def456 *ProjxDiff-macos.zip\n"
                                           b"not a checksum line\n"})
    sums = uc.fetch_checksums("9.9.9")
    assert sums == {"ProjxDiff-setup.exe": "abc123",
                    "ProjxDiff-macos.zip": "def456"}


def test_download_update_verifies_and_reports_progress(tmp_path, monkeypatch):
    payload = b"fake installer bytes" * 5000
    digest = hashlib.sha256(payload).hexdigest()
    _serve(monkeypatch, {
        "SHA256SUMS.txt": f"{digest}  ProjxDiff-setup.exe\n".encode(),
        "ProjxDiff-setup.exe": payload,
    })
    seen = []
    out = uc.download_update("9.9.9", tmp_path, asset="ProjxDiff-setup.exe",
                             progress=lambda done, total: seen.append((done, total)))
    assert out.read_bytes() == payload
    assert seen and seen[-1] == (len(payload), len(payload))


def test_download_update_rejects_checksum_mismatch(tmp_path, monkeypatch):
    payload = b"tampered bytes"
    _serve(monkeypatch, {
        "SHA256SUMS.txt": b"0" * 64 + b"  ProjxDiff-setup.exe\n",
        "ProjxDiff-setup.exe": payload,
    })
    import pytest
    with pytest.raises(RuntimeError, match="mismatch"):
        uc.download_update("9.9.9", tmp_path, asset="ProjxDiff-setup.exe")
    assert not (tmp_path / "ProjxDiff-setup.exe").exists()  # discarded


def test_download_update_refuses_release_without_checksum(tmp_path, monkeypatch):
    _serve(monkeypatch, {"SHA256SUMS.txt": b""})  # e.g. an old release
    import pytest
    with pytest.raises(RuntimeError, match="no verifiable checksum"):
        uc.download_update("1.0.7", tmp_path, asset="ProjxDiff-setup.exe")


def test_download_update_no_asset_for_platform(tmp_path, monkeypatch):
    monkeypatch.setattr(uc.sys, "platform", "linux")
    import pytest
    with pytest.raises(RuntimeError, match="no packaged update"):
        uc.download_update("9.9.9", tmp_path)
