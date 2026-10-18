#!/usr/bin/env python3
"""Translate the documentation into German and French.

``sphinx-build -b gettext`` writes one message template (``.pot``) per page. This script keeps one catalog (``.po``) per page and language under ``docs/locale/<lang>/LC_MESSAGES/`` and fills in what is missing with a machine translation. Sphinx reads these catalogs when it builds with ``-D language=de`` or ``fr``, on this computer as on Read the Docs.

The catalogs are also the translation memory. A machine translation carries the translator comment ``machine translation: <engine> <hash>``, where the hash identifies exactly what was sent to the engine. An entry is translated again only when it is new, when its English text changed, or when a change to the glossary or to the term repairs changes what would be sent for it. An entry without that comment was reviewed by a person and is kept as long as its English text stays the same.

What is never translated: inline literals, roles and cross-references, hyperlink targets, substitutions, footnote references, URLs, the names in ``keep.txt``, and text in single asterisks, which these pages use for the labels of the plugin (the plugin itself is in English, so a German page has to name the button the reader will see). Bold text is translated in its sentence, the visible text of a link or of a cross-reference with an explicit title on its own.

The glossary (``glossary.<lang>.tsv``) is applied before the translation: the target term replaces the English term in the sentence that is sent to the engine, which then declines it and chooses the article itself. German nouns are capitalized anyway and the engine copies them. A French term is sent in title case (``Courbe De Tarage``), because the engine copies a capitalized term like a name and otherwise translates rare French words a second time (``tarage`` came back as ``goudron``); the case is restored afterwards. ``termfix.<lang>.tsv`` repairs what the engine still gets wrong.

Usage::

    translate.py --pot docs/_build/i18n/gettext --engine argos      # what changed
    translate.py --pot docs/_build/i18n/gettext --engine none       # catalogs only
    translate.py --pot docs/_build/i18n/gettext --engine argos --retranslate
    translate.py --pot docs/_build/i18n/gettext --check             # masking self-test
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import multiprocessing
import os
import re
import sys
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

I18N = Path(__file__).resolve().parent
DOCS = I18N.parent
LOCALE = DOCS / "locale"
LANGS = ("de", "fr")
LANGUAGE_NAMES = {"de": "German", "fr": "French"}

#: Bump to translate every machine entry again after a change to how a message is prepared for the engine (masking, injection). A change to the glossary or to the term repairs needs no bump: it changes the hash of the entries it concerns, and only of those.
PREPARATION_VERSION = 1

#: How a glossary term enters the sentence that is sent to the engine (see the module docstring).
INJECTION = {"de": "plain", "fr": "title"}

MACHINE_RE = re.compile(r"^machine translation: (\S+) ([0-9a-f]{12})$")

# Argos builds its sentence splitter with logging_level="WARNING" and Stanza then logs "Language en package default expects mwt, which has been added" once per worker. A filter rather than a level, because Stanza resets the level whenever a pipeline is created.
logging.getLogger("stanza").addFilter(lambda record: "expects mwt" not in record.getMessage())


# ----------------------------------------------------------------------------------------------
# Catalogs
# ----------------------------------------------------------------------------------------------

@dataclass
class Entry:
    """One message of a catalog or of a template."""

    msgid: str
    msgstr: str = ""
    msgctxt: str | None = None
    comments: list[str] = field(default_factory=list)   # "# ..." (translator comments)
    locations: list[str] = field(default_factory=list)  # "#: ..."
    flags: list[str] = field(default_factory=list)      # "#, ..."

    @property
    def key(self) -> tuple[str | None, str]:
        return (self.msgctxt, self.msgid)

    @property
    def machine(self) -> tuple[str, str] | None:
        """``(engine, hash)`` of a machine translation, None for a reviewed entry."""
        for comment in self.comments:
            match = MACHINE_RE.match(comment.strip())
            if match:
                return match.group(1), match.group(2)
        return None

    @property
    def fuzzy(self) -> bool:
        return "fuzzy" in self.flags

    @property
    def from_docstring(self) -> bool:
        """True for a message that autodoc took from a docstring and from nowhere else."""
        return bool(self.locations) and all("docstring of" in loc for loc in self.locations)


_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\t": "\\t", "\r": "\\r"}
_UNESCAPES = {"\\": "\\", '"': '"', "n": "\n", "t": "\t", "r": "\r"}


def _escape(text: str) -> str:
    return "".join(_ESCAPES.get(ch, ch) for ch in text)


def _unescape(text: str) -> str:
    return re.sub(r"\\(.)", lambda m: _UNESCAPES.get(m.group(1), m.group(1)), text)


def read_catalog(path: Path) -> list[Entry]:
    """Read a ``.po`` or ``.pot`` file. The header entry and obsolete entries are left out."""
    entries: list[Entry] = []
    current: Entry | None = None
    target: str | None = None
    pending: dict = {"comments": [], "locations": [], "flags": []}

    def finish() -> None:
        nonlocal current, target
        if current is not None and current.msgid:
            entries.append(current)
        current, target = None, None

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            finish()
            continue
        if line.startswith("#~"):
            continue
        if line.startswith("#"):
            if current is not None and target == "msgstr":
                finish()
            if line.startswith("#:"):
                pending["locations"].append(line[2:].strip())
            elif line.startswith("#,"):
                pending["flags"].extend(f.strip() for f in line[2:].split(",") if f.strip())
            elif line.startswith(("#.", "#|")):
                pass
            else:
                pending["comments"].append(line[1:].strip())
            continue
        match = re.match(r'^(msgctxt|msgid_plural|msgid|msgstr(?:\[\d+\])?)\s+"(.*)"$', line)
        if match:
            keyword, value = match.group(1), _unescape(match.group(2))
            if keyword == "msgctxt" or (keyword == "msgid" and (current is None or target == "msgstr")):
                finish()
                current = Entry(msgid="", **pending)
                pending = {"comments": [], "locations": [], "flags": []}
            if current is None:
                continue
            if keyword == "msgctxt":
                current.msgctxt, target = value, "msgctxt"
            elif keyword == "msgid":
                current.msgid, target = value, "msgid"
            elif keyword == "msgid_plural":
                target = "msgid_plural"
            elif keyword in ("msgstr", "msgstr[0]"):
                current.msgstr, target = value, "msgstr"
            else:
                target = "msgstr_other"
            continue
        if line.startswith('"') and line.endswith('"') and current is not None:
            value = _unescape(line[1:-1])
            if target == "msgctxt":
                current.msgctxt = (current.msgctxt or "") + value
            elif target == "msgid":
                current.msgid += value
            elif target == "msgstr":
                current.msgstr += value
    finish()
    return entries


def render_catalog(entries: list[Entry], lang: str, page: str) -> str:
    """The text of a catalog. No line is wrapped, so a diff shows whole messages."""
    name = LANGUAGE_NAMES[lang]
    lines = [
        f"# {name} translation of the aXqua documentation, page {page}.",
        "# Written by docs/i18n/translate.py. An entry with the comment \"machine translation\" is",
        "# translated again when its English text changes. Delete that comment after reviewing an",
        "# entry, and the translation is kept as long as the English text stays the same.",
        'msgid ""',
        'msgstr ""',
        '"Project-Id-Version: aXqua\\n"',
        f'"Language: {lang}\\n"',
        '"MIME-Version: 1.0\\n"',
        '"Content-Type: text/plain; charset=UTF-8\\n"',
        '"Content-Transfer-Encoding: 8bit\\n"',
        '"Plural-Forms: nplurals=2; plural=(n > 1);\\n"' if lang == "fr"
        else '"Plural-Forms: nplurals=2; plural=(n != 1);\\n"',
        "",
    ]
    for entry in entries:
        lines.extend(f"# {comment}" for comment in entry.comments)
        if entry.flags:
            lines.append("#, " + ", ".join(entry.flags))
        if entry.msgctxt is not None:
            lines.append(f'msgctxt "{_escape(entry.msgctxt)}"')
        lines.append(f'msgid "{_escape(entry.msgid)}"')
        lines.append(f'msgstr "{_escape(entry.msgstr)}"')
        lines.append("")
    return "\n".join(lines)


# ----------------------------------------------------------------------------------------------
# Glossary, term repairs and names that are kept
# ----------------------------------------------------------------------------------------------

def _rows(path: Path) -> list[list[str]]:
    rows = []
    if not path.exists():
        return rows
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = [part.strip() for part in line.split("\t")]
        rows.append([f"{path.name}:{number}", *parts])
    return rows


def _term_pattern(term: str) -> str:
    """Whole-word pattern for an English term; a space also matches a hyphen and vice versa."""
    words = re.split(r"[\s-]+", term.strip())
    return r"(?<![\w-])" + r"[\s-]+".join(re.escape(word) for word in words) + r"(?![\w-])"


@dataclass
class Term:
    english: tuple[str, str]   # singular, plural
    target: tuple[str, str]
    gender: str = ""           # "m" or "f": the articles in front of a French term are made to agree


@dataclass
class Glossary:
    lang: str
    terms: list[Term] = field(default_factory=list)
    pattern: re.Pattern | None = None
    lookup: dict[str, tuple[Term, int]] = field(default_factory=dict)

    @classmethod
    def load(cls, lang: str, folder: Path = I18N) -> Glossary:
        glossary = cls(lang)
        for where, *parts in _rows(folder / f"glossary.{lang}.tsv"):
            if len(parts) < 2 or not parts[0] or not parts[1]:
                sys.exit(f"{where}: expected <english>[|<plural>] TAB <{lang}>[|<plural>] [TAB m|f]")
            english = [form.strip() for form in parts[0].split("|")]
            target = [form.strip() for form in parts[1].split("|")]
            english_forms = (english[0], english[1] if len(english) > 1 else english[0])
            target_forms = (target[0], target[1] if len(target) > 1 else target[0])
            gender = parts[2].lower() if len(parts) > 2 and parts[2].lower() in ("m", "f") else ""
            glossary.terms.append(Term(english_forms, target_forms, gender))
        alternatives = []
        for term in glossary.terms:
            for number, form in enumerate(term.english):
                glossary.lookup.setdefault(_normal(form), (term, number))
                alternatives.append(form)
        if alternatives:
            # one alternation, longest first, so that a term is never replaced inside another
            ordered = sorted(set(alternatives), key=len, reverse=True)
            parts = []
            for form in ordered:
                pattern = _term_pattern(form)
                parts.append(pattern if form.isupper() else f"(?i:{pattern})")
            glossary.pattern = re.compile("|".join(parts))
        return glossary

    def find(self, matched: str) -> tuple[Term, int] | None:
        return self.lookup.get(_normal(matched))


def _normal(text: str) -> str:
    """Lookup key of an English term: spaces and hyphens alike, case ignored unless an acronym."""
    key = re.sub(r"[\s-]+", " ", text.strip())
    return key if key.isupper() else key.lower()


@dataclass
class TermFix:
    term_re: re.Pattern
    pattern: re.Pattern
    replacement: str
    line: str


def load_termfix(lang: str, folder: Path = I18N) -> list[TermFix]:
    """``termfix.<lang>.tsv``: ``<english term> TAB <regex> TAB <replacement>``.

    A rule runs only on a message whose English text contains its term, so that a generic word of the translation can be repaired without touching other messages.
    """
    rules = []
    for where, *parts in _rows(folder / f"termfix.{lang}.tsv"):
        if len(parts) != 3 or not parts[0] or not parts[1]:
            sys.exit(f"{where}: expected <english term> TAB <regex> TAB <replacement>")
        try:
            pattern = re.compile(parts[1])
        except re.error as error:
            sys.exit(f"{where}: {parts[1]!r} is no regular expression: {error}")
        # the term triggers the rule in its plural and as the first part of a compound too ("solvers", "Solver-agnostic")
        words = re.split(r"[\s-]+", parts[0].strip())
        trigger = (r"(?<!\w)" + r"[\s-]+".join(re.escape(word) for word in words)
                   + r"(?:s|es)?(?!\w)")
        rules.append(TermFix(re.compile(trigger, re.IGNORECASE), pattern, parts[2],
                             "\t".join(parts)))
    return rules


def load_keep(folder: Path = I18N) -> list[str]:
    """Names that are never translated (``keep.txt``), longest first."""
    path = folder / "keep.txt"
    if not path.exists():
        return []
    names = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
             if line.strip() and not line.lstrip().startswith("#")]
    return sorted(set(names), key=len, reverse=True)


# ----------------------------------------------------------------------------------------------
# Preparing a message for the engine
# ----------------------------------------------------------------------------------------------

SENTINEL = "@@{}@@"
# Argos spaces a sentinel out ("@ @ 2 @ @"), drops an "@" ("@1@@") or doubles one, which must all still restore. The trailing "@" are taken only while they do not open the next sentinel ("@@3@@ @@4@@"). What is left of a sentinel that lost its number fails the check in assemble.
SENTINEL_RE = re.compile(r"@(?:\s?@)*\s?(\d+)\s?@(?:\s?@(?!\s?@?\s?\d+\s?@))*")

# Order matters: an inline literal can hold anything, so it goes first; a role or link with an explicit title before the generic forms of the same markup.
LITERAL = re.compile(r"``.+?``(?!`)")
ESCAPE = re.compile(r"\\.")
TITLED_ROLE = re.compile(r"(:(?:[\w.-]+:)*(?:ref|doc|term):`)([^`<]*?\S)(\s*<[^`>]+>`)")
ROLE = re.compile(r":(?:[\w.+-]+:)+`[^`]*`")
TITLED_LINK = re.compile(r"(`)([^`<]*?\S)(\s*<[^`>]+>`__?)")
INTERPRETED = re.compile(r"_?`[^`]+`_{0,2}")
SUBSTITUTION = re.compile(r"(?<![\w|])\|[^|\s](?:[^|]*[^|\s])?\|_{0,2}(?![\w|])")
FOOTNOTE = re.compile(r"\[(?:#[\w-]*|\*|\d+|[A-Za-z][\w.-]*)\]_")
STRONG = re.compile(r"(?<![\w*])(\*\*)(?=\S)([^*]+?)(?<=\S)(\*\*)(?![\w*])")
EMPHASIS = re.compile(r"(?<![\w*])\*(?=[^\s*])[^*]+?(?<=[^\s*])\*(?![\w*])")
URL = re.compile(r"https?://[^\s<>`]*[^\s<>`.,;:!?)\]]")


@dataclass
class Span:
    """What a sentinel stands for."""

    text: str = ""                      # restored as it is
    markup: bool = False                # inline markup: needs a space or punctuation around it
    before: str = ""                    # around a translated part
    after: str = ""
    sub: Prepared | None = None         # a part that is translated on its own


@dataclass
class Prepared:
    """A message as it is sent to the engine, and what is needed to put it back together."""

    source: str                         # the English text (for the term repairs)
    text: str                           # masked, with the glossary terms in it
    spans: list[Span] = field(default_factory=list)
    injected: list[str] = field(default_factory=list)   # canonical targets injected in title case
    prefix: str = ""                    # masked parts at the start and the end, which do not go to
    suffix: str = ""                    # the engine ("@@0@@ The solver ..." came back "Autres ...")
    words: dict[str, str] | None = None  # set when only connecting words are left ("@@0@@ or @@1@@")
    lang: str = ""
    genders: dict[str, str] = field(default_factory=dict)  # injected French term -> "m" or "f"
    expect: list[str] = field(default_factory=list)  # terms put into the sentence: must come back
    names: list[str] = field(default_factory=list)   # names of keep.txt sent as they are

    def pieces(self) -> list[str]:
        """Every text that goes to the engine for this message."""
        out = [self.text] if needs_engine(self.text) and self.words is None else []
        for span in self.spans:
            if span.sub is not None:
                out.extend(span.sub.pieces())
        return out


def needs_engine(text: str) -> bool:
    """False when nothing is left to translate once the masked parts are taken out."""
    return re.search(r"[A-Za-z]{2,}", SENTINEL_RE.sub(" ", text)) is not None


#: Messages that are masked parts joined by these words are put together without the engine, which invents a sentence around them ("``a`` or ``b``" came back "À l'adresse suivante:").
CONNECTORS = {
    "de": {"or": "oder", "and": "und", "to": "bis", "with": "mit", "per": "pro", "of": "von"},
    "fr": {"or": "ou", "and": "et", "to": "à", "with": "avec", "per": "par", "of": "de"},
}

#: An entry of a list of references: the title of a paper is never translated.
REFERENCE = re.compile(r"^[A-Z][\w'’-]+(?: [A-Z][\w'’-]+)*, (?:[A-Z]\.\s?)+.*\b(?:19|20)\d\d[a-z]?\b")


def is_reference(source: str) -> bool:
    return REFERENCE.match(source.strip()) is not None


def _worth_translating(text: str) -> bool:
    """Link texts and titles: only those with an ordinary lower-case word in them.

    Names (``GitHub``, ``Sebastian Schwindt``, ``HydroBayesCal``) and DOIs stay as they are.
    """
    return re.search(r"(?<![\w.:/])[a-z]{3,}(?![\w.:/])", text) is not None


def _title(term: str) -> str:
    """``courbe de tarage`` -> ``Courbe De Tarage``; ``hauteur d'eau`` -> ``Hauteur D'eau``."""
    return " ".join(word[:1].upper() + word[1:] for word in term.split(" "))


def prepare(source: str, glossary: Glossary, keep: list[str], *, bold_in_context: bool = True,
            translate_titles: bool = True, terms_masked: bool = False) -> Prepared:
    """Mask what is not translated and put the glossary terms into the sentence.

    ``terms_masked`` masks the glossary terms too, for a message in which the engine lost one that was put into its sentence: the term then comes through for certain, while the engine guesses the article in front of it (and for French the gender in the glossary corrects that guess).
    """
    prepared = Prepared(source=source, text=source, lang=glossary.lang)

    def sentinel(span: Span) -> str:
        prepared.spans.append(span)
        return SENTINEL.format(len(prepared.spans) - 1)

    def sub(text: str) -> Prepared:
        return prepare(text, glossary, keep, bold_in_context=True, translate_titles=False,
                       terms_masked=terms_masked)

    text = source
    if not bold_in_context:
        # before anything else is masked, so that a bold part comes out whole; not inside a literal
        literals = [m.span() for m in LITERAL.finditer(source)]

        def strong(m: re.Match) -> str:
            if any(a < m.end() and m.start() < b for a, b in literals):
                return m.group(0)
            return sentinel(Span(markup=True, before="**", after="**", sub=sub(m.group(2))))

        text = STRONG.sub(strong, text)
    text = LITERAL.sub(lambda m: sentinel(Span(m.group(0), markup=True)), text)
    text = ESCAPE.sub(lambda m: sentinel(Span(m.group(0))), text)

    def titled(m: re.Match) -> str:
        if translate_titles and _worth_translating(m.group(2)) and not SENTINEL_RE.search(m.group(2)):
            return sentinel(Span(markup=True, before=m.group(1), after=m.group(3), sub=sub(m.group(2))))
        return sentinel(Span(m.group(0), markup=True))

    text = TITLED_ROLE.sub(titled, text)
    text = ROLE.sub(lambda m: sentinel(Span(m.group(0), markup=True)), text)
    text = TITLED_LINK.sub(titled, text)
    text = INTERPRETED.sub(lambda m: sentinel(Span(m.group(0), markup=True)), text)
    text = SUBSTITUTION.sub(lambda m: sentinel(Span(m.group(0), markup=True)), text)
    text = FOOTNOTE.sub(lambda m: sentinel(Span(m.group(0), markup=True)), text)
    text = EMPHASIS.sub(lambda m: sentinel(Span(m.group(0), markup=True)), text)
    text = URL.sub(lambda m: sentinel(Span(m.group(0))), text)
    for name in keep:
        pattern = rf"(?<![\w@-]){re.escape(name)}(?![\w@-])"
        if terms_masked or " " in name:
            text = re.sub(pattern, lambda m: sentinel(Span(m.group(0))), text)
        elif re.search(pattern, text):
            # a single word goes as it is: one placeholder less, and the engine sees a subject; its spelling is restored afterwards ("AXqua") and it has to come back
            prepared.names.append(name)

    inserted: list[tuple[str, str]] = []   # (as sent to the engine, as it is to be shown)
    if glossary.pattern is not None:
        def inject(m: re.Match) -> str:
            found = glossary.find(m.group(0))
            if found is None:
                return m.group(0)
            term, number = found
            target = term.target[number]
            if term.gender:
                prepared.genders[target] = term.gender
            shown = target
            if m.group(0)[:1].isupper() and shown[:1].islower():
                shown = shown[:1].upper() + shown[1:]
            if terms_masked or _normal(target).lower() == _normal(m.group(0)).lower():
                # the same word in both languages, or a term the engine lost before: masked
                return sentinel(Span(shown))
            if INJECTION.get(glossary.lang) == "title":
                prepared.injected.append(target)
                inserted.append((_title(shown), shown))
            else:
                inserted.append((shown, shown))
            prepared.expect.append(target)
            return f"\ue000{len(inserted) - 1}\ue001"

        text = glossary.pattern.sub(inject, text)

    # Only glossary terms and connecting words: put together without the engine, which garbles a lone term ("Maillage" as a heading came back "Mèche", "Convergence En Maillage" as "Convergence en courrier").
    connectors = CONNECTORS.get(glossary.lang, {})
    outside = SENTINEL_RE.sub(" ", re.sub(r"\ue000\d+\ue001", " ", text))
    words = re.findall(r"[A-Za-z]+", outside)
    if (inserted or words) and all(word.lower() in connectors for word in words):
        prepared.words = connectors
        prepared.expect = []
        prepared.text = re.sub(r"\ue000(\d+)\ue001", lambda m: inserted[int(m.group(1))][1], text)
        return prepared
    text = re.sub(r"\ue000(\d+)\ue001", lambda m: inserted[int(m.group(1))][0], text)

    # only markup (a label in italics, a DOI) is set aside; a name kept in English at the start is the subject of the sentence ("aXqua builds ...") and goes to the engine with it
    def markup_only(found: str) -> bool:
        return all(prepared.spans[int(m.group(1))].markup for m in SENTINEL_RE.finditer(found))

    group = r"(?:@@\d+@@[\s,;:]*)+"
    head = re.match(rf"^\s*{group}", text)
    if head and needs_engine(text[head.end():]) and markup_only(head.group(0)):
        prepared.prefix, text = head.group(0), text[head.end():]
    tail = re.search(rf"(?<=[\s.,;:!?)]){group}\s*$", text)
    if tail and needs_engine(text[:tail.start()]) and markup_only(tail.group(0)):
        prepared.suffix, text = tail.group(0), text[:tail.start()]
    prepared.text = text
    return prepared


# ----------------------------------------------------------------------------------------------
# Putting a translation back together
# ----------------------------------------------------------------------------------------------

_ACCENTS = {"a": "aàâä", "c": "cç", "e": "eéèêë", "i": "iîï", "o": "oôö", "u": "uùûü", "y": "yÿ"}


def _loose(term: str) -> str:
    """Pattern for a term whatever the engine did to its case and accents."""
    out = []
    for ch in term:
        base = unicodedata.normalize("NFD", ch.lower())[0]
        if ch in " -":
            out.append(r"[\s-]+")
        elif ch in "'’":
            out.append(r"\s?['’]\s?")
        elif base in _ACCENTS:
            letters = _ACCENTS[base] + _ACCENTS[base].upper()
            out.append(f"[{letters}]")
        else:
            out.append(re.escape(ch))
    return "".join(out)


def _untitle(text: str, injected: list[str]) -> str:
    """Put the terms that were sent in title case back into their own case."""
    for term in sorted(set(injected), key=len, reverse=True):
        def restore(m: re.Match, term: str = term) -> str:
            start = re.search(r"(?:^|[.!?]\s)[\s*«\"'(]*$", m.string[:m.start()]) is not None
            return term[:1].upper() + term[1:] if start else term
        text = re.sub(rf"(?<!\w){_loose(_title(term))}(?!\w)", restore, text, flags=re.IGNORECASE)
    return text


# The words in front of a French noun that show its gender, masculine -> feminine, for a noun that starts with a consonant. Before a vowel or a mute h the elided forms are the same for both.
_FR_GENDERED = {"un": "une", "le": "la", "du": "de la", "au": "à la", "ce": "cette", "son": "sa",
                "mon": "ma", "aucun": "aucune", "quel": "quelle", "nouveau": "nouvelle",
                "premier": "première", "dernier": "dernière", "seul": "seule", "petit": "petite"}
_FR_MASCULINE = {fem: masc for masc, fem in _FR_GENDERED.items()}
_FR_BEFORE_VOWEL = {"le": "l'", "la": "l'", "du": "de l'", "de la": "de l'", "au": "à l'",
                    "à la": "à l'", "ce": "cet", "sa": "son", "ma": "mon"}


def _french_articles(text: str, genders: dict[str, str]) -> str:
    """Make the article in front of a glossary term agree with its gender.

    Sent in title case, a term is copied like a name, and the engine sometimes picks the article of the English word's usual translation ("un tâche").
    """
    for term, gender in sorted(genders.items(), key=lambda item: len(item[0]), reverse=True):
        # a mute h elides like a vowel (l'hydrogramme), an aspirated one does not (la hauteur)
        vowel = re.match(r"[aeiouyàâéèêëîïôûü]|hy", term, re.IGNORECASE) is not None
        words = sorted({*_FR_GENDERED, *_FR_MASCULINE, *_FR_BEFORE_VOWEL}, key=len, reverse=True)
        pattern = (r"(?<!\w)(" + "|".join(re.escape(w) for w in words) + r")(\s+)(?=" +
                   re.escape(term) + r"(?!\w))")

        def agree(m: re.Match) -> str:
            word = m.group(1)
            low = word.lower()
            if vowel and low in _FR_BEFORE_VOWEL:
                new = _FR_BEFORE_VOWEL[low]
                return (new[:1].upper() + new[1:] if word[:1].isupper() else new)
            if gender == "f":
                new = _FR_GENDERED.get(low, low)
            else:
                new = _FR_MASCULINE.get(low, low)
            new = new[:1].upper() + new[1:] if word[:1].isupper() else new
            return new + m.group(2)

        text = re.sub(pattern, agree, text, flags=re.IGNORECASE)
        # "l'" + space is no French
        text = re.sub(r"(?<![\w])([Ll]'|[Dd]e l'|à l')\s+(?=" + re.escape(term) + ")", r"\1", text)
    return text


# In front of a feminine German noun in the singular, these forms of the article belong to a masculine or neuter noun and are wrong in every case ("des Steuerdatei", "einem Rauheitszone").
_DE_FEMININE = {"des": "der", "dem": "der", "eines": "einer", "einem": "einer", "keines": "keiner",
                "keinem": "keiner", "jedes": "jeder", "jedem": "jeder", "dieses": "dieser",
                "diesem": "dieser", "seines": "seiner", "seinem": "seiner", "ihres": "ihrer",
                "ihrem": "ihrer"}


def _german_articles(text: str, genders: dict[str, str]) -> str:
    """Repair the article in front of a feminine glossary term that the engine declined as masculine."""
    for term, gender in genders.items():
        if gender != "f":
            continue
        pattern = (r"(?<!\w)(" + "|".join(sorted(_DE_FEMININE, key=len, reverse=True)) + r")(\s+)(?=" +
                   re.escape(term) + r"(?!\w))")
        text = re.sub(pattern, lambda m: (_DE_FEMININE[m.group(1).lower()].capitalize()
                                          if m.group(1)[:1].isupper()
                                          else _DE_FEMININE[m.group(1).lower()]) + m.group(2),
                      text, flags=re.IGNORECASE)
    return text


def _french_spacing(text: str) -> str:
    """A non-breaking space before a colon, semicolon, question mark and exclamation mark."""
    return re.sub(r"(?<=[\w»)])\s?([:;!?])(?=\s|$)", "\u00a0\\1", text)


def _terms_came_back(text: str, prepared: Prepared) -> bool:
    """Every term put into the sentence is in the answer (a German noun with any case ending)."""
    for term in prepared.expect:
        if prepared.lang == "fr":
            found = re.search(rf"(?<!\w){_loose(term)}(?!\w)", text, re.IGNORECASE)
        else:
            found = re.search(re.escape(term.split()[-1]), text, re.IGNORECASE)
        if found is None:
            return False
    return True


def _fix_strong(text: str, expected: int) -> str | None:
    """Tidy the bold markers the engine kept; None when their number changed."""
    positions = [m.start() for m in re.finditer(r"\*\*", text)]
    if len(positions) != expected or len(positions) % 2:
        return None
    out, last = [], 0
    for opening, closing in zip(positions[::2], positions[1::2]):
        inner = text[opening + 2:closing].strip()
        if not inner or "*" in inner:
            return None
        head = text[last:opening]
        if head and not re.search(r"[\s(\[{'\"«/-]$", head):
            head += " "
        out.append(head + "**" + inner + "**")
        last = closing + 2
        tail = text[last:last + 1]
        if tail and re.match(r"\w", tail):
            out.append("\\ ")
    out.append(text[last:])
    return "".join(out)


def apply_termfix(source: str, text: str, rules: list[TermFix]) -> str:
    for rule in rules:
        if rule.term_re.search(source):
            text = rule.pattern.sub(rule.replacement, text)
    return text


def assemble(prepared: Prepared, results: dict[str, str], rules: list[TermFix], *,
             strict: bool = True) -> str | None:
    """The translation of a message, or None when the engine lost or doubled a masked part.

    *strict* also requires every glossary term put into the sentence to come back; the last resort of :func:`translate_language` relaxes that, since a term the engine reworded in a long paragraph is a smaller loss than the whole paragraph in English.
    """
    if prepared.words is not None:
        text = re.sub(r"[A-Za-z]+", lambda m: prepared.words.get(m.group(0).lower(), m.group(0)),
                      prepared.text)
    elif needs_engine(prepared.text):
        if prepared.text not in results:
            return None
        text = results[prepared.text]
    else:
        text = prepared.text
    if prepared.words is None:
        text = _untitle(text, prepared.injected)
    for name in prepared.names:
        word = rf"(?<![\w@-]){re.escape(name)}(?![\w@-])"
        text = re.sub(word, name, text, flags=re.IGNORECASE)
        if re.search(word, text) is None:
            return None
    if prepared.lang == "fr":
        text = _french_spacing(_french_articles(text, prepared.genders))
    elif prepared.lang == "de":
        text = _german_articles(text, prepared.genders)
    if strict and not _terms_came_back(text, prepared):
        return None
    # a capital where the English has one: headings and list items come back in lower case
    english = re.search(r"[^\W\d_]", SENTINEL_RE.sub(" ", prepared.text))
    first = re.match(r"^([\s*«\"'(]*)([^\W\d_])", text)
    if english and english.group(0).isupper() and first and first.group(2).islower():
        text = first.group(1) + first.group(2).upper() + text[first.end():]
    text = prepared.prefix + text.strip() + (" " if prepared.suffix and not prepared.suffix[:1].isspace()
                                             else "") + prepared.suffix

    found = Counter(int(m.group(1)) for m in SENTINEL_RE.finditer(text))
    if found != Counter(range(len(prepared.spans))):
        return None
    # the remains of a sentinel that lost its number ("im Zustand @@")
    if SENTINEL_RE.sub("", text).count("@") != SENTINEL_RE.sub("", prepared.text).count("@"):
        return None
    strong = len(re.findall(r"\*\*", SENTINEL_RE.sub("", prepared.text)))
    if strong:
        fixed = _fix_strong(text, strong)
        if fixed is None:
            return None
        text = fixed

    rendered = []
    for span in prepared.spans:
        if span.sub is None:
            rendered.append(span.text)
            continue
        inner = assemble(span.sub, results, rules, strict=strict)
        if inner is None:
            return None
        rendered.append(span.before + inner.strip() + span.after)

    def escape(index: int) -> bool:
        return prepared.spans[index].text.startswith("\\")

    def replace(m: re.Match) -> str:
        index = int(m.group(1))
        value = rendered[index]
        span = prepared.spans[index]
        # two masked parts the engine put next to each other, which the source had apart
        following = SENTINEL_RE.match(m.string, m.end())
        if following is not None:
            other = int(following.group(1))
            if (span.markup or prepared.spans[other].markup) and not escape(index) and not escape(other):
                value += " "
        if not span.markup:
            return value
        # inline markup is recognized only after a space or punctuation and before one
        before = m.string[m.start() - 1:m.start()]
        after = m.string[m.end():m.end() + 1]
        if before and re.match(r"\w", before):
            value = " " + value
        if after and re.match(r"\w", after):
            value += "\\ "
        return value

    # spaces are tidied before the masked parts go back in, so that a literal keeps its own
    text = re.sub(r"[ \t]{2,}", " ", text).strip()
    text = SENTINEL_RE.sub(replace, text)
    # the term repairs see the whole sentence, the parts that were translated on their own included, but never the inside of a literal
    parts = LITERAL.split(text)
    literals = LITERAL.findall(text)
    out = [apply_termfix(prepared.source, part, rules) for part in parts]
    return "".join(piece for pair in zip(out, [*literals, ""]) for piece in pair)


_GERMAN_WORD = re.compile(r"(?<![\w@`*/.-])([A-Za-zÄÖÜäöüß]{3,})(?![\w@`*/-])")


def german_nouns(entries) -> set[str]:
    """Words that the German translations write with a capital inside a sentence: the nouns."""
    upper, lower = Counter(), Counter()
    for entry in entries:
        for sentence in re.split(r"(?<=[.!?:])\s+", SENTINEL_RE.sub(" ", entry.msgstr)):
            for m in list(_GERMAN_WORD.finditer(sentence))[1:]:
                word = m.group(1)
                (upper if word[:1].isupper() else lower)[word.lower()] += 1
    return {word for word, n in upper.items() if n >= 2 and n > 3 * lower[word]}


#: What a translation carries over from the English unchanged: inline literals, roles, links and GUI labels (single asterisks)
_PROTECTED = re.compile(r"(``.*?``|:\w+:`[^`]*`|`[^`]*`_{0,2}|(?<!\*)\*[^*\s][^*]*(?<!\s)\*(?!\*))")


def recapitalize(text: str, nouns: set[str]) -> str:
    """Capitalize the nouns of a German sentence that came back all in lower case.

    Argos writes a German sentence entirely in lower case now and then ("bei dem eintrag um einen tippfehler"). A sentence of eight words or more without a single capital is taken for one.
    """
    # Literals, roles, links and GUI labels are not German text: they decide nothing and are never changed. They are set aside before the text is cut into sentences, because they may hold a full stop or a colon themselves ("Celik et al. 2008", ``a. b``).
    kept: list[str] = []

    def aside(match: re.Match) -> str:
        kept.append(match.group(0))
        return f"\x00{len(kept) - 1}\x01"

    def fix(sentence: str) -> str:
        words = _GERMAN_WORD.findall(sentence)
        if len(words) < 8 or any(w[:1].isupper() for w in words[1:]):
            return sentence
        return _GERMAN_WORD.sub(lambda m: m.group(1).capitalize() if m.group(1).lower() in nouns
                                else m.group(1), sentence)

    masked = _PROTECTED.sub(aside, text)
    done = "".join(fix(part) for part in re.split(r"((?<=[.!?:])\s+)", masked))
    return re.sub("\x00(\\d+)\x01", lambda m: kept[int(m.group(1))], done)


def _sentences(text: str) -> list[str]:
    return [part for part in re.split(r"(?<=[.!?:;])\s+(?=[A-Z@(])", text) if part]


# ----------------------------------------------------------------------------------------------
# Engines
# ----------------------------------------------------------------------------------------------

class Engine:
    """A machine translation backend. ``translate`` returns the raw output for one piece."""

    name = "base"

    def translate(self, text: str) -> str:
        raise NotImplementedError


class ArgosEngine(Engine):
    """Argos Translate: offline, no key. The model is downloaded on first use."""

    name = "argos"

    def __init__(self, lang: str):
        try:
            import argostranslate.package as package
            import argostranslate.translate as translate
        except ImportError:
            sys.exit("Argos Translate is not installed: mamba env create -f docs/i18n/environment.yml")
        self._translate = translate
        self.lang = lang
        installed = translate.get_installed_languages()
        source = next((lg for lg in installed if lg.code == "en"), None)
        target = next((lg for lg in installed if lg.code == lang), None)
        if not (source and target and source.get_translation(target)):
            print(f"[argos] downloading the English-{LANGUAGE_NAMES[lang]} model (once)", flush=True)
            package.update_package_index()
            match = next((p for p in package.get_available_packages()
                          if p.from_code == "en" and p.to_code == lang), None)
            if match is None:
                sys.exit(f"Argos Translate offers no English-{LANGUAGE_NAMES[lang]} model")
            package.install_from_path(match.download())

    def translate(self, text: str) -> str:
        return self._translate.translate(text, "en", self.lang)


class DeepLEngine(Engine):
    """DeepL (``pip install deepl``, ``DEEPL_API_KEY``). Not tried yet: no key on this computer.

    The masked parts go as XML tags that DeepL keeps where they belong in the sentence.
    """

    name = "deepl"

    def __init__(self, lang: str, client=None):
        if client is None:
            try:
                import deepl
            except ImportError:
                sys.exit("The DeepL client is not installed: pip install deepl")
            key = os.environ.get("DEEPL_API_KEY")
            if not key:
                sys.exit("DEEPL_API_KEY is not set")
            client = deepl.Translator(key)
        self.client = client
        self.target = {"de": "DE", "fr": "FR"}[lang]

    @staticmethod
    def to_xml(text: str) -> str:
        escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return SENTINEL_RE.sub(lambda m: f'<x i="{m.group(1)}"/>', escaped)

    @staticmethod
    def from_xml(text: str) -> str:
        text = re.sub(r'<x i="(\d+)"\s*/>', lambda m: SENTINEL.format(m.group(1)), text)
        return text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")

    def translate(self, text: str) -> str:
        result = self.client.translate_text(self.to_xml(text), source_lang="EN",
                                            target_lang=self.target, tag_handling="xml",
                                            preserve_formatting=True)
        return self.from_xml(result.text)


ENGINES = {"argos": ArgosEngine, "deepl": DeepLEngine}

_worker_engine: Engine | None = None


def _worker_init(engine: str, lang: str) -> None:
    global _worker_engine
    # one or two threads per worker: Argos otherwise takes every core in each process
    os.environ.setdefault("ARGOS_INTRA_THREADS", "2")
    logging.getLogger("stanza").addFilter(lambda record: "expects mwt" not in record.getMessage())
    _worker_engine = ENGINES[engine](lang)


def _worker_translate(text: str) -> tuple[str, str]:
    assert _worker_engine is not None
    return text, _worker_engine.translate(text)


def default_jobs() -> int:
    return max(1, min(4, (os.cpu_count() or 2) // 4))


def run_engine(pieces: list[str], engine: str, lang: str, jobs: int) -> dict[str, str]:
    """Translate the pieces, in a pool of workers that each load the model once."""
    results: dict[str, str] = {}
    if not pieces:
        return results
    started = time.time()
    if jobs <= 1:
        _worker_init(engine, lang)
        iterator = map(_worker_translate, pieces)
        pool = None
    else:
        pool = multiprocessing.get_context("spawn").Pool(jobs, _worker_init, (engine, lang))
        iterator = pool.imap_unordered(_worker_translate, pieces, chunksize=4)
    try:
        for done, (text, out) in enumerate(iterator, 1):
            results[text] = out
            if done % 50 == 0 or done == len(pieces):
                rate = done / max(time.time() - started, 1e-6)
                print(f"   [{lang}] {done}/{len(pieces)} pieces, {rate:.1f}/s", flush=True)
    finally:
        if pool is not None:
            pool.close()
            pool.join()
    return results


# ----------------------------------------------------------------------------------------------
# Driver
# ----------------------------------------------------------------------------------------------

def entry_hash(prepared: Prepared, rules: list[TermFix]) -> str:
    """Identifies what was sent to the engine for a message and how its answer was repaired."""
    applicable = [rule.line for rule in rules if rule.term_re.search(prepared.source)]
    payload = json.dumps([PREPARATION_VERSION, prepared.text, prepared.pieces(), prepared.injected,
                          sorted(prepared.genders.items()), applicable], ensure_ascii=False)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def templates(pot_dir: Path) -> dict[str, Path]:
    return {path.relative_to(pot_dir).with_suffix("").as_posix(): path
            for path in sorted(pot_dir.rglob("*.pot"))}


def translate_language(lang: str, pot_dir: Path, *, engine: str, locale: Path = LOCALE,
                       jobs: int = 1, retranslate: bool = False, docstrings: bool = False,
                       folder: Path = I18N, run=run_engine) -> dict[str, int]:
    """Update the catalogs of one language and translate what is missing. Returns counts."""
    glossary = Glossary.load(lang, folder)
    rules = load_termfix(lang, folder)
    keep = load_keep(folder)
    out_dir = locale / lang / "LC_MESSAGES"
    pages = templates(pot_dir)

    # everything translated so far, from every page: a message moved to another page keeps its translation
    memory: dict[tuple[str | None, str], Entry] = {}
    for path in sorted(out_dir.rglob("*.po")) if out_dir.exists() else []:
        for entry in read_catalog(path):
            if entry.msgstr and not entry.fuzzy:
                previous = memory.get(entry.key)
                if previous is None or (previous.machine and not entry.machine):
                    memory[entry.key] = entry

    counts = Counter()
    plans: dict[str, list[tuple[Entry, Prepared | None, str]]] = {}
    wanted: set[str] = set()
    for page, path in pages.items():
        plan = []
        for template in read_catalog(path):
            old = memory.get(template.key)
            if template.from_docstring and not docstrings:
                # not translated in this run, but what the catalog has is kept as it is: a reviewed entry would be lost otherwise
                if old is not None:
                    counts["kept"] += 1
                    plan.append((template, None, old.msgstr))
                continue
            prepared = prepare(template.msgid, glossary, keep)
            digest = entry_hash(prepared, rules)
            if old is not None and old.machine is None:
                counts["reviewed"] += 1
                plan.append((template, None, old.msgstr))
                continue
            if old is not None and (engine == "none" or (old.machine[1] == digest and not retranslate)):
                counts["kept"] += 1
                plan.append((template, None, old.msgstr))
                continue
            if not prepared.pieces() and prepared.words is None:
                counts["nothing to translate"] += 1
                continue
            if is_reference(template.msgid):
                counts["references"] += 1
                continue
            if engine == "none":
                counts["missing"] += 1
                continue
            plan.append((template, prepared, digest))
            wanted.update(prepared.pieces())
        plans[page] = plan

    results = run(sorted(wanted), engine, lang, jobs) if wanted else {}

    # what failed in its sentence goes again with the bold parts translated on their own, then with the glossary terms masked, and then sentence by sentence
    retry: dict[str, list[Prepared]] = {}
    for plan in plans.values():
        for template, prepared, _ in plan:
            if prepared is not None and assemble(prepared, results, rules) is None:
                retry[template.msgid] = [
                    prepare(template.msgid, glossary, keep, bold_in_context=False),
                    prepare(template.msgid, glossary, keep, bold_in_context=False, terms_masked=True)]
    if retry:
        attempts = [p for pair in retry.values() for p in pair]
        more = {piece for p in attempts for piece in p.pieces()} - results.keys()
        sentences = {s for p in attempts if p.words is None for s in _sentences(p.text)}
        results.update(run(sorted(more | (sentences - results.keys())), engine, lang, jobs))

    failed: list[str] = []
    kept_after_failure: list[str] = []          # failed again, the earlier translation kept
    nouns = german_nouns(memory.values()) if lang == "de" else set()
    out_dir.mkdir(parents=True, exist_ok=True)
    written = set()
    for page, plan in plans.items():
        entries = []
        for template, prepared, value in plan:
            entry = Entry(template.msgid, msgctxt=template.msgctxt)
            if prepared is None:
                old = memory[template.key]
                entry.msgstr, entry.comments = value, list(old.comments)
            else:
                text = assemble(prepared, results, rules)
                for again in retry.get(template.msgid, []) if text is None else []:
                    text = assemble(again, results, rules)
                    if text is None and again.words is None:
                        parts = _sentences(again.text)
                        if all(part in results for part in parts):
                            joined = {again.text: " ".join(results[part] for part in parts)}
                            text = assemble(again, {**results, **joined}, rules)
                    if text is not None:
                        break
                for again in ([prepared, *retry.get(template.msgid, [])] if text is None else []):
                    text = assemble(again, results, rules, strict=False)
                    if text is not None:
                        counts["a term reworded"] += 1
                        break
                if text is None and template.key in memory:
                    # the new translation failed: the earlier one stays, with its old hash, so that the next run tries again
                    old = memory[template.key]
                    entry.msgstr, entry.comments = old.msgstr, list(old.comments)
                    counts["failed, earlier translation kept"] += 1
                    kept_after_failure.append(template.msgid)
                    entries.append(entry)
                    continue
                if text is None:
                    failed.append(template.msgid)
                    counts["failed"] += 1
                    continue
                entry.msgstr = recapitalize(text, nouns) if nouns else text
                entry.comments = [f"machine translation: {engine} {value}"]
                counts["translated"] += 1
            entries.append(entry)
        target = out_dir / f"{page}.po"
        target.parent.mkdir(parents=True, exist_ok=True)
        content = render_catalog(entries, lang, page)
        if not target.exists() or target.read_text(encoding="utf-8") != content:
            target.write_text(content, encoding="utf-8")
        written.add(target)

    for stale in sorted(out_dir.rglob("*.po")):
        if stale not in written:
            stale.unlink()
            counts["pages removed"] += 1
    for stale in sorted(out_dir.rglob("*.mo")):
        if stale.with_suffix(".po") not in written:
            stale.unlink()

    report = pot_dir.parent / f"failed-{lang}.txt"
    if failed or kept_after_failure:
        sections = []
        if failed:
            sections.append("# left in English\n\n" + "\n\n".join(failed))
        if kept_after_failure:
            sections.append("# translated anew without success; the earlier translation is kept "
                            "and tried again in the next run\n\n" + "\n\n".join(kept_after_failure))
        report.write_text("\n\n".join(sections) + "\n", encoding="utf-8")
    elif report.exists():
        report.unlink()
    return dict(counts)


def check(pot_dir: Path, folder: Path = I18N, docstrings: bool = False) -> int:
    """Self-test: masking and reassembly give back every message unchanged."""
    keep = load_keep(folder)
    bad = 0
    total = 0
    empty = Glossary("en")
    for path in templates(pot_dir).values():
        for template in read_catalog(path):
            if template.from_docstring and not docstrings:
                continue
            for bold in (True, False):
                prepared = prepare(template.msgid, empty, keep, bold_in_context=bold)
                identity = {piece: piece for piece in prepared.pieces()}
                total += 1
                if assemble(prepared, identity, []) != template.msgid.strip():
                    bad += 1
                    print(f"does not round-trip: {template.msgid[:120]!r}")
    print(f"{total - bad}/{total} messages round-trip")
    return 1 if bad else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--pot", type=Path, required=True,
                        help="folder of the templates (sphinx-build -b gettext)")
    parser.add_argument("--engine", choices=["none", *ENGINES], default="none",
                        help="'none' updates the catalogs without translating anything")
    parser.add_argument("--langs", nargs="+", default=list(LANGS), choices=LANGS)
    parser.add_argument("--jobs", type=int, default=default_jobs())
    parser.add_argument("--retranslate", action="store_true",
                        help="translate every machine entry again (reviewed entries are kept)")
    parser.add_argument("--docstrings", action="store_true",
                        help="also translate the reference of the Python code")
    parser.add_argument("--check", action="store_true", help="masking self-test, no engine")
    args = parser.parse_args()
    if not args.pot.is_dir():
        sys.exit(f"{args.pot} is not a folder of templates")
    if args.check:
        return check(args.pot, docstrings=args.docstrings)
    for lang in args.langs:
        started = time.time()
        counts = translate_language(lang, args.pot, engine=args.engine, jobs=args.jobs,
                                    retranslate=args.retranslate, docstrings=args.docstrings)
        summary = ", ".join(f"{value} {key}" for key, value in sorted(counts.items()))
        print(f"[{lang}] {summary or 'nothing to do'} ({time.time() - started:.0f} s)", flush=True)
        if counts.get("failed") or counts.get("failed, earlier translation kept"):
            print(f"[{lang}] failed translations: see {args.pot.parent / f'failed-{lang}.txt'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
