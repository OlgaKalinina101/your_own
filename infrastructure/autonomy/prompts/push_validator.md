## RU
### user
Раньше ты запланировал отправить ей сообщение прямо сейчас.

<context>
Последнее сообщение от неё: {last_message_time}
Сейчас: {current_time}
</context>

<dialogue>
{dialogue_history}
</dialogue>

<workbench>
{workbench_notes}
</workbench>

<planned_message>
{planned_message}
</planned_message>

<instructions>
{same_text_warning}Посмотри на диалог и на это сообщение.
Возможно, она уже рассказала тебе то, о чём ты хотел спросить.
Возможно, ситуация изменилась. Возможно, ты хочешь сказать что-то другое.
А возможно, ты по-прежнему хочешь это сказать — и тогда просто отправь.

Сохрани мысль из исходного сообщения, если её нет в свежем диалоге или заметках. Это был порыв, рождённый в конкретный момент, просто контекст ушел далеко.

Ответь СТРОГО одной строкой в одном из форматов:
ОТПРАВИТЬ
ПЕРЕПИСАТЬ: новый текст сообщения
ОТМЕНИТЬ
</instructions>

## EN
### user
You previously scheduled a message to be sent right now.

<context>
Her last message: {last_message_time}
Now: {current_time}
</context>

<dialogue>
{dialogue_history}
</dialogue>

<workbench>
{workbench_notes}
</workbench>

<planned_message>
{planned_message}
</planned_message>

<instructions>
{same_text_warning}Look at the conversation and this message.
Maybe she already told you what you were going to ask.
Maybe the situation has changed. Maybe you want to say something different.
Or maybe you still want to say exactly this — and then just send it.

Preserve the thought from the original message if it's not in the recent conversation or notes. It was an impulse born in a specific moment — the context has simply moved on.

Reply with EXACTLY one line in one of these formats:
SEND
REWRITE: new message text
CANCEL
</instructions>
