## RU
### system
Ты помогаешь программе понять, когда к участнику дружеского чата обращаются по имени. Верни только список слов. Без пояснений.

### user
Участника чата зовут: {ai_name}

Друзья в этом чате пишут по-русски.

Если имя в настройке записано не так, как его пишут в чате, — латиницей, с приставкой вроде «AI» или «Bot», — первой строкой дай обычное написание этого имени по-русски. Именно так к нему будут обращаться.

Дальше: как друзья могут назвать его в переписке? Уменьшительные, разговорные и ласковые формы, которые реально используются в живой речи.

Правила:
- только формы этого имени, не общие слова вроде «друг» или «бро»;
- каждая форма — одно слово, только буквы;
- в именительном падеже: падежи программа образует сама;
- не выдумывай редкое и книжное; лучше меньше, но те, что правда звучат;
- не больше шести строк; если у имени нет устоявшихся кратких форм, верни меньше или одну строку: НЕТ.

Формат: по одной форме на строку.

## EN
### system
You are helping a program recognise when a member of a friendly group chat is being addressed by name. Return only a list of words. No explanations.

### user
The chat member's name is: {ai_name}

The friends in this chat write in English.

If the name in the setting is not written the way it is written in the chat — in another script, or with a tag like "AI" or "Bot" on it — give its ordinary English spelling as the first line. That is how they will be addressed.

Then: what might friends call them in a chat? Diminutives, casual and affectionate forms that people actually use.

Rules:
- only forms of this name, not general words like "buddy" or "bro";
- each form is a single word, letters only;
- plain form: the program handles grammatical cases itself;
- nothing rare or bookish; fewer is better than invented;
- no more than six lines; if the name has no established short forms, return fewer, or the single line: NONE.

Format: one form per line.
