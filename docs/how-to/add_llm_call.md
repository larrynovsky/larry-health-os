[English](add_llm_call.en.md) · **Русский**

# Как добавить новый вызов модели

1. Получите клиента через `llm_client.guarded_client()` (или `hai_core.get_client()`). Других
   выходов к модели нет: мимо этого клиента не проходит гард секретов.
2. Назовите задачу в вызове и задайте длину ответа:

   ```python
   resp = client.messages.create(task="my_module.my_function",
                                 model=hai_core.get_model("sonnet"),
                                 max_tokens=800, messages=[...])
   text = llm_client.answer_text(resp)
   ```

   `max_tokens` — длина ответа. Запас под думание не закладывайте: его прибавит обёртка.
   Текст берите только через `llm_client.answer_text` — первый блок ответа бывает рассуждением.
   Асинхронному вызову нужен срок — передайте `deadline="measured"`, а не `asyncio.wait_for` с
   числом: срок считается из замера модели и включает думание.
3. Добавьте задачу в `methodology/llm_task_modes.json`:

   ```json
   "my_module.my_function": {"mode": "think", "why": "суждение: ..."}
   ```

   `think` — между входом и ответом есть вывод; `read` — ответ получается чтением. Сомневаетесь и
   есть эталон — `disputed`, и решите замером.
4. Прогоните `pytest tests/unit/test_llm_task_modes.py`. Он красный, если у вызова нет ключа, если
   ключа нет в таблице или если ключ в таблице остался без вызова.

Поля таблицы и профиля модели — в [справочнике](../reference/llm_thinking.md).
