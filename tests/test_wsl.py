"""The Windows Subsystem for Linux seen from Windows (core/wsl.py), without a WSL: wsl.exe is replaced."""
from __future__ import annotations

import subprocess

import pytest

from axqua.core import wsl


@pytest.mark.parametrize("unc, expected", [
    (r"\\wsl.localhost\Ubuntu-24.04\srv\stud\opt\telemac-mascaret\configs\pysource.sh",
     ("Ubuntu-24.04", "/srv/stud/opt/telemac-mascaret/configs/pysource.sh")),
    (r"\\wsl$\Ubuntu\home\x", ("Ubuntu", "/home/x")),
    ("//wsl.localhost/Debian/opt/telemac", ("Debian", "/opt/telemac")),
    (r"\\WSL.LOCALHOST\Ubuntu", ("Ubuntu", "/")),
])
def test_a_windows_path_into_a_distribution_is_its_linux_path(unc, expected):
    assert wsl.is_unc(unc)
    assert wsl.from_unc(unc) == expected
    assert wsl.to_linux(unc) == expected[1]


@pytest.mark.parametrize("path, expected", [
    (r"C:\Users\Jürgen Müller\OneDrive\Dokumente\fall", "/mnt/c/Users/Jürgen Müller/OneDrive/Dokumente/fall"),
    ("D:\\", "/mnt/d"),
    ("/srv/stud/opt", "/srv/stud/opt"),
    (r"relative\case", "relative/case"),
])
def test_drive_paths_go_below_mnt_and_linux_paths_stay(path, expected):
    assert wsl.to_linux(path) == expected
    assert wsl.to_linux(wsl.to_linux(path)) == expected      # applied twice, unchanged


def test_a_linux_path_has_a_windows_spelling_through_its_distribution():
    assert wsl.to_windows("/mnt/c/Users/x/case") == "C:\\Users\\x\\case"
    assert wsl.to_windows("/srv/stud/r2d.slf", "Ubuntu-24.04") == \
        r"\\wsl.localhost\Ubuntu-24.04\srv\stud\r2d.slf"
    assert wsl.to_windows("/srv/stud/r2d.slf") == "/srv/stud/r2d.slf"   # no distro, no spelling
    unc = wsl.to_unc("Ubuntu", "/opt/telemac/configs/pysource.sh")
    assert wsl.from_unc(unc) == ("Ubuntu", "/opt/telemac/configs/pysource.sh")


def test_utf16_output_of_wsl_exe_is_read_as_text():
    german = "Das Windows-Subsystem für Linux hat keine installierten Distributionen."
    assert wsl.decode(german.encode("utf-16-le")) == german
    assert wsl.decode(b"\xff\xfe" + german.encode("utf-16-le")) == german
    assert wsl.decode("Ubuntu ✓".encode("utf-8")) == "Ubuntu ✓"
    assert wsl.decode(None) == ""


def test_the_list_of_distributions_is_read_in_any_language():
    text = ("  NAME            ZUSTAND         VERSION\n"
            "* Ubuntu-24.04    Running         2\n"
            "  Debian          Stopped         2\n"
            "  legacy          Stopped         1\n")
    found = wsl._parse_list(text)
    assert [d.name for d in found] == ["Ubuntu-24.04", "Debian", "legacy"]
    assert found[0].default and found[2].version == 1
    assert wsl.default_distro(found) == "Ubuntu-24.04"
    assert wsl.WslStatus(present=True, distros=found).usable
    assert not wsl.WslStatus(present=True, distros=[found[2]]).usable   # WSL 1 only


def test_a_command_inside_a_distribution(monkeypatch):
    monkeypatch.setattr(wsl, "executable", lambda: "wsl.exe")
    assert wsl.command(["apt-get", "install", "-y", "gfortran"], "Ubuntu", user="root") == \
        ["wsl.exe", "-d", "Ubuntu", "-u", "root", "--", "apt-get", "install", "-y", "gfortran"]
    seen = {}

    def fake(args, **kwargs):
        seen.update(args=args, env=kwargs["env"])
        return subprocess.CompletedProcess(args, 0, "/srv/stud".encode("utf-16-le"), b"")

    monkeypatch.setattr(wsl.subprocess, "run", fake)
    assert str(wsl.home("Ubuntu")) == "/srv/stud"
    assert seen["env"]["WSL_UTF8"] == "1"
    assert seen["args"][:4] == ["wsl.exe", "-d", "Ubuntu", "--"]


def test_without_wsl_exe_the_status_says_so(monkeypatch):
    monkeypatch.setattr(wsl, "executable", lambda: None)
    found = wsl.status()
    assert not found.present and not found.usable and "not found" in found.message
    assert wsl.distros() == []


def test_wsl_exe_without_a_distribution_reports_what_it_said(monkeypatch):
    monkeypatch.setattr(wsl, "executable", lambda: "wsl.exe")

    def fake(args, **kwargs):
        if "-l" in args:
            return subprocess.CompletedProcess(args, 1, b"", b"")
        return subprocess.CompletedProcess(
            args, 1, "Das Windows-Subsystem für Linux ist nicht installiert.".encode("utf-16-le"), b"")

    monkeypatch.setattr(wsl.subprocess, "run", fake)
    found = wsl.status()
    assert found.present and not found.usable
    assert found.message == "Das Windows-Subsystem für Linux ist nicht installiert."
