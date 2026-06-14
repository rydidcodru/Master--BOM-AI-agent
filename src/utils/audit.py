"""Audit trail for deterministic transformations.

Every transformation step (normalize, map, resolve) appends an
:class:`AuditEntry` so a processed value can be traced back to its raw input
and the exact sequence of steps applied.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AuditEntry:
    """One recorded transformation step."""

    stage: str
    field_name: str
    before: object
    after: object
    note: str = ""


@dataclass
class AuditTrail:
    """An ordered collection of audit entries for a single run."""

    run_id: str
    entries: list[AuditEntry] = field(default_factory=list)

    def record(
        self,
        stage: str,
        field_name: str,
        before: object,
        after: object,
        note: str = "",
    ) -> None:
        """Append a transformation step to the trail.

        Args:
            stage: Pipeline stage (e.g. ``"normalize.part_no"``).
            field_name: The field that was transformed.
            before: Value before the step.
            after: Value after the step.
            note: Optional human-readable note.
        """
        self.entries.append(
            AuditEntry(stage, field_name, before, after, note)
        )

    def to_records(self) -> list[dict[str, object]]:
        """Return the trail as plain dicts for serialization.

        Returns:
            One dict per audit entry.
        """
        return [
            {
                "run_id": self.run_id,
                "stage": entry.stage,
                "field_name": entry.field_name,
                "before": entry.before,
                "after": entry.after,
                "note": entry.note,
            }
            for entry in self.entries
        ]
