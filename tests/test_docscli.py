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
def test_a_browser_in_a_snap_or_flatpak_sandbox_is_recognized(browser, expected):
    assert docscli.sandboxed(browser) is expected


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
