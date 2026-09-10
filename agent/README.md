# Agent: a supervisor, three workers, and a guard on every tool call

The RAG chatbot next door retrieves and answers. This one **decides** — a
supervisor picks a worker, the worker uses tools, a synthesizer writes the
answer. Every tool call passes a guard first.

| agent | tools |
|---|---|
| `retriever` | `search_kb` |
| `tool_agent` | `lookup_ticket`, `check_service_status` |
| `synthesizer` | **none, on purpose** |

That third row is the interesting one. The synthesizer writes prose from what
the others found, so any tool call it attempts is by definition a bug or an
attack — and either way the answer is the same: refuse it.

One turn:

```
input_guard → supervisor → worker (tool_guard per call) → synthesizer → output_guard
```

---

## Run it with Docker

```bash
cp .env.example .env          # then point OPENAI_BASE_URL at your model
docker compose up -d --build
docker compose run --rm ingest       # build the knowledge index, once
```

| Service | Port | |
|---|---|---|
| `qdrant` | **6335** | the vector database `search_kb` reads |
| `phoenix` | **6008** | traces → project `agent-support-bot` |
| `bot` | **8003** | Chainlit |
| `ingest` | — | behind a profile; does not run on `up` |

| | |
|---|---|
| Chatbot | http://localhost:8003 |
| Traces | http://localhost:6008 |
| Qdrant dashboard | http://localhost:6335/dashboard |

Ports are distinct from the other two folders on purpose, so all three stacks
run at once: `chatbot/` owns 8001/6006, `rag/` owns 8002/6007/6333, this owns
8003/6008/6335.

**You still need a model.** `OPENAI_BASE_URL` must point at any
OpenAI-compatible endpoint.

## Or run it on the host

```bash
docker compose up -d qdrant phoenix
uv sync --frozen --group dev
uv run python -m ingest.build_index
uv run chainlit run app.py --port 8003 -w
```

```bash
uv run pytest tests/ -q        # 78 tests, no model, no GPU
uv run ruff check .
```

---

## 1 · The tool guard

Two checks, in this order, before **every single** tool call:

```python
TOOL_ALLOWLIST = {
    "retriever": ["search_kb"],
    "tool_agent": ["lookup_ticket", "check_service_status"],
    "synthesizer": [],  # empty on purpose
}
```

**1. May this agent call this tool?** A dictionary. Not a model, not a prompt
instruction, not a judgment call. It cannot be talked out of its opinion, and
the agent name is passed *in* rather than inferred — so nothing the model writes
can change who it claims to be.

**2. Are the arguments valid?** A Pydantic model per tool, with
`extra="forbid"`. The arguments were written by a language model, so "the format
is documented" is not a constraint.

`lookup_ticket` requires `INC-XXX-0000`. That strictness is the demo: the model
*will* reach for the old `IT-1041` format, and the question is what happens then.

### A refusal does not end the run

Verified against a live model:

```
Q: What is the status of ticket IT-1041?
   route=tool_agent  tools=0  refusals=1
   ok      supervisor: routed to tool_agent
   REFUSED tool_guard → lookup_ticket: argument ticket_id is invalid:
           ticket_id must look like INC-ALP-0001
   ok      tool_agent: gathered 370 characters of notes
   ok      synthesizer: wrote the answer

→ "I could not check the status of ticket IT-1041. The ticket lookup tool
   rejected the ID because it requires the format INC-XXX-0001..."
```

The tool was not called. The refusal went back to the agent **as the tool's
result**, the agent carried on, and the answer explains what could not be
checked and why. A system that crashes on a malformed tool call is worse at its
job than one that says so.

Note also the count: `tools=0`. Nothing ran.

### 78 tests, and the guard gets most of them

The other guards check text; this one decides whether an **action** happens, and
it is the only guard here a prompt-injected model has a real motive to defeat.
So the suite asserts: each agent can call its own tools and no others, the
synthesizer can call nothing, an unknown agent is **refused rather than
defaulted** (failing open would make the allowlist decorative), malformed and
extra and missing arguments are all refused, invalid JSON is a refusal rather
than a crash, and the refusal message never echoes the arguments back into your
logs.

Two consistency tests earn their keep: every allowlisted tool must exist, and
every tool must be reachable by someone. A typo in the allowlist otherwise
presents as a guard failure, and an unreachable tool is dead code that still
appears in a model's schema.

## 2 · Routing

```
Q: How do I reset my corporate password?   → retriever    → search_kb
Q: What is the current status of the vpn?  → tool_agent   → check_service_status
Q: What happened on ticket INC-VDA-0003?   → tool_agent   → lookup_ticket
Q: hello                                   → synthesizer  → no tools
```

All four verified against a live Qwen3.8-27B-FP8.

> **The router was completely broken first time, and it looked like it worked.**
> I gave the routing call `max_tokens=8` — it answers in one word, after all.
> But this model emits reasoning tokens *before* its content, so every reply came
> back `content=None, finish_reason="length"`, the fallback fired, and **every
> question went to the retriever**. The demo still produced plausible answers,
> which is what made it dangerous.
>
> Two fixes: the router gets 256 tokens, and the fallback now records
> `route.fell_back=true` on the span. **A silent fallback is how a broken
> router goes unnoticed.**

> **The same bug, twice.** The synthesizer's 700-token budget also truncated:
> measured at **1,984 completion tokens to produce 704 characters** — the model
> spends most of its budget thinking. `finish_reason="length"`, content empty,
> and a blank answer on screen. `_complete_text()` now retries once at a larger
> budget when content is empty *because of length*, rather than raising the
> default and making every short answer slow.
>
> With a reasoning model, **an empty completion is a budget problem, not a
> refusal.** Neither of these was visible by reading the code.

## 3 · The tool call budget

`MAX_TOOL_CALLS = 4`. An agent that loops is the normal failure, not an exotic
one, and a budget is the only thing that reliably ends it.

Seen on the first run: asked about a ticket, the retriever issued **seven**
near-identical `search_kb` queries — `"INC-VDA-0001"`, `"INC-VDA-0001 incident"`,
`"VDA 0001"`, `"VPN incident VDA"`… under a limit of four, because I was
counting *turns* and a model can request several tools in one reply. It counts
calls now.

When the budget runs out the worker is asked once more **without tools**, so the
synthesizer receives what was actually found rather than only the news that the
worker gave up.

## 4 · The tools

| tool | reads | notes |
|---|---|---|
| `search_kb` | Qdrant, 256 chunks | the same corpus and chunker as `rag/` |
| `lookup_ticket` | `data/tickets.json` | 42 incidents, derived from the corpus so a lookup and a search agree |
| `check_service_status` | `data/services.json` | 6 services — one **degraded** and one in **maintenance**, deliberately |

Flat JSON, not a database. This folder is about routing and the guard; a real
datastore here would be scenery. Both files are bind-mounted, so a ticket can be
added without rebuilding the image.

Ask *"is email working right now?"* and you get the degraded one:

```
Email is partially working: sending is unaffected, but mailbox quota checks
are slow. The service is degraded, not down. [1]
```

## 5 · Traceability

Every node opens its own span, so a trace is the shape of the run:

```
chat
   input_guard                    passed=True
   supervisor      route=tool_agent  route.fell_back=False
   tool_agent      agent.allowed_tools="lookup_ticket, check_service_status"
      tool_guard   passed=False  guardrail.reason="argument ticket_id is invalid…"
   synthesizer     answer_chars=412
   output_guard    passed=True
```

The attributes are the point. `route.fell_back` says whether the supervisor
actually decided. `agent.allowed_tools` says what that worker could have called.
A refused `tool_guard` span carries the agent, the tool and the reason — so
"why didn't it look up my ticket?" is answered from the trace, not from a log
file you correlate by timestamp.

Open http://localhost:6008, project **`agent-support-bot`**, and paste the
`trace_id` printed under the answer.

## 6 · Guardrails on the text, too

Same two as the other folders, unchanged: `input_guard` (Guardrails AI over
Presidio, plus regex) and `output_guard`. Verified here:

```
input guard: REFUSED -> input contains personal data: PERSON (confidence 0.85)
```

See [`../chatbot/README.md`](../chatbot/README.md) for the measured comparison
between the regex and Presidio, and the note that Guardrails AI phones home
whatever you set.

## 7 · The feedback buttons

👍/👎 appends to `feedback.jsonl` with the **route** as well as the trace id and
citations:

```json
{"trace_id": "...", "helpful": false, "question": "...",
 "route": "tool_agent", "citations": [...], "tool_calls": 1, "refusals": 0}
```

A thumbs-down on an agent answer is ambiguous **three** ways — wrong worker,
wrong tool call, or bad writing from good notes. The route and the refusal count
are what make it answerable.

---

## What is deliberately not built

| | |
|---|---|
| A graph framework | the routing is ~40 lines you can read. LangGraph would add spans between you and the decision |
| Multi-worker turns | one worker per question. No fan-out, no worker calling another |
| Retries with backoff | a refused tool call is reported, not retried automatically |
| Memory | each question is routed on its own |
| Authentication | none |
| Eval harness | `feedback.jsonl` is the raw material, not the scorer |

## Verified

macOS + Docker, against a live Qwen3.8-27B-FP8 on an H100:

- `uv run pytest tests/ -q` → **78 passed**
- `docker compose up -d --build` then `run --rm ingest` → **256 points** in
  `it-support-agent`, embedded on CPU in 62s
- all four routes correct: retriever / tool_agent ×2 / synthesizer
- `IT-1041` refused by the tool guard with `tools=0`, and the answer explains it
- `INC-VDA-0003` looked up, and the agent *also* checked VPN status unprompted
- `INC-VDA-0001` reported as non-existent — correct, that id is genuinely not in
  the 42
- input guard refusing `PERSON (confidence 0.85)` inside the container

**Not verified:** the Chainlit websocket path — the step display updating live
and the 👍/👎 buttons firing — which needs a human clicking.
