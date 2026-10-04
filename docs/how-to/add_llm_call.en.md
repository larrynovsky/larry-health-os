<!-- translation-of: docs/how-to/add_llm_call.md sha256:c618b18c39c9 -->
**English** · [Русский](add_llm_call.md)

# How to add a new model call

1. Get the client via `llm_client.guarded_client()` (or `hai_core.get_client()`). There is no other
   way out to a model: the secrets guard sits on this client.
2. Name the task in the call and set the answer length:

   ```python
   resp = client.messages.create(task="my_module.my_function",
                                 model=hai_core.get_model("sonnet"),
                                 max_tokens=800, messages=[...])
   text = llm_client.answer_text(resp)
   ```

   `max_tokens` is the answer length. Do not add room for thinking: the wrapper adds it.
   Take the text only through `llm_client.answer_text` — the first block of an answer can be reasoning.
3. Add the task to `methodology/llm_task_modes.json`:

   ```json
   "my_module.my_function": {"mode": "think", "why": "judgement: ..."}
   ```

   `think` — there is inference between input and answer; `read` — the answer comes from reading.
   In doubt and there is reference data — `disputed`, and decide by measurement.
4. Run `pytest tests/unit/test_llm_task_modes.py`. It is red if a call has no key, if the key is not
   in the table, or if a key in the table has no call.

Fields of the table and of the model profile are in the [reference](../reference/llm_thinking.md).
