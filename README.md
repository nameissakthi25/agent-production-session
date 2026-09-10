# Agent Production Session

Running an open model yourself, and putting the parts around it that turn a demo
into something you could defend: **guardrails**, **tracing**, and a **UI**.

```
agent-production-session/
├── serving-from-scratch.ipynb   ← start here: empty machine → traced, guarded endpoint
├── chatbot/                     ← the same ideas as a running app, no documents
└── rag/                         ← ...and with a corpus it can actually read
```

Read them in that order. The chatbot invents a plausible password-reset portal
because it has nothing to read; `rag/` gives it 51 real IT support articles, and
the difference between the two answers is the entire argument for retrieval.

---

## The notebook

`serving-from-scratch.ipynb` is the ground-up version. Nothing is pre-installed
and nothing hides behind a Makefile — every step is a command you run in order.

| § | |
|---|---|
| 1 | `pip install huggingface_hub`, then list what Qwen actually publishes and work out which checkpoints fit your card |
| 2 | `pip install vllm` and serve one, with the failure mode of every flag |
| 3 | Inference, GPU memory, and latency — measured, including the three-way split between weights, activations and KV cache |
| 4 | Tracing with Phoenix, and how to find one specific run |
| 5 | Guardrails AI — PII and NSFW validators, and what they cost per request |

**It wants its own environment.** vLLM pins `torch`, `transformers` and `openai`
hard, and will move them under anything else you have installed:

```bash
python3 -m venv ~/serving-venv && source ~/serving-venv/bin/activate
pip install ipykernel && python -m ipykernel install --user --name serving
```

Then pick the **serving** kernel. You need an NVIDIA GPU with ~40GB free for the
default model; the notebook names a small alternative for 24GB cards.

---

## The two applications

Each is a self-contained project: its own folder, README, lockfile, Docker
image, compose file, guards and Phoenix project. Nothing is imported across the
boundary, so either one can be read, run or copied on its own.

| | [`chatbot/`](chatbot/) | [`rag/`](rag/) |
|---|---|---|
| **Reads documents** | no | **yes** — 51 IT support articles |
| **Answer to "how do I reset my password"** | invents a plausible portal | the real URL, cited `[1]` |
| **Says "I do not know"** | never | when nothing clears the score floor |
| Ports | 8001, 6006 | 8002, 6007, 6333 |
| Phoenix project | `chainlit-support-bot` | `rag-support-bot` |
| Guards | input + output | input + output, before and after retrieval |
| Tests | 26 | 51 |
| Docs | [chatbot/README.md](chatbot/README.md) | [rag/README.md](rag/README.md) |

**Read them in that order.** The chatbot invents a password-reset portal because
it has nothing to read; `rag/` gives it a corpus, and the difference between the
two answers is the entire argument for retrieval.

They are separate compose projects and run at the same time — verified with both
up: 8001, 8002, 6006, 6007 and 6333 all serving. They were both on host 6006 at
first, which fails on the port bind the moment you bring up the second one.

```bash
cd chatbot && docker compose up -d --build     # http://localhost:8001
cd ../rag  && docker compose up -d --build     # http://localhost:8002
```

Both need a model: point `OPENAI_BASE_URL` at any OpenAI-compatible endpoint.

## The ideas the three share

Each app documents its own version of these. Stated once here so the repetition
is deliberate rather than accidental.

**Guards go around the model, never inside it.** One before anything reaches it,
one before the answer reaches the user. A check that runs after the model has
already acted is not a guardrail, it is a log entry.

**Tracing is per request, not aggregate.** "Why did it say that?" is a question
about one answer, and only a trace can answer it. Phoenix runs as a container in
both apps — a trace backend is infrastructure, not a library of your
application.

**Every measured number here came from a run.** Where a first guess turned out
wrong the number and the correction are both recorded, because the correction is
usually the more useful half: a chunk minimum that deleted 12% of the corpus, a
score floor that made "I do not know" unreachable, a guard framework that phones
home whatever you set.

**Nothing is shared between the two apps.** The guards are duplicated rather
than factored into a common package, on purpose: each folder can be read, run or
lifted out on its own, and a teaching repo whose examples depend on each other
teaches the dependency instead of the idea.

---

## Verified

Every claim in the two app READMEs was measured on 2026-09-10 against a live
Qwen3.8-27B-FP8 on an H100, not estimated. Each README ends with its own
verified list and says plainly what is **not** verified — in both cases the
Chainlit websocket path, which needs a human clicking.

The notebook runs end to end: **32 code cells, zero failures.**
