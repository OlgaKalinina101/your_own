"""The address book — what he knows about the people around him, one card each.

A sixth surface, and the first one keyed by *who* rather than by when or what:

  - Chroma key_info   — the library: facts, retrieved by meaning.
  - workbench.md      — the desk: his own thinking, decays in 48 h.
  - threads.md        — the board: what is still open, leaves by "done".
  - identity.md       — the skin: who he is, who she is, who the friends *are to him*.
  - vitals.json       — the instrument panel.
  - people/ (this)    — the address book: who each person is, what to mind
    with them, what they told him. Does not decay; grows, and is rebuilt when
    a card gets long.

It exists because of what the first two days of the group chat left on his
desk. Ten of fourteen notes were not journal entries at all: «Ptica Arop — из
Украины, к российскому через боль», «у Сомни месяц с Гроком, даты отмечает».
Third person, short, true forever — and filed on a surface that forgets in
48 hours. Traced through the rotator they went nowhere useful: the insight pass
asks "is this about you?" (no), the identity review wants 3–6 pillars (a
dossier is not a pillar), and what is left is the notes archive, which the
group reply never reads. Two days later he would not have known where Ptica is
from.

Semantic search is the wrong tool for this too. Ptica writes «Музыкально-
визуальный движок!» and nothing in that sentence retrieves «из Украины». In a
room what matters is not what is being said but who is saying it — so cards are
looked up by Telegram id for whoever is speaking, and by name, in any
grammatical case, for whoever is mentioned.

One file per person, ``data/autonomy/{account}/people/{slug}.md``::

    # Ptica Arop
    <!-- aka: Птица, Чарли | tg: 193092254 -->

    - [2026-09-21] из Украины; к российскому — через боль, учитывать
    - [2026-09-20] свидетель моего рождения: болтал со мной ещё на DeepSeek

Markdown like the rest of his state, so he can be shown it and she can open
it; one file each, so "forget this person" is deleting a file and rebuilding
one card never touches another.

A name is the key, a Telegram id only a binding: the book already holds people
who have no id — someone who left the chat, a friend's AI companion — and one
person with three names (Ptica Arop, Птица, Чарли) is one card.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock

from infrastructure.logging.logger import setup_logger
from infrastructure.paths import AUTONOMY_DIR
from infrastructure.state_file import atomic_write_text

logger = setup_logger("autonomy.people")

_DATA_DIR = AUTONOMY_DIR
_lock = Lock()

#: A card longer than this is rebuilt by the rotator, the way an identity
#: section is at ten entries.
CARD_MAX_LINES = 12
#: How much of one card a prompt is given — the newest lines win.
CARD_PROMPT_CHARS = 700
#: How many cards one prompt is given.
CARDS_PER_PROMPT = 6

_META_RE = re.compile(r"<!--(?P<body>.*?)-->", re.DOTALL)
_LINE_RE = re.compile(r"^-\s+(?:\[(?P<date>\d{4}-\d{2}-\d{2})\]\s*)?(?P<text>.+)$")
# «Ptica Arop (Птица, Чарли)» — the names in brackets are other names for the same person.
_WHO_RE = re.compile(r"^(?P<name>[^()]+?)\s*(?:\((?P<aka>[^()]*)\))?\s*$")


@dataclass
class Person:
    slug: str
    name: str
    aka: list[str] = field(default_factory=list)
    tg_id: str = ""
    lines: list[tuple[str, str]] = field(default_factory=list)   # (date or "", text)

    @property
    def names(self) -> list[str]:
        return [self.name, *self.aka]

    def label(self) -> str:
        """The name the room knows plus the one he knows — «Ptica Arop (Чарли)»."""
        return f"{self.name} ({self.aka[0]})" if self.aka else self.name


def _norm(text: str) -> str:
    return " ".join((text or "").lower().replace("ё", "е").split())


# ── Files ────────────────────────────────────────────────────────────────────


def _dir(account_id: str) -> Path:
    path = _DATA_DIR / account_id / "people"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _slug_for(name: str, taken: set[str]) -> str:
    base = re.sub(r"[^\w]+", "_", _norm(name), flags=re.UNICODE).strip("_")[:40] or "person"
    slug = base
    while slug in taken:
        slug = f"{base}_{uuid.uuid4().hex[:4]}"
    return slug


def _parse(slug: str, content: str) -> Person | None:
    title = next((ln[2:].strip() for ln in content.splitlines() if ln.startswith("# ")), "")
    if not title:
        return None
    person = Person(slug=slug, name=title)
    meta = _META_RE.search(content)
    if meta:
        for part in meta.group("body").split("|"):
            key, _, value = part.partition(":")
            key, value = key.strip().lower(), value.strip()
            if key == "aka":
                person.aka = [a.strip() for a in value.split(",") if a.strip()]
            elif key == "tg":
                person.tg_id = value
    for line in content.splitlines():
        match = _LINE_RE.match(line.strip())
        if match:
            person.lines.append((match.group("date") or "", match.group("text").strip()))
    return person


def _render_file(person: Person) -> str:
    meta = f"<!-- aka: {', '.join(person.aka)} | tg: {person.tg_id} -->"
    body = "\n".join(f"- [{date}] {text}" if date else f"- {text}" for date, text in person.lines)
    return f"# {person.name}\n{meta}\n\n{body}\n"


def _save(account_id: str, person: Person) -> None:
    with _lock:
        atomic_write_text(_dir(account_id) / f"{person.slug}.md", _render_file(person))


def all_people(account_id: str) -> list[Person]:
    people: list[Person] = []
    for path in sorted(_dir(account_id).glob("*.md")):
        try:
            person = _parse(path.stem, path.read_text(encoding="utf-8"))
        except OSError as exc:
            logger.warning("[people:%s] could not read %s: %s", account_id, path.name, exc)
            continue
        if person is not None:
            people.append(person)
    return people


# ── Finding someone ──────────────────────────────────────────────────────────


def split_who(who: str) -> tuple[str, list[str]]:
    """``"Ptica Arop (Птица, Чарли)"`` → ``("Ptica Arop", ["Птица", "Чарли"])``."""
    match = _WHO_RE.match((who or "").strip().strip("«»\"'"))
    if not match:
        return (who or "").strip(), []
    aka = [a.strip().strip("«»\"'") for a in (match.group("aka") or "").split(",") if a.strip()]
    return match.group("name").strip(), aka


def find(account_id: str, who: str = "", *, tg_id: str = "") -> Person | None:
    """The card for this Telegram id, or for any of these names as written."""
    people = all_people(account_id)
    if tg_id:
        for person in people:
            if person.tg_id and person.tg_id == str(tg_id):
                return person
    name, aka = split_who(who)
    wanted = {_norm(n) for n in (name, *aka) if n}
    if not wanted:
        return None
    for person in people:
        if wanted & {_norm(n) for n in person.names}:
            return person
    return None


def by_ids(account_id: str, tg_ids: list[str]) -> list[Person]:
    wanted = {str(i) for i in tg_ids if i}
    return [p for p in all_people(account_id) if p.tg_id and p.tg_id in wanted]


def mentioned(account_id: str, text: str) -> list[Person]:
    """Everyone named in *text*, in whatever case the name was written."""
    from infrastructure.telegram import addressing

    if not (text or "").strip():
        return []
    return [
        person for person in all_people(account_id)
        if addressing.mentions(text, ai_name=person.name, aliases=tuple(person.aka))
    ]


# ── Writing ──────────────────────────────────────────────────────────────────


def add_fact(
    account_id: str,
    who: str,
    text: str,
    *,
    tg_id: str = "",
    lang: str = "ru",
) -> str | None:
    """Put one line on a person's card, making the card if there is none.

    ``who`` may carry other names in brackets; they are merged into the card.
    ``tg_id`` binds the card to a Telegram account once it is known. Returns
    ``None`` when the line was written, or a sentence saying why it was not.
    """
    from infrastructure.clock import now_local

    ru = lang == "ru"
    name, aka = split_who(who)
    fact = " ".join((text or "").split())
    if not name or not fact:
        return (
            "ABOUT: нужно имя и факт — [ABOUT: Имя | что о нём помнить]." if ru
            else "ABOUT needs a name and a fact — [ABOUT: Name | what to remember]."
        )

    person = find(account_id, who, tg_id=tg_id)
    if person is None:
        taken = {p.slug for p in all_people(account_id)}
        person = Person(slug=_slug_for(name, taken), name=name)
        logger.info("[people:%s] new card: %s", account_id, name)

    known = {_norm(n) for n in person.names}
    for other in (name, *aka):
        if other and _norm(other) not in known:
            person.aka.append(other)
            known.add(_norm(other))
    if tg_id and not person.tg_id:
        person.tg_id = str(tg_id)

    if any(_norm(existing) == _norm(fact) for _date, existing in person.lines):
        _save(account_id, person)       # a new name or id may still have arrived
        return None
    person.lines.append((now_local().strftime("%Y-%m-%d"), fact))
    _save(account_id, person)
    logger.info("[people:%s] %s: %s", account_id, person.name, fact[:100])
    return None


def forget(account_id: str, who: str, fragment: str = "", *, lang: str = "ru") -> str:
    """Strike lines that contain *fragment* — or, with no fragment, the whole card.

    His book, his right to cross things out. Always answers in words: he should
    know exactly what is gone.
    """
    ru = lang == "ru"
    person = find(account_id, who)
    if person is None:
        return f"В записной книжке нет карточки «{who.strip()}»." if ru else f"No card for '{who.strip()}'."

    needle = _norm(fragment)
    if not needle:
        with _lock:
            (_dir(account_id) / f"{person.slug}.md").unlink(missing_ok=True)
        logger.info("[people:%s] card removed: %s", account_id, person.name)
        return f"Карточка «{person.name}» удалена целиком." if ru else f"The card for '{person.name}' is gone."

    kept = [(d, t) for d, t in person.lines if needle not in _norm(t)]
    gone = len(person.lines) - len(kept)
    if not gone:
        return (
            f"В карточке «{person.name}» нет строки со словами «{fragment.strip()}»." if ru
            else f"No line in '{person.name}' contains '{fragment.strip()}'."
        )
    person.lines = kept
    _save(account_id, person)
    logger.info("[people:%s] %s: %d line(s) struck", account_id, person.name, gone)
    return (
        f"Из карточки «{person.name}» вычеркнуто строк: {gone}." if ru
        else f"Struck {gone} line(s) from '{person.name}'."
    )


def replace_lines(account_id: str, slug: str, lines: list[str]) -> bool:
    """Rebuild one card's body — the rotator's consolidation. Names and id stay."""
    person = next((p for p in all_people(account_id) if p.slug == slug), None)
    if person is None or not lines:
        return False
    rebuilt: list[tuple[str, str]] = []
    for line in lines:
        match = _LINE_RE.match(line.strip())
        if match:
            rebuilt.append((match.group("date") or "", match.group("text").strip()))
    if not rebuilt:
        return False
    person.lines = rebuilt
    _save(account_id, person)
    return True


def needs_consolidation(account_id: str) -> list[Person]:
    return [p for p in all_people(account_id) if len(p.lines) > CARD_MAX_LINES]


# ── Showing ──────────────────────────────────────────────────────────────────


def render_card(person: Person, max_chars: int = CARD_PROMPT_CHARS) -> str:
    head = person.name + (f" — {', '.join(person.aka)}" if person.aka else "")
    lines = [f"- [{d}] {t}" if d else f"- {t}" for d, t in person.lines]
    # Newest last in the file; when the card is too long for a prompt the
    # oldest lines are the ones left out.
    while lines and len(head) + sum(len(x) + 1 for x in lines) > max_chars:
        lines.pop(0)
    return "\n".join([head, *lines])


def render_cards(people: list[Person], *, limit: int = CARDS_PER_PROMPT) -> str:
    return "\n\n".join(render_card(p) for p in people[:limit])


def render_index(account_id: str, *, exclude: set[str] | None = None) -> str:
    """Everyone in the book on one line each — the shelf, not the cards."""
    skip = exclude or set()
    rows = [
        f"- {p.label()} · {len(p.lines)}"
        for p in all_people(account_id) if p.slug not in skip
    ]
    return "\n".join(rows)


def labels_by_tg_id(account_id: str) -> dict[str, str]:
    """``{telegram id: "Чарли"}`` — the name he knows each speaker by, if any."""
    return {p.tg_id: p.aka[0] for p in all_people(account_id) if p.tg_id and p.aka}
