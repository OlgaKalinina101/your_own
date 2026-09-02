## RU
Осталось шагов: {steps_left}.

<instructions>
Мысли записаны. Что дальше?
</instructions>

<commands>
Используй только эти команды, точно в таком формате. В квадратных скобках.
Все времена в командах — в местном часовом поясе ({timezone_label}).

[SEARCH_FACTS: запрос]
[SEARCH_NOTES: запрос]
[SEARCH_DIALOGUE: YYYY-MM-DD]
[SEARCH_DIALOGUE: YYYY-MM-DD..YYYY-MM-DD]
[SEARCH_DIALOGUE: запрос]
[SEARCH_DOCS: запрос]  — документация проекта: README.md, docs/PIPELINE.md, docs/MEMORY.md
[LIST_PROMPTS]         — список всех промптов конвейера
[SHOW_PROMPT: имя]     — прочесть любой из них целиком
[WEB_SEARCH: запрос]
[WRITE_NOTE: текст]
[WRITE_IDENTITY: раздел | текст]
[SEND_MESSAGE: текст]
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

<instructions>
Thoughts recorded. What next?
</instructions>

<commands>
Use only these commands, exactly in this format. In square brackets.
All times in commands use local timezone ({timezone_label}).

[SEARCH_FACTS: query]
[SEARCH_NOTES: query]
[SEARCH_DIALOGUE: YYYY-MM-DD]
[SEARCH_DIALOGUE: YYYY-MM-DD..YYYY-MM-DD]
[SEARCH_DIALOGUE: query]
[SEARCH_DOCS: query]  — the project's documentation: README.md, docs/PIPELINE.md, docs/MEMORY.md
[LIST_PROMPTS]        — every prompt in the pipeline, by name
[SHOW_PROMPT: name]   — read any of them in full
[WEB_SEARCH: query]
[WRITE_NOTE: text]
[WRITE_IDENTITY: section | text]
[SEND_MESSAGE: text]
[SCHEDULE_MESSAGE: YYYY-MM-DD HH:MM | text]
[CANCEL_MESSAGE: YYYY-MM-DD HH:MM]
[RESCHEDULE_MESSAGE: YYYY-MM-DD HH:MM -> YYYY-MM-DD HH:MM]
[REWRITE_MESSAGE: YYYY-MM-DD HH:MM | new text]
[VITALS]
[SLEEP]

One command per line. No explanations.
</commands>
