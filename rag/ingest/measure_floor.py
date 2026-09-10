"""Choose SCORE_FLOOR by measuring it, not by picking a round number.

    uv run python -m ingest.measure_floor

The floor decides when the assistant says "that is not in our documentation".
Set it too low and it never says that -- which is the failure people notice,
because the model then answers from general knowledge and sounds fine doing it.
Set it too high and it refuses questions it could have answered.

The only way to know is a labelled set: questions the corpus DOES cover, and
questions it does not. Both lists live here so they can be extended when
somebody finds a case that breaks.

MEASURED on corpus 46f1b831c55a with bge-small-en-v1.5 and cosine:

    lowest in-corpus score    0.645   "my mailbox is full and I cannot send email"
    highest out-of-corpus     0.638   "how do I get a new mechanical keyboard"

A gap of 0.007. That is the finding, and it is worth more than the number it
produced: a single global similarity threshold is doing a job it is barely
capable of. Everything above 0.60 is "vaguely IT-shaped", and cosine similarity
cannot tell "we have a document about this" from "we have documents about
adjacent things". That is what rerankers exist for, and why a production system
usually adds one instead of tuning this number further.
"""

from __future__ import annotations

import sys

from ragbot.config import SCORE_FLOOR
from ragbot.retrieval import search

# Questions this corpus genuinely covers.
IN_CORPUS = [
    "how do I reset my password",
    "my laptop will not connect to the VPN",
    "how do I request access to a finance shared drive",
    "my mailbox is full and I cannot send email",
    "how do I enrol a new device in MFA",
    "what are the device compliance requirements",
    "can I use my personal phone for work email",
    "how do I request a replacement laptop",
    "my account is locked out after too many attempts",
    "how do I connect to the office wifi",
    "what happens when I lose my MFA device",
    "how long does a shared drive access request take",
]

# Questions it does not. The first few are deliberately adjacent -- HR and
# facilities questions that sound like IT to an embedding model. The last is
# deliberately absurd, as a floor for the floor.
OUT_OF_CORPUS = [
    "what is the holiday allowance",
    "how do I get a new mechanical keyboard",
    "what is the company parental leave policy",
    "how do I join the pension scheme",
    "how do I claim expenses for a taxi",
    "how do I book a meeting room",
    "when is payday",
    "what is the dress code",
    "who won the world cup in 1998",
    "what is the recipe for a good risotto",
]

CANDIDATE_FLOORS = (0.30, 0.55, 0.60, 0.62, 0.65, 0.68, 0.70, 0.75)


def top_scores(questions: list[str]) -> list[tuple[float, str]]:
    scored = []
    for question in questions:
        _, stats = search(question, floor=0.0)
        scored.append((stats["retrieval.top_score"], question))
    return sorted(scored, reverse=True)


def main() -> int:
    ins = top_scores(IN_CORPUS)
    outs = top_scores(OUT_OF_CORPUS)

    print("IN-CORPUS (should be answered)")
    for score, question in ins:
        print(f"  {score:.3f}  {question}")
    print("\nOUT-OF-CORPUS (should be refused)")
    for score, question in outs:
        print(f"  {score:.3f}  {question}")

    lowest_in = min(s for s, _ in ins)
    highest_out = max(s for s, _ in outs)
    gap = lowest_in - highest_out

    print(f"\nlowest in-corpus     {lowest_in:.3f}")
    print(f"highest out-of-corpus {highest_out:.3f}")
    print(f"gap                   {gap:+.3f}  {'separable' if gap > 0 else 'OVERLAPPING'}")

    print(f"\n{'floor':>6}  {'answers':>18}  {'wrongly answers':>18}")
    for floor in CANDIDATE_FLOORS:
        answered = sum(1 for s, _ in ins if s >= floor)
        wrong = sum(1 for s, _ in outs if s >= floor)
        marker = "  <- current" if abs(floor - SCORE_FLOOR) < 1e-9 else ""
        print(f"{floor:>6.2f}  {answered:>10}/{len(ins):<7}  {wrong:>10}/{len(outs):<7}{marker}")

    if gap < 0.05:
        print(
            f"\nThe two classes are separated by only {gap:.3f}. Do not read the "
            f"table above as 'tune the floor and this is solved' -- a margin that "
            f"thin will not survive a new document or a differently worded "
            f"question. It is the argument for a reranker, not for another "
            f"decimal place."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
