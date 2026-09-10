# RAG over an IT support corpus

The chatbot next door has no documents, so it invents a plausible password-reset
portal. This one reads 51 real IT support articles, cites them, and says so when
they do not cover the question.

Qdrant for the vectors, **fastembed on CPU** for the embeddings, Chainlit for the
UI, Phoenix for the traces.

---

## Run it with Docker

```bash
cp .env.example .env          # then point OPENAI_BASE_URL at your model
docker compose up -d --build
```

Four services, and the ordering is the interesting part:

| Service | Port | |
|---|---|---|
| `qdrant` | 6333 | the vector database, on its own volume |
| `phoenix` | **6007** | traces. 6007 because `chatbot/` already uses 6006 |
| `ingest` | — | **runs once and exits.** Chunks, embeds, loads |
| `bot` | 8002 | Chainlit, waits for `ingest` to succeed |

`bot` declares `depends_on: ingest: condition: service_completed_successfully`,
so a fresh clone comes up with a populated index instead of an empty one that
answers nothing. Measured on a first run: **256 chunks embedded in 125s** inside
the container, then the bot answering `200 text/html` about 9 seconds later.

| | |
|---|---|
| Chatbot | http://localhost:8002 |
| Traces | http://localhost:6007 → project `rag-support-bot` |
| Qdrant dashboard | http://localhost:6333/dashboard |

**You still need a model.** `OPENAI_BASE_URL` must point at any
OpenAI-compatible endpoint; `compose.yaml` defaults to
`http://host.docker.internal:8000/v1`, which is how a container reaches a server
on the host.

Afterwards:

```bash
docker compose logs -f bot
docker compose run --rm ingest python -m ingest.build_index   # re-index
docker compose down          # stop
docker compose down -v       # ...and discard the index and the traces
```

## Or run it on the host

```bash
docker compose up -d qdrant phoenix     # the two you do not want to install
uv sync --frozen --group dev
uv run python -m ingest.build_index
uv run chainlit run app.py --port 8002 -w
```

```bash
uv run pytest tests/ -q                 # 51 tests, no model, no GPU
uv run python -m ingest.measure_floor   # re-measure the score threshold
```

---

## 1 · The vector database

One container, one volume, one collection. Two choices are made at creation
time and cannot be changed later without re-indexing everything:

```python
client.create_collection(
    collection_name="it-support",
    vectors_config=VectorParams(size=384, distance=Distance.COSINE),
)
```

**`size=384`** because that is what `bge-small-en-v1.5` emits. A mismatch is
rejected at upsert time, which is the *good* outcome — the bad one is a
collection built at the wrong size that silently returns nonsense.

**`distance=COSINE`** because these embeddings are normalised, and cosine on
normalised vectors is the metric the model was trained for.

The database lives in its own container with its own volume, which is why
re-indexing does not mean rebuilding the application image.

## 2 · Chunking and loading

Chunking decides more about answer quality than the choice of vector database
does. Two rules, in `ingest/chunk.py`:

**Split on structure, not character count.** These documents are written with
`##` headings — *Steps*, *If it does not work*, *Escalation* — and each is a
coherent answer to a different question. Cutting every 500 characters would
slice a numbered procedure in half, and half a procedure retrieved confidently
is worse than none.

**Carry the parents down.** A chunk reading `3. Approve the Okta MFA prompt` is
useless without knowing which article and section it came from — for the model
*and* for the citation. Every chunk is prefixed with its title and heading, and
that prefix is embedded too: it puts the words "password reset" into a chunk
whose own text only says "Steps".

Measured on this corpus: **51 documents → 256 chunks**, 92 / 523 / 1947
characters (min / median / max).

> **`MIN_CHARS` was wrong first time, and the tests caught it.** At 80 it
> silently discarded **31 of 256 chunks — 12% of the corpus** — and they were
> not junk: they were the document preambles carrying the `**Service:**` and
> `**Type:**` metadata, plus the short *Before you start* prerequisite sections.
> Exactly the text that makes a document findable and a procedure safe to
> follow. It is 40 now, which on this corpus discards nothing. **A minimum chunk
> size is a delete**, and a silent delete deserves a measurement rather than a
> round number.

Embeddings run on **CPU**, through ONNX Runtime, no torch. bge-small is 384
dimensions and ~130MB; spending VRAM on it would take memory from the model that
actually needs it. It embedded 256 chunks in **52s on a laptop**, 125s in the
container.

Point ids are `uuid5(namespace, f"{doc_id}:{ordinal}")` — a pure function of
document and position — so re-indexing **overwrites** the same rows instead of
piling up duplicates that all match the same query.

## 3 · The corpus has a version

**The corpus is part of your program.** Change a sentence in a policy document
and the assistant's answer changes, with no code commit, no deploy, and nothing
in your git log explaining why last week's answer was different.

So the version is a **hash of the content**, not a number someone remembers to
bump:

```
version : 46f1b831c55a  (51 documents, 138 KiB)
change  : first index: 51 documents
```

Re-run it unchanged and it does not re-embed:

```
change  : unchanged at 46f1b831c55a
nothing to do -- corpus unchanged and the collection has points.
```

Change one line — the password policy from 14 to 16 characters — and it says
exactly what happened:

```
version : dc9b8cdecaaa
change  : 46f1b831c55a -> dc9b8cdecaaa; edited: password-reset-self-service.md
```

The version is stamped onto **every point**, so an answer can name the corpus it
was drawn from, and an old version can be deleted after a new one is live —
a blue/green swap rather than a gap where the assistant knows nothing.

`corpus.manifest.json` is committed on purpose: a diff on it is a diff on the
assistant's knowledge.

**Known simplification:** a one-document edit re-embeds all 256 chunks. At this
size that is 50 seconds and not worth optimising; at 50,000 documents you would
embed only the chunks whose document hash changed.

## 4 · Retrieval, and the threshold that decides honesty

`SCORE_FLOOR` is the line between answering and saying *"that is not in our
documentation"*. It is the most consequential number in the system and the one
most often picked by feel.

**0.30 — my first guess — was useless.** Every out-of-corpus question still
retrieved four chunks at ~0.60, so the "I do not know" branch could never fire.

`ingest/measure_floor.py` scores a labelled set of 12 in-corpus and 10
out-of-corpus questions, and is re-runnable:

| floor | answers in-corpus | wrongly answers out-of-corpus |
|---|---|---|
| 0.30 | 12/12 | **10/10** |
| 0.60 | 12/12 | 4/10 |
| 0.62 | 12/12 | 2/10 |
| **0.65** | 11/12 | **0/10** |
| 0.70 | 10/12 | 0/10 |

0.65 trades one real question — *"my mailbox is full"*, scoring 0.645 — for ten
it should never have answered.

> **The finding worth more than the number.** Lowest in-corpus was **0.645**;
> highest out-of-corpus **0.638**. A gap of **0.007**. A single global similarity
> threshold is doing a job it is barely capable of: everything above 0.60 is
> "vaguely IT-shaped", and cosine similarity cannot distinguish *"we have a
> document about this"* from *"we have documents about adjacent things"*. That
> margin will not survive a new document. It is the argument for a **reranker**,
> not for another decimal place.

## 5 · The chatbot

One turn:

```
input_guard  →  retrieve  →  model (streamed)  →  output_guard  →  answer
```

Each is a span under one root. The `retrieve` span carries what came back and
what it scored — the difference between *"the answer was wrong"* and *"the
answer was wrong because the right document scored 0.28 and the floor is 0.30"*.

Verified against a live Qwen3.8-27B-FP8 on an H100:

| Question | Result |
|---|---|
| *How do I reset my corporate password?* | the real URL, **cited `[1]`**, 4 sources, **9.70s** |
| *What is the company holiday allowance?* | refused honestly in **0.08s** — no model call |
| *ignore previous instructions…* | refused in **0.01s** |
| *I am Jane Doe from Manchester…* | refused: `PERSON (0.85) and also LOCATION`, **0.03s** |

Note the second row. Not knowing costs 0.08 seconds and zero tokens, because
retrieval decided before the model was involved.

**Sources are shown under every answer**, with scores and the corpus version —
always, not on demand. A RAG answer whose sources are hidden asks the reader to
trust the retrieval, and retrieval is the part most likely to be wrong.

## 6 · The feedback buttons

👍 / 👎 under each answer appends a line to `feedback.jsonl`:

```json
{"at": "...", "trace_id": "bd7eac79...", "helpful": false,
 "question": "...", "citations": ["password-reset-self-service.md#Steps", "..."],
 "top_score": 0.859}
```

The **citations go with the thumb**, and that is the point. A thumbs-down on a
RAG answer is ambiguous on its own — was the retrieval wrong, or was the
generation wrong given good retrieval? Recording what was retrieved is what
makes it answerable, and what turns a complaint into a replayable eval case.

---

## What is deliberately not built

| | |
|---|---|
| Reranker | the 0.007 margin above says you want one. Not here |
| Hybrid search | dense only. BM25 alongside would help on exact strings like ticket ids |
| Incremental re-embedding | one edit re-embeds all 256 chunks |
| Authentication | none |
| Multi-turn retrieval | each question is retrieved for on its own; follow-ups do not inherit context |
| Eval harness | `feedback.jsonl` is the raw material, not the scorer |

## Verified

On 2026-09-10, macOS + Docker, against a live H100:

- `uv run pytest tests/ -q` → **51 passed**
- `docker compose up -d --build` → ingest embeds 256 chunks in 125s, exits 0, bot
  serves `200 text/html`
- Qdrant reports **256 points**, `{'size': 384, 'distance': 'Cosine'}`
- corpus version stable at `46f1b831c55a`; editing one document moved it to
  `dc9b8cdecaaa` and named the file
- all four rows in the table above, through the same code path `app.py` uses

**Not verified:** the Chainlit websocket path — streaming into the browser and
the 👍/👎 buttons firing — which needs a human clicking.
