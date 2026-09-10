"""The three tools the workers can call, and their schemas.

A tool is two things: a function, and a contract about what may be passed to it.
The contract is a Pydantic model rather than prose, because the arguments are
written by a language model and "please use the right format" is not a
constraint.

`lookup_ticket` is deliberately strict about its id format. That strictness is
what the tool guard demonstrates: the model will get the format wrong sooner or
later, and the interesting question is what happens then.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

DATA = Path(__file__).resolve().parent.parent / "data"

# --- argument contracts ----------------------------------------------------


class SearchKbArgs(BaseModel):
    """Anything can be searched for, so this only bounds the length."""

    model_config = {"extra": "forbid"}

    query: str = Field(min_length=2, max_length=500)


class LookupTicketArgs(BaseModel):
    """INC, a three-letter service family, four digits. INC-VDA-0001."""

    model_config = {"extra": "forbid"}

    ticket_id: str

    @field_validator("ticket_id")
    @classmethod
    def _shape(cls, value: str) -> str:
        import re

        if not re.fullmatch(r"INC-[A-Z]{3}-\d{4}", value.strip().upper()):
            raise ValueError(
                "ticket_id must look like INC-ALP-0001 "
                "(INC, a three-letter family, four digits)"
            )
        return value.strip().upper()


class CheckServiceStatusArgs(BaseModel):
    """A service name from the known list. Not free text."""

    model_config = {"extra": "forbid"}

    service: str = Field(min_length=2, max_length=60)


# --- the data the tools read ----------------------------------------------
#
# Flat JSON files, not a database. The point of this folder is the routing and
# the guard, and a real datastore here would be scenery.

_tickets: dict | None = None
_services: dict | None = None


def _load(name: str) -> dict:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def tickets() -> dict:
    global _tickets
    if _tickets is None:
        _tickets = _load("tickets.json")
    return _tickets


def services() -> dict:
    global _services
    if _services is None:
        _services = _load("services.json")
    return _services


# --- the tools themselves --------------------------------------------------


def search_kb(query: str) -> str:
    """Search the knowledge base. Returns the retrieved chunks as text."""
    from agentbot.retrieval import build_context, search

    hits, _ = search(query)
    if not hits:
        return "Nothing in the knowledge base scored above the retrieval threshold."
    return build_context(hits)


def lookup_ticket(ticket_id: str) -> str:
    """One incident record, or a plain statement that there is no such ticket."""
    record = tickets().get(ticket_id)
    if record is None:
        return (
            f"There is no ticket {ticket_id} in the incident system. "
            f"Known ids look like INC-VDA-0001."
        )
    return json.dumps(record, indent=2)


def check_service_status(service: str) -> str:
    """Current status of one service, or the list of services we know about."""
    known = services()
    match = known.get(service.strip().lower())
    if match is None:
        return (
            f"No service called {service!r}. Known services: "
            + ", ".join(sorted(known))
        )
    return json.dumps(match, indent=2)


# --- the registry ----------------------------------------------------------
#
# Name -> (callable, argument model, OpenAI tool schema). One place, so the
# guard, the graph and the model prompt cannot disagree about what exists.

REGISTRY: dict[str, tuple] = {
    "search_kb": (
        search_kb,
        SearchKbArgs,
        {
            "type": "function",
            "function": {
                "name": "search_kb",
                "description": (
                    "Search the IT support knowledge base for policies, how-to "
                    "articles and past incidents."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "What to look for"}
                    },
                    "required": ["query"],
                },
            },
        },
    ),
    "lookup_ticket": (
        lookup_ticket,
        LookupTicketArgs,
        {
            "type": "function",
            "function": {
                "name": "lookup_ticket",
                "description": (
                    "Fetch one incident ticket by id. Ids look like INC-VDA-0001: "
                    "INC, a three-letter service family, four digits."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "ticket_id": {
                            "type": "string",
                            "description": "e.g. INC-VDA-0001",
                        }
                    },
                    "required": ["ticket_id"],
                },
            },
        },
    ),
    "check_service_status": (
        check_service_status,
        CheckServiceStatusArgs,
        {
            "type": "function",
            "function": {
                "name": "check_service_status",
                "description": "Current operational status of one named service.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "service": {
                            "type": "string",
                            "description": "e.g. vpn, email, password-reset-portal",
                        }
                    },
                    "required": ["service"],
                },
            },
        },
    ),
}


def schemas_for(names: list[str]) -> list[dict]:
    """The OpenAI tool schemas for these tools, in order."""
    return [REGISTRY[n][2] for n in names if n in REGISTRY]
