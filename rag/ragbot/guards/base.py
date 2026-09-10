"""What a refusal is."""


class GuardRejected(Exception):
    """A guard said no.

    Carries which guard refused, why, and which backend produced the verdict --
    all three go onto the span, so a refusal is debuggable from the trace
    instead of from a log file you have to correlate by timestamp.
    """

    def __init__(self, guard: str, reason: str, backend: str = "local") -> None:
        super().__init__(reason)
        self.guard = guard
        self.reason = reason
        self.backend = backend
