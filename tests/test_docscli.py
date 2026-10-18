"""axqua docs: where Help finds the documentation (axqua.docscli)."""
from __future__ import annotations

import json

import pytest

from axqua import docscli


@pytest.mark.parametrize("browser, expected", [
    ("firefox_firefox.desktop", True),              # snap
    ("chromium_chromium.desktop", True),            # snap
    ("org.chromium.Chromium.desktop", True),        # Flatpak
    ("com.google.Chrome.desktop", True),            # Flatpak
    ("firefox-esr.desktop", False),
    ("google-chrome.desktop", False),
    ("debian-sensible-browser.desktop", False),
    ("", False),
])
def test_a_browser_whose_desktop_file_is_not_found_is_judged_by_its_name(browser, expected):
    assert docscli.sandboxed(browser, folders=[]) is expected


def test_the_folder_of_the_desktop_file_tells_a_sandboxed_browser_from_one_of_the_system(tmp_path):
    """Review of 2026-10-18: GNOME Web of the operating system is org.gnome.Epiphany.desktop, a name like the one of a Flatpak, and was taken for a sandbox."""
    system = tmp_path / "usr" / "share" / "applications"
    flatpak = tmp_path / "var" / "lib" / "flatpak" / "exports" / "share" / "applications"
    snap = tmp_path / "var" / "lib" / "snapd" / "desktop" / "applications"
    user = tmp_path / "home" / ".local" / "share" / "applications"
    for folder, name, command in [
            (system, "org.gnome.Epiphany.desktop", "epiphany %U"),
            (flatpak, "org.chromium.Chromium.desktop", "/usr/bin/flatpak run org.chromium.Chromium"),
            (snap, "firefox_firefox.desktop", "env BAMF_DESKTOP_FILE_HINT=x /snap/bin/firefox %u"),
            (system, "firefox-esr.desktop", "/usr/lib/firefox-esr/firefox-esr %u"),
            # a copy a menu editor wrote: the folder is gone, the command is still the snap
            (user, "chromium_chromium.desktop", "/snap/bin/chromium %U")]:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text(f"[Desktop Entry]\nName=x\nExec={command}\n")
    folders = [user, system, flatpak, snap]
    assert not docscli.sandboxed("org.gnome.Epiphany.desktop", folders)
    assert docscli.sandboxed("org.chromium.Chromium.desktop", folders)
    assert docscli.sandboxed("firefox_firefox.desktop", folders)
    assert not docscli.sandboxed("firefox-esr.desktop", folders)
    assert docscli.sandboxed("chromium_chromium.desktop", folders)
    # a copy that names /usr/bin/firefox (on Ubuntu a wrapper of the snap) beside the file of the snap
    (user / "firefox_firefox.desktop").write_text("[Desktop Entry]\nExec=/usr/bin/firefox %u\n")
    assert docscli.sandboxed("firefox_firefox.desktop", folders)


def test_a_sandboxed_browser_reads_the_home_folder_but_no_hidden_folder_in_it(tmp_path):
    home = tmp_path / "home"
    assert docscli.readable_by_sandbox(home / "aXqua" / "docs" / "_build" / "site", home)
    assert not docscli.readable_by_sandbox(home / ".local" / "share" / "docs", home)
    assert not docscli.readable_by_sandbox(tmp_path / "srv" / "docs", home)


def _site(folder, languages=("en",)):
    for lang in languages:
        page = folder / ("" if lang == "en" else lang) / "index.html"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text("<html></html>")


def test_the_folder_named_in_the_environment_is_the_local_copy(tmp_path, monkeypatch):
    site = tmp_path / "site"
    _site(site, ("en", "de", "fr"))
    monkeypatch.setenv(docscli.ENV_VAR, str(site))
    answer = docscli.describe(browser="firefox-esr.desktop")
    assert answer["local"] == str(site.resolve())
    assert answer["languages"] == ["en", "de", "fr"] and answer["readable"]
    assert answer["published"] == "https://axqua.eu/"


def test_a_copy_a_sandboxed_browser_cannot_read_is_reported_with_the_reason(tmp_path, monkeypatch):
    site = tmp_path / "site"
    _site(site)
    monkeypatch.setenv(docscli.ENV_VAR, str(site))
    monkeypatch.setattr(docscli, "readable_by_sandbox", lambda folder, home=None: False)
    answer = docscli.describe(browser="chromium_chromium.desktop")
    assert answer["local"] and not answer["readable"]
    assert "sandbox" in answer["reason"]


def test_without_a_built_copy_only_the_published_address_is_given(tmp_path, monkeypatch):
    monkeypatch.setenv(docscli.ENV_VAR, str(tmp_path / "nothing"))
    monkeypatch.setattr(docscli, "checkout", lambda: None)
    answer = docscli.describe(browser="")
    assert answer["local"] is None and not answer["readable"]


def test_the_command_answers_as_json(tmp_path, monkeypatch, capsys):
    from axqua import cli

    site = tmp_path / "site"
    _site(site)
    monkeypatch.setenv(docscli.ENV_VAR, str(site))
    monkeypatch.setattr(docscli, "default_browser", lambda: "")
    assert cli.main(["docs", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)["data"]
    assert data["local"] == str(site.resolve()) and data["readable"]
