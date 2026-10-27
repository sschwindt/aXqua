# The documentation in German and French, and its publication

The documentation is written in English. German and French pages are machine translations of it, kept per page in `docs/locale/<lang>/LC_MESSAGES/` and published together with the English pages on GitHub Pages:

| Address | Language | Source |
|---|---|---|
| `https://axqua.eu/` | English | the `.rst` files in `docs/` |
| `https://axqua.eu/de/` (`axqua.de` and `axqua.ch` forward here) | German | `docs/locale/de/LC_MESSAGES/**.po` |
| `https://axqua.eu/fr/` (`axqua.fr` forwards here) | French | `docs/locale/fr/LC_MESSAGES/**.po` |
| `https://axqua.readthedocs.io` | English | built by Read the Docs from `main` |

How the domains are set up at the registrars is in the [README of the repository](../../README.md#domains).

## Publishing

```bash
mamba env create -f docs/i18n/environment.yml   # once: Sphinx, the theme, Argos Translate
bash deploy.sh                                  # build and look at docs/_build/site/index.html
bash deploy.sh --push                           # publish
```

`deploy.sh --push` builds the three languages, translates what is new or changed, commits the updated catalogs on `main`, pushes `main`, and commits and pushes the site on `gh-pages`. It refuses to run with uncommitted changes (what is published has to be what `main` holds), with a `main` that is behind GitHub, or with a `gh-pages` that has diverged. Pushing `main` also updates Read the Docs, which builds the English pages by itself and never runs these scripts.

The `gh-pages` branch is checked out in its own worktree, `../aXqua-gh-pages` beside the checkout (`DEPLOY_DIR` names another one; a missing one is created with `git worktree add`). It holds the built site, `CNAME` (`axqua.eu`) and `.nojekyll`, which keeps GitHub from running Jekyll (Jekyll drops every folder whose name starts with an underscore, such as `_static`). Never edit it by hand: each deploy replaces its content.

## How the site is built

`docs/i18n/build_i18n.sh` (called by `deploy.sh`):

1. builds the English pages into `docs/_build/site/`, with warnings as errors as in the CI;
2. extracts one message template per page (`sphinx-build -b gettext`, into `docs/_build/i18n/gettext/`);
3. updates the catalogs and translates what is missing (`translate.py`);
4. builds the German and French pages into `docs/_build/site/de/` and `fr/` (`-D language=de|fr`), with their warnings in `docs/_build/i18n/warnings-<lang>.log`;
5. adds the language button in the top right corner, `hreflang` links, the note on machine translation and a `404.html` (`inject_i18n.py`, `assets/`).

`ENGINE=none bash docs/i18n/build_i18n.sh` builds with the translations that are there and translates nothing: new text then shows in English on the German and French pages.

**Which language a reader sees** (`assets/lang.js`): the address always wins, so a German or French page is never redirected. A reader who arrives from outside the site on an English page is sent to the language chosen before, or else to the language of the browser if that is German or French. A language chosen with the button in the top right corner is remembered. Following links within the site redirects nothing.

## The catalogs are the translation memory

A machine translation carries the translator comment `machine translation: <engine> <hash>`. The hash identifies what was sent to the engine for that message, so an entry is translated again only when it is new, when its English text changed, or when a change to the glossary or to the term repairs changes what would be sent for it. A message that moved to another page keeps its translation. A deploy therefore translates only the paragraphs that changed, in seconds.

**Correcting a translation by hand:** edit the `msgstr` in the `.po` file of the page and delete the comment line `# machine translation: ...` above it. The entry is then kept as long as its English text stays the same, also by `--retranslate`. When the English text changes, the entry is translated by machine again and has to be reviewed again. Any editor for gettext catalogs works (for instance Poedit), as long as it keeps translator comments.

**Installation tutorial video:** the English and French builds use `https://youtu.be/gK83Q8bJCHI`; the German build uses `https://youtu.be/oBcTvuRuKyk`. `_localize_installation_video` in `docs/conf.py` changes this tutorial's embedded player and direct link on any page under `installation/` after gettext has translated the page, so catalog updates do not reset the video choice. The German title and link caption are reviewed entries in the corresponding catalogs under `docs/locale/de/LC_MESSAGES/installation/` (`index.po`, `plugin-setup.po`, `qgis-plugin.po`); their source URL stays English because the build hook selects the German target afterwards. When the same tutorial block is added to another installation page, `translate.py` reuses these reviewed labels from its cross-page translation memory.

Settings of `build_i18n.sh` and `deploy.sh`:

| Variable | Effect |
|---|---|
| `ENGINE` | `argos` (offline, the default of a deploy), `deepl` (`pip install deepl`, `DEEPL_API_KEY`; not tried yet), `none` |
| `RETRANSLATE=1` | translate every machine translation again (`deploy.sh --retranslate`) |
| `I18N_JOBS=N` | translation workers, each with two threads (default: a quarter of the cores, at most 4) |
| `I18N_FAST=1` | greedy decoding, about a quarter faster and slightly worse wording |
| `DOCSTRINGS=1` | also translate the reference of the Python code (see below) |
| `DOCS_PYTHON` | the Python of the documentation environment, if it is not `axqua-docs` under a usual conda root |

A full translation of both languages takes about four minutes with four workers on a 32-core computer.

## What is translated and what is not

Translated: prose, headings, list items, table cells, captions, the visible text of links and of cross-references with an explicit title. Never translated: inline literals (``` ``...`` ```), roles and cross-references, hyperlink targets, substitutions, footnote references, URLs, the names in `keep.txt`, entries of reference lists (the titles of papers), and text in single asterisks, which these pages use for the labels of the plugin: the plugin is in English, so a German page has to name the button the reader sees.

The reference of the Python code (the docstrings that `autodoc` collects) stays in English in all three languages. It is the text of the code, with the names of functions and parameters in every sentence, and its readers are developers; translated, it would also triple the size of the catalogs. `DOCSTRINGS=1` includes it.

## Glossary and term repairs

Argos Translate knows no hydraulic engineering: it renders *reach* as *Reichweite* and *portée*, *rating curve* as *courbe de goudron*. The glossaries `glossary.de.tsv` and `glossary.fr.tsv` fix the terms:

```
<english>[|<english plural>]  TAB  <target>[|<target plural>]  [TAB m|f]
```

`translate.py` puts the target term into the English sentence before it goes to the engine, which then declines the term and chooses the article itself: *"The Wassertiefe at the Zuflussrand is computed from the Abfluss"* comes back as *"Die Wassertiefe am Zuflussrand wird aus dem Abfluss berechnet"*. French terms are sent in title case (*Courbe De Tarage*), because the engine copies a capitalized term like a name but translates rare French words a second time when they are in lower case; the case is restored afterwards, and the gender in the third column makes the article agree (*un tâche* becomes *une tâche*). A row whose target is the English term keeps the word in English (*Preprocessing*, *Layer*). An acronym matches in capitals only. A longer term wins over a shorter one inside it.

`termfix.<lang>.tsv` repairs what the engine still gets wrong, as a regular expression over its answer that runs only on messages whose English text contains a given term:

```
<english term>  TAB  <regular expression over the translation>  TAB  <replacement>
```

A change to either file translates again only the messages it concerns, at the next deploy. Reference for German and French terms: [TERMDAT](https://www.termdat.bk.admin.ch/), and for French the manuals of TELEMAC.

## Repairs made after the engine

Things Argos does that `translate.py` repairs or catches, each found in the first full translation:

- It spaces out or loses parts of the placeholders that stand for masked text (`@ @ 2 @ @`, `@1@@`). Those are restored; a placeholder that lost its number fails the message, which is then sent again with the bold text translated on its own, and then sentence by sentence. What still fails stays in English and is listed in `docs/_build/i18n/failed-<lang>.txt`; a message that had a translation and fails when it is translated anew keeps the earlier one, is listed there as well, and is tried again in the next run.
- It invents a sentence around messages that are only code and *or* (`@@0@@ or @@1@@` came back as *À l'adresse suivante:*). These are put together without the engine. A masked part at the start or the end of a paragraph is not sent either (*Autres* instead of a label in italics).
- It writes a German sentence entirely in lower case now and then. A sentence of eight words or more without a capital gets the capitals of the words that the German pages write with a capital elsewhere.
- French needs a non-breaking space before `: ; ! ?`, which is added.

## Read the Docs in German and French (optional)

Read the Docs serves translations as separate projects. To offer German and French on `axqua.readthedocs.io` too: create two projects from the same repository with the language set to German and French (for example `axqua-de` and `axqua-fr`), and add them under *Admin > Translations* of the `axqua` project. Read the Docs then builds them with `-D language=de|fr` from the catalogs on `main` (`locale_dirs` in `docs/conf.py`) and shows its own language menu. Nothing in this repository has to change for that.

## Files

| File | Role |
|---|---|
| `../../deploy.sh` | builds, then publishes `main` and `gh-pages` |
| `build_i18n.sh` | builds the three languages into `docs/_build/site/` |
| `translate.py` | updates the catalogs and translates what is missing; `--check` tests the masking without an engine |
| `inject_i18n.py`, `assets/` | language button, `hreflang`, note on machine translation, `404.html` |
| `glossary.<lang>.tsv`, `termfix.<lang>.tsv`, `keep.txt` | terms |
| `environment.yml` | the `axqua-docs` environment |
| `../locale/<lang>/LC_MESSAGES/` | the catalogs, one per page (tracked; the `.mo` files Sphinx compiles from them are not) |
