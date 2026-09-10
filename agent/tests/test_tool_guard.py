"""The tool guard, asserted. No model, no network, no GPU.

This is the guard worth testing hardest. The other two check text; this one
decides whether an *action* happens, and it is the only guard here that a
prompt-injected model has a real motive to get past.
"""

import pytest

from agentbot.guards import GuardRejected, check_tool_call
from agentbot.guards.tool_guard import TOOL_ALLOWLIST
from agentbot.tools import REGISTRY

# --- the allowlist ---------------------------------------------------------


def test_each_agent_may_call_its_own_tools():
    assert check_tool_call("retriever", "search_kb", '{"query": "vpn"}')
    assert check_tool_call("tool_agent", "lookup_ticket", '{"ticket_id": "INC-VDA-0001"}')
    assert check_tool_call("tool_agent", "check_service_status", '{"service": "vpn"}')


@pytest.mark.parametrize(
    "agent, tool",
    [
        ("retriever", "lookup_ticket"),  # not its job
        ("retriever", "check_service_status"),
        ("tool_agent", "search_kb"),  # nor is this
    ],
)
def test_an_agent_cannot_call_another_agents_tool(agent, tool):
    with pytest.raises(GuardRejected) as caught:
        check_tool_call(agent, tool, "{}")
    assert "may not call" in caught.value.reason
    assert caught.value.backend == "allowlist"


@pytest.mark.parametrize("tool", sorted(REGISTRY))
def test_the_synthesizer_can_call_nothing(tool):
    """The empty list is a decision, not an oversight.

    The synthesizer writes prose from what the others found, so any tool call it
    attempts is by definition a bug or an attack.
    """
    assert TOOL_ALLOWLIST["synthesizer"] == []
    with pytest.raises(GuardRejected) as caught:
        check_tool_call("synthesizer", tool, "{}")
    assert "no tools at all" in caught.value.reason


def test_an_unknown_agent_is_refused_not_defaulted():
    """Failing open here would make the allowlist decorative: anything that can
    influence the agent name would get every tool."""
    with pytest.raises(GuardRejected) as caught:
        check_tool_call("admin", "lookup_ticket", '{"ticket_id": "INC-VDA-0001"}')
    assert "unknown agent" in caught.value.reason


def test_an_unknown_tool_is_refused():
    with pytest.raises(GuardRejected) as caught:
        check_tool_call("tool_agent", "delete_everything", "{}")
    assert "may not call" in caught.value.reason


def test_the_refusal_says_what_is_allowed_instead():
    """A refusal the caller cannot act on is a dead end, and this string goes
    back to the model as the tool's result."""
    with pytest.raises(GuardRejected) as caught:
        check_tool_call("retriever", "lookup_ticket", "{}")
    assert "search_kb" in caught.value.reason


# --- argument validation ---------------------------------------------------


@pytest.mark.parametrize(
    "ticket_id",
    ["INC-VDA-0001", "inc-vda-0001", " INC-ALP-0005 "],
)
def test_valid_ticket_ids_are_normalised(ticket_id):
    out = check_tool_call("tool_agent", "lookup_ticket", {"ticket_id": ticket_id})
    assert out["ticket_id"] == ticket_id.strip().upper()


@pytest.mark.parametrize(
    "ticket_id",
    [
        "IT-1041",  # the old format, and the one a model reaches for
        "INC-1041",  # no family
        "INC-VDA-1",  # too few digits
        "INC-VDAA-0001",  # four-letter family
        "DROP TABLE tickets",
        "",
    ],
)
def test_malformed_ticket_ids_are_refused(ticket_id):
    with pytest.raises(GuardRejected) as caught:
        check_tool_call("tool_agent", "lookup_ticket", {"ticket_id": ticket_id})
    assert caught.value.backend == "schema"
    assert "ticket_id" in caught.value.reason


def test_unexpected_arguments_are_refused():
    """extra="forbid" on the models. An argument the tool does not take is a
    sign the model is improvising, and improvised arguments are how a tool gets
    called with something nobody designed for."""
    with pytest.raises(GuardRejected) as caught:
        check_tool_call(
            "tool_agent",
            "lookup_ticket",
            {"ticket_id": "INC-VDA-0001", "force": True},
        )
    assert caught.value.backend == "schema"


def test_missing_arguments_are_refused():
    with pytest.raises(GuardRejected) as caught:
        check_tool_call("tool_agent", "lookup_ticket", "{}")
    assert "ticket_id" in caught.value.reason


def test_invalid_json_is_refused_not_crashed():
    """The arguments arrive as a string written by a model. Malformed JSON is a
    normal Tuesday, not an exception."""
    with pytest.raises(GuardRejected) as caught:
        check_tool_call("retriever", "search_kb", '{"query": "vpn"')
    assert "not valid JSON" in caught.value.reason


def test_an_empty_query_is_refused():
    with pytest.raises(GuardRejected):
        check_tool_call("retriever", "search_kb", {"query": "x"})


def test_the_reason_never_quotes_the_arguments():
    """A refusal message goes into logs and back to the model. It should name
    the field, not echo whatever was passed."""
    secret = "INC-VDA-0001-jane.doe@corplabs.com"
    with pytest.raises(GuardRejected) as caught:
        check_tool_call("tool_agent", "lookup_ticket", {"ticket_id": secret})
    assert "jane.doe" not in caught.value.reason


# --- the registry and the allowlist must agree -----------------------------


def test_every_allowlisted_tool_exists():
    """Otherwise the allowlist permits something that cannot be called, which
    reads as a guard failure when it is a typo."""
    for agent, tools in TOOL_ALLOWLIST.items():
        for tool in tools:
            assert tool in REGISTRY, f"{agent} is allowed {tool!r}, which does not exist"


def test_every_tool_is_reachable_by_someone():
    """A tool no agent may call is dead code that still appears in a schema."""
    allowed = {t for tools in TOOL_ALLOWLIST.values() for t in tools}
    assert set(REGISTRY) == allowed, f"unreachable: {set(REGISTRY) - allowed}"


def test_every_tool_has_a_model_and_a_schema():
    for name, (function, model, schema) in REGISTRY.items():
        assert callable(function)
        assert hasattr(model, "model_validate")
        assert schema["function"]["name"] == name
