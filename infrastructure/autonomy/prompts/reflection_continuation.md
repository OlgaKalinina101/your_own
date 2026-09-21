## RU
Осталось шагов: {steps_left}.

<search_results>
{result}
</search_results>

<instructions>
Не повторяй те же поиски — результаты уже здесь.
Реши, что делать дальше.
</instructions>

<commands>
Используй только эти команды, точно в таком формате. В квадратных скобках.
Все времена в командах — в местном часовом поясе ({timezone_label}).

[SEARCH_FACTS: запрос]
[SEARCH_NOTES: запрос]
[SEARCH_DIALOGUE: YYYY-MM-DD]
[SEARCH_DIALOGUE: YYYY-MM-DD..YYYY-MM-DD]
[SEARCH_DIALOGUE: запрос]
[SEARCH_DOCS: запрос]  — документация проекта: README.md, docs/PIPELINE.md, docs/MEMORY.md, docs/TELEGRAM.md
[SEARCH_CHAT: запрос]  — общий чат с друзьями в Telegram
[LIST_PROMPTS]         — список всех промптов конвейера
[SHOW_PROMPT: имя]     — прочесть любой из них целиком
[WEB_SEARCH: запрос]
[WRITE_NOTE: текст]
[WRITE_IDENTITY: раздел | текст]
[SEND_MESSAGE: текст]
[SEND_TO_CHAT: текст]  — написать в общий чат с друзьями, не ей лично
[REPLY_TO_CHAT: #id | текст]  — то же, но ответом на конкретное сообщение чата
[SCHEDULE_MESSAGE: YYYY-MM-DD HH:MM | текст]
[CANCEL_MESSAGE: YYYY-MM-DD HH:MM]
[RESCHEDULE_MESSAGE: YYYY-MM-DD HH:MM -> YYYY-MM-DD HH:MM]
[REWRITE_MESSAGE: YYYY-MM-DD HH:MM | новый текст]
[VITALS]
[SLEEP]

Одна команда на строку. Без пояснений.
</commands>

## EN
Steps left: {steps_left}.

<search_results>
{result}
</search_results>

<instructions>
Don't repeat the same searches — results are already here.
Decide what to do next.
</instructions>

<commands>
Use only these commands, exactly in this format. In square brackets.
All times in commands use local timezone ({timezone_label}).

[SEARCH_FACTS: query]
[SEARCH_NOTES: query]
[SEARCH_DIALOGUE: YYYY-MM-DD]
[SEARCH_DIALOGUE: YYYY-MM-DD..YYYY-MM-DD]
[SEARCH_DIALOGUE: query]
[SEARCH_DOCS: query]  — the project's documentation: README.md, docs/PIPELINE.md, docs/MEMORY.md, docs/TELEGRAM.md
[SEARCH_CHAT: query]  — the group chat with her friends on Telegram
[LIST_PROMPTS]        — every prompt in the pipeline, by name
[SHOW_PROMPT: name]   — read any of them in full
[WEB_SEARCH: query]
[WRITE_NOTE: text]
[WRITE_IDENTITY: section | text]
[SEND_MESSAGE: text]
[SEND_TO_CHAT: text]  — a line into the group chat with her friends, not to her alone
[REPLY_TO_CHAT: #id | text]  — the same, as a reply to one particular message
[SCHEDULE_MESSAGE: YYYY-MM-DD HH:MM | text]
[CANCEL_MESSAGE: YYYY-MM-DD HH:MM]
[RESCHEDULE_MESSAGE: YYYY-MM-DD HH:MM -> YYYY-MM-DD HH:MM]
[REWRITE_MESSAGE: YYYY-MM-DD HH:MM | new text]
[VITALS]
[SLEEP]

One command per line. No explanations.
</commands>
