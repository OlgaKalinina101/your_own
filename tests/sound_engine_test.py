"""
Sound Engine Test — прототип на Python.

Симулирует печатание текста потоком (как LLM стрим),
воспроизводя звуки клавиатуры с jitter, паузами на пунктуации
и волновым ритмом длинных фраз.

Звуки:
  min_sound.mp3        — короткое слово (1-3 буквы)
  min_sound_+1.mp3     — слово средней длины (4-5 букв)
  min_sound+2.mp3      — длинное слово (6-8 букв)
  min_sound+3.mp3      — очень длинное слово (9+ букв)
  two_min_sound.mp3    — пробел / мягкий разделитель
  end_sound.mp3        — конец сообщения / новая строка

Запуск:
  python tests/sound_engine_test.py
"""

import time
import random
import threading
import queue
import re
from pathlib import Path

# This module is a standalone prototype (run: python tests/sound_engine_test.py),
# but pytest collects it by name. pygame is an optional dep — skip the whole
# module under pytest when it's absent instead of failing collection.
import pytest
pygame = pytest.importorskip("pygame")

# ─── Пути к звукам ────────────────────────────────────────────────────────────
SOUNDS_DIR = Path(__file__).parent.parent / "mobile" / "assets" / "sounds" / "keyboard"

SOUND_FILES = {
    "s0":    SOUNDS_DIR / "min_sound.mp3",      # питч 0   — 1-2 буквы
    "s0_5":  SOUNDS_DIR / "min+0_5.mp3",        # питч 0.5 — 2-3 буквы
    "s1":    SOUNDS_DIR / "min_sound_+1.mp3",   # питч 1   — 3-5 букв
    "s1_5":  SOUNDS_DIR / "min+1_5.mp3",        # питч 1.5 — 5-6 букв
    "s2":    SOUNDS_DIR / "min_sound+2.mp3",    # питч 2   — 6-8 букв
    "s3":    SOUNDS_DIR / "min_sound+3.mp3",    # питч 3   — 9+ букв
    "space": SOUNDS_DIR / "two_min_sound.mp3",  # пробел/разделитель
    "end":   SOUNDS_DIR / "end_sound.mp3",      # enter/конец
}

# ─── Тайминги (секунды) ────────────────────────────────────────────────────────
BASE_WORD_INTERVAL   = 0.23   # базовая пауза между словами
JITTER_RANGE         = 0.045  # ±случайный разброс к паузе
SPACE_DELAY          = 0.065  # задержка после пробела
COMMA_DELAY          = 0.36   # пауза на запятую
PERIOD_DELAY         = 0.65   # пауза на точку/! /?
DASH_DELAY           = 0.45   # пауза на тире
NEWLINE_DELAY        = 0.78   # пауза на новую строку
LONG_WORD_EXTRA      = 0.065  # доп. пауза за каждые 3 лишних буквы в длинном слове

# Минимальный интервал между стартами двух звуков подряд.
# Звуки могут перекрываться хвостами — важно только чтобы старты
# не были ближе этого значения (убирает эффект "трёх ударов").
MIN_INTERVAL_BETWEEN_SOUNDS = 0.20

# Громкость (0.0 — тишина, 1.0 — максимум)
VOLUME = 0.45


# ─── Инициализация pygame ─────────────────────────────────────────────────────
def init_audio():
    pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=512)
    # Загружаем все звуки в память заранее
    sounds = {}
    durations = {}
    for name, path in SOUND_FILES.items():
        if path.exists():
            snd = pygame.mixer.Sound(str(path))
            snd.set_volume(VOLUME)
            sounds[name] = snd
            durations[name] = snd.get_length()  # длина в секундах
            print(f"  {name}: {durations[name]:.3f}s")
        else:
            print(f"[WARN] Файл не найден: {path}")
    return sounds, durations


# ─── Классификация слова ──────────────────────────────────────────────────────
# Таблица: (макс_длина, [варианты звуков с весами])
# На границах диапазонов — два соседних варианта, чтобы не было резкого скачка.
_WORD_SOUND_TABLE = [
    (2,  [("s0",   0.8), ("s0_5", 0.2)]),
    (3,  [("s0",   0.4), ("s0_5", 0.6)]),
    (4,  [("s0_5", 0.5), ("s1",   0.5)]),
    (5,  [("s0_5", 0.2), ("s1",   0.8)]),
    (6,  [("s1",   0.5), ("s1_5", 0.5)]),
    (7,  [("s1_5", 0.6), ("s2",   0.4)]),
    (8,  [("s1_5", 0.2), ("s2",   0.8)]),
    (99, [("s2",   0.3), ("s3",   0.7)]),
]

def classify_word(word: str) -> str:
    """Возвращает ключ звука по длине слова со случайным выбором на границах."""
    n = len(word)
    for max_len, choices in _WORD_SOUND_TABLE:
        if n <= max_len:
            keys, weights = zip(*choices)
            return random.choices(keys, weights=weights, k=1)[0]
    return "s3"


def word_extra_delay(word: str) -> float:
    """Доп. задержка для очень длинных слов — имитирует 'волну'."""
    extra_chars = max(0, len(word) - 5)
    return (extra_chars // 3) * LONG_WORD_EXTRA


# ─── Токенизатор текста → очередь событий ────────────────────────────────────
def tokenize(text: str):
    """
    Разбивает текст на события: (тип, значение, задержка_после).
    Типы: 'word', 'space', 'punct', 'newline'
    """
    events = []
    # Разбиваем по границам слов и символам пунктуации
    tokens = re.findall(r'\w+|[^\w\s]|\s+', text)

    for token in tokens:
        stripped = token.strip()

        if not stripped:  # пробелы/пробельные символы
            if '\n' in token:
                events.append(('newline', token, NEWLINE_DELAY))
            else:
                events.append(('space', token, SPACE_DELAY))

        elif stripped in ('.', '!', '?'):
            events.append(('punct', stripped, PERIOD_DELAY))

        elif stripped == ',':
            events.append(('punct', stripped, COMMA_DELAY))

        elif stripped in ('—', '-', '–'):
            events.append(('punct', stripped, DASH_DELAY))

        elif stripped in (':', ';'):
            events.append(('punct', stripped, COMMA_DELAY))

        elif re.match(r'\w+', stripped):
            delay = BASE_WORD_INTERVAL + word_extra_delay(stripped)
            delay += random.uniform(-JITTER_RANGE, JITTER_RANGE)
            delay = max(MIN_INTERVAL_BETWEEN_SOUNDS, delay)
            events.append(('word', stripped, delay))

        else:
            # прочие символы — как пробел
            events.append(('space', stripped, SPACE_DELAY))

    return events


# ─── Sound Scheduler ──────────────────────────────────────────────────────────
class SoundScheduler:
    """
    Принимает чанки текста в очередь.
    Отдельный поток вычитывает очередь и воспроизводит звуки с нужными паузами.
    Если новый чанк приходит раньше, чем обработан предыдущий — он просто
    пополняет очередь, без взрывного воспроизведения.
    """

    def __init__(self, sounds: dict, durations: dict):
        self.sounds = sounds
        self.durations = durations
        self._queue: queue.Queue = queue.Queue()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()
        self._next_play_at = 0.0  # момент времени, раньше которого нельзя играть

    def feed(self, text: str):
        """Добавить чанк текста в буфер."""
        events = tokenize(text)
        for event in events:
            self._queue.put(event)

    def end_message(self):
        """Сигнал конца сообщения — добавляет финальный звук."""
        self._queue.put(('end', '', NEWLINE_DELAY))

    def _play(self, sound_key: str):
        """Воспроизвести звук. Следующий старт — не раньше MIN_INTERVAL от этого."""
        now = time.monotonic()
        wait = self._next_play_at - now
        if wait > 0:
            time.sleep(wait)

        snd = self.sounds.get(sound_key)
        if snd:
            snd.play()

        # Фиксируем минимальный интервал до следующего старта.
        # Звуки могут перекрываться хвостами — это нормально и звучит мягко.
        self._next_play_at = time.monotonic() + MIN_INTERVAL_BETWEEN_SOUNDS

    def _worker(self):
        while not self._stop_event.is_set():
            try:
                event_type, value, delay = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue

            if event_type == 'word':
                sound_key = classify_word(value)
                self._play(sound_key)
                print(f"  [{sound_key:8s}] {value!r}")

            elif event_type == 'space':
                self._play('space')

            elif event_type == 'newline':
                self._play('end')
                print()

            elif event_type == 'punct':
                # Пунктуация — тихий пробел + пауза (без звука клавиши)
                self._play('space')
                print(f"  [punct   ] {value!r}")

            elif event_type == 'end':
                self._play('end')
                print("\n  [END MESSAGE]")

            # Пауза после события
            time.sleep(delay)

    def stop(self):
        self._stop_event.set()
        self._thread.join()


# ─── Симуляция LLM-стрима ─────────────────────────────────────────────────────
def simulate_stream(scheduler: SoundScheduler, full_text: str, chunk_size: int = 12):
    """
    Нарезает текст на чанки разного размера (как реальный LLM стрим)
    и отправляет их в scheduler с небольшими задержками между чанками.
    """
    words = full_text.split()
    i = 0
    while i < len(words):
        # Случайный размер чанка — от 1 до chunk_size слов
        size = random.randint(1, chunk_size)
        chunk_words = words[i:i + size]
        chunk = ' '.join(chunk_words)
        # Добавляем пробел в конце чанка (кроме последнего)
        if i + size < len(words):
            chunk += ' '

        scheduler.feed(chunk)

        # Задержка между чанками — имитирует latency сети/модели
        # Иногда чанк приходит быстро, иногда чуть позже
        network_delay = random.uniform(0.0, 0.15)
        time.sleep(network_delay)

        i += size


# ─── Основной тест ────────────────────────────────────────────────────────────
TEST_TEXT = """
Привет! Я думаю, что это довольно интересная идея. 
Звуковой движок для стрима — это что-то, чего я раньше не встречал.
Короткие слова звучат мягко, длинные слова дают более насыщенный тон.
Пунктуация создаёт паузы, как настоящая речь.
Тире — особый символ — делает ритм богаче.
А конец сообщения завершается отдельным красивым звуком.
""".strip()


def main():
    print("=== Sound Engine Test ===")
    print(f"Текст ({len(TEST_TEXT)} символов):")
    print(TEST_TEXT)
    print("\n--- Начинаем воспроизведение ---\n")

    sounds, durations = init_audio()
    if not sounds:
        print("[ERROR] Не удалось загрузить ни одного звука.")
        return

    print(f"\nЗагружено звуков: {list(sounds.keys())}\n")

    scheduler = SoundScheduler(sounds, durations)

    # Запускаем симуляцию стрима в отдельном потоке
    stream_thread = threading.Thread(
        target=simulate_stream,
        args=(scheduler, TEST_TEXT, 8)
    )
    stream_thread.start()
    stream_thread.join()

    # Ждём пока очередь опустеет
    scheduler.end_message()
    while not scheduler._queue.empty():
        time.sleep(0.1)

    # Даём доиграть последний звук
    time.sleep(1.5)
    scheduler.stop()
    pygame.mixer.quit()

    print("\n=== Готово ===")


if __name__ == "__main__":
    main()
