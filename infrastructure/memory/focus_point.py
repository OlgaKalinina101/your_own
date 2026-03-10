"""
FocusPointPipeline — extracts, normalises and expands keywords from text.

Language selection determines:
  - RU: pymorphy3 for lemmatisation + RuWordNet for synonyms
  - EN: NLTK WordNetLemmatizer + WordNet for synonyms

Bulk import does not use this pipeline anymore: imported rows use the faster
`extract_focus_fast()` path plus multilingual embeddings. This module remains
important at query time for lemmatisation, synonyms, and language detection.
"""
from __future__ import annotations

import logging
import re
from typing import Literal

logger = logging.getLogger(__name__)

Language = Literal["ru", "en"]

# ── Stop-word tables ──────────────────────────────────────────────────────────

_STOP_RU: frozenset[str] = frozenset({
    # предлоги, союзы, частицы
    "а", "и", "в", "на", "с", "у", "к", "о", "из", "за", "по", "от", "до",
    "что", "как", "это", "так", "ты", "я", "мы", "он", "она", "они", "вы",
    "не", "да", "но", "же", "ли", "бы", "то", "ещё", "еще", "уже", "вот",
    "все", "всё", "мне", "меня", "тебе", "тебя", "нам", "нас", "мой", "твой",
    "если", "когда", "чтобы", "потому", "очень", "только", "просто", "прям",
    "какие", "какой", "какая", "какое", "который", "которая", "которое",
    "хочешь", "хочу", "могу", "можешь", "буду", "будет", "есть", "был", "была",
    "опять", "снова", "теперь", "сейчас", "тоже", "также", "быть", "этот",
    # наречия времени и количества
    "сегодня", "вчера", "завтра", "наконец", "столько", "сколько", "немного",
    "много", "мало", "чуть", "совсем", "вообще", "всегда", "никогда", "иногда",
    "потом", "затем", "сначала", "вдруг", "просто", "буквально", "реально",
    "кстати", "однако", "впрочем", "например", "вроде", "типа", "нибудь",
    # местоимения и указательные
    "себя", "себе", "сам", "сама", "само", "сами", "тот", "эта", "эти",
    "такой", "такая", "такое", "такие", "свой", "своя", "своё", "свои",
    # падежные формы
    "его", "её", "ему", "ней", "них", "ним", "ними", "нём", "нее",
    "эти", "этим", "этих", "того", "тому", "том", "без", "для", "про", "при",
    "тут", "там", "куда", "зачем", "откуда", "здесь", "туда", "оттуда",
    "лишь", "хоть", "даже", "ведь", "мол", "дескать", "неужели", "разве",
    "либо", "иль", "иначе", "именно", "нет", "нету", "ещё",
    # разговорные / филлеры
    "ладно", "короче", "ну", "ого", "ааа", "ммм", "блин", "наверное", "кажется",
    "ахах", "хах", "ахаха", "хаха", "лол", "давай", "давайте",
    "пожалуйста", "спасибо",
})

_STOP_EN: frozenset[str] = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "it", "its", "this", "that",
    "these", "those", "i", "you", "he", "she", "we", "they", "my", "your",
    "his", "her", "our", "their", "not", "no", "so", "if", "then", "just",
    "also", "very", "more", "most", "some", "any", "all", "as", "up", "out",
    "me", "him", "us", "them", "yes", "into", "through", "over", "under",
    "again", "once", "here", "there", "when", "where", "why", "how", "each",
    "such", "only", "own", "same", "than", "too", "much", "both", "what",
    "being", "because", "before", "after", "above", "below", "about", "down",
    # colloquial / filler
    "lmao", "lmfao", "lol", "omg", "omfg", "wtf", "bruh", "bro",
    "yeah", "yep", "nah", "nope", "okay", "ok", "like", "literally",
    "basically", "actually", "really", "totally", "maybe", "probably",
    "gonna", "wanna", "gotta", "kinda", "sorta", "dunno",
    "haha", "hahaha", "hehe", "hmm", "umm", "ugh", "wow", "damn",
    "tbh", "imo", "imho", "btw", "fyi", "idk", "nvm",
    "well", "anyway", "anyways", "whatever", "though",
})

# ── Lazy singletons ───────────────────────────────────────────────────────────

_morph_ru = None
_ruwordnet = None
_lemmatizer_en = None


def _get_morph_ru():
    global _morph_ru
    if _morph_ru is None:
        import pymorphy3
        _morph_ru = pymorphy3.MorphAnalyzer()
    return _morph_ru


def _get_ruwordnet():
    global _ruwordnet
    if _ruwordnet is None:
        try:
            from ruwordnet import RuWordNet
            _ruwordnet = RuWordNet()
        except Exception as e:
            logger.warning("[focus_point] RuWordNet not available: %s", e)
            _ruwordnet = False
    return _ruwordnet if _ruwordnet is not False else None


def _get_lemmatizer_en():
    global _lemmatizer_en
    if _lemmatizer_en is None:
        import nltk
        try:
            from nltk.stem import WordNetLemmatizer
            nltk.download("wordnet", quiet=True)
            nltk.download("omw-1.4", quiet=True)
            _lemmatizer_en = WordNetLemmatizer()
        except Exception as e:
            logger.warning("[focus_point] NLTK WordNet not available: %s", e)
            _lemmatizer_en = False
    return _lemmatizer_en if _lemmatizer_en is not False else None


# ── Core helpers ──────────────────────────────────────────────────────────────

def _clean(text: str) -> list[str]:
    """Lowercase, strip emoji / punctuation, split into words."""
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return text.split()


def detect_language(text: str) -> Language:
    """
    Lightweight query-time language detector.

    Default heuristic:
      - any Cyrillic character -> Russian
      - otherwise -> English
    """
    if re.search(r"[А-Яа-яЁёІіЇїЄєҐґ]", text or ""):
        return "ru"
    return "en"


# ── Russian pipeline ──────────────────────────────────────────────────────────

def _lemma_ru(word: str) -> str:
    try:
        return _get_morph_ru().parse(word)[0].normal_form
    except Exception:
        return word


def _synonyms_ru(word: str) -> set[str]:
    wn = _get_ruwordnet()
    if not wn:
        return set()
    result: set[str] = set()
    try:
        for synset in wn.get_synsets(word)[:3]:
            for sense in synset.senses:
                lemma = sense.name.lower()
                if lemma != word:
                    result.add(lemma)
            for hypo in synset.hyponyms[:5]:
                for sense in hypo.senses[:3]:
                    result.add(sense.name.lower())
    except Exception as e:
        logger.debug("[focus_point/ru] synonyms error for '%s': %s", word, e)
    return result


def _extract_ru(text: str, expand: bool = True) -> list[str]:
    words = _clean(text)
    keywords: set[str] = set()
    base: list[str] = []
    for w in words:
        if len(w) >= 3 and w not in _STOP_RU:
            lemma = _lemma_ru(w)
            if lemma not in _STOP_RU:
                keywords.add(lemma)
                base.append(lemma)
    if expand:
        for lemma in base:
            keywords.update(_synonyms_ru(lemma))
    return sorted(keywords)


# ── English pipeline ──────────────────────────────────────────────────────────

def _lemma_en(word: str) -> str:
    lem = _get_lemmatizer_en()
    if not lem:
        return word
    try:
        return lem.lemmatize(word)
    except Exception:
        return word


def _synonyms_en(word: str) -> set[str]:
    try:
        from nltk.corpus import wordnet
        result: set[str] = set()
        for syn in wordnet.synsets(word)[:3]:
            for lemma in syn.lemmas():
                name = lemma.name().replace("_", " ").lower()
                if name != word:
                    result.add(name)
            for hypo in syn.hyponyms()[:5]:
                for lemma in hypo.lemmas()[:3]:
                    result.add(lemma.name().replace("_", " ").lower())
        return result
    except Exception as e:
        logger.debug("[focus_point/en] synonyms error for '%s': %s", word, e)
        return set()


def _extract_en(text: str, expand: bool = True) -> list[str]:
    words = _clean(text)
    keywords: set[str] = set()
    base: list[str] = []
    for w in words:
        if len(w) >= 3 and w not in _STOP_EN:
            lemma = _lemma_en(w)
            if lemma not in _STOP_EN:
                keywords.add(lemma)
                base.append(lemma)
    if expand:
        for lemma in base:
            keywords.update(_synonyms_en(lemma))
    return sorted(keywords)


# ── Public API ────────────────────────────────────────────────────────────────

class FocusPointPipeline:
    """
    Extracts focus_point keywords from raw message text.

    Usage:
        pipeline = FocusPointPipeline(language="ru")
        keywords = pipeline.extract("Сегодня я устал и не могу работать")
        # → ["работа", "усталость", ...]

        embedding_text = pipeline.to_embedding_text(keywords)
        # → "работа усталость ..."   ← use this for vector embedding
    """

    def __init__(self, language: Language = "ru", expand_synonyms: bool = True) -> None:
        self.language = language
        self.expand_synonyms = expand_synonyms

    def extract(self, text: str) -> list[str]:
        """Return sorted list of normalised keywords for a message."""
        if not text or not text.strip():
            return []
        if self.language == "ru":
            return _extract_ru(text, expand=self.expand_synonyms)
        return _extract_en(text, expand=self.expand_synonyms)

    @staticmethod
    def to_embedding_text(keywords: list[str]) -> str:
        """Join keywords into a single string to be vectorised."""
        return " ".join(keywords)


# ── Fast tokeniser (no lemmatisation, no NLP libs) ────────────────────────────
# Mirrors the Kotlin SemanticSearchUtil.tokenize() logic exactly.
# Used during bulk import where speed matters.

_STOP_ALL: frozenset[str] = _STOP_RU | _STOP_EN | frozenset({
    # Ukrainian (from Kotlin implementation)
    "і", "з", "від", "до", "що", "як", "це", "та", "але", "ж", "чи", "би",
    "те", "ще", "вже", "ось", "всі", "мені", "мене", "тобі", "тебе", "нам",
    "нас", "мій", "твій", "якщо", "коли", "щоб", "тому", "дуже", "тільки",
    "які", "який", "яка", "яке", "котрий", "котра", "котре", "хочеш", "хочу",
    "можу", "можеш", "буду", "буде", "є", "був", "була", "знову", "тепер",
    "зараз", "теж", "також", "бути", "цей", "ці", "цим", "цих", "або",
})


def extract_focus_fast(text: str, min_len: int = 3) -> list[str]:
    """
    Fast focus_point extraction — no NLP, no lemmatisation.

    Steps (mirrors Kotlin tokenize()):
      1. Lowercase
      2. Strip everything except letters/digits/spaces
      3. Split on whitespace
      4. Drop tokens shorter than min_len
      5. Drop stop-words (RU + EN + UK)

    Returns a deduplicated list preserving order of first occurrence.
    """
    normalised = re.sub(r"[^\w\s]", " ", text.lower())
    seen: dict[str, None] = {}
    for word in normalised.split():
        if len(word) >= min_len and word not in _STOP_ALL:
            seen[word] = None
    return list(seen.keys())


def split_to_sentences(text: str, min_len: int = 15) -> list[str]:
    """
    Split text into sentences for per-sentence embedding.
    Splits on . ! ? and newlines.
    Sentences shorter than min_len chars are dropped.
    If no sentences survive, returns the full text as one item.
    """
    parts = re.split(r"[.!?\n]+", text)
    sentences = [p.strip() for p in parts if len(p.strip()) >= min_len]
    return sentences if sentences else [text.strip()]
