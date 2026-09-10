# IT support assistant, multi-agent

A supervisor picks one worker, the worker uses its tools, a synthesizer writes
the answer. Every tool call passes a guard first.

| agent | tools |
|---|---|
| `retriever` | `search_kb` |
| `tool_agent` | `lookup_ticket`, `check_service_status` |
| `synthesizer` | **none, on purpose** |

The steps are shown under every answer: which worker was chosen, which tools ran,
and which calls the guard refused.

Try:

- `how do I reset my password` → routed to **retriever**, answers from the corpus
- `what is the status of the vpn` → routed to **tool_agent**, live service status
- `what happened on ticket INC-VDA-0001` → a real ticket lookup
- `what is the status of ticket IT-1041` → the model uses the old id format, the
  **tool guard refuses the call**, and the run continues
