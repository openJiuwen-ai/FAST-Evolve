"""SuccessDB persistence.

``SuccessDB`` is the reusable success pool.  Parent selection and inspiration
selection only read from this pool, so failed attempts cannot become parents.
The broader audit trail of every attempt lives in ``evotrias.db.attempts``.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from evotrias.db.models import SuccessRecord
from evotrias.mcp.storage import (
    read_json,
    success_db_path,
    write_json_atomic,
)


class SuccessDB:
    """Small JSON-backed repository of successful records for one run."""

    def __init__(self, run_id: str):
        self.run_id = run_id
        self.path = success_db_path(run_id)
        self._records: Dict[str, SuccessRecord] = {}
        self.load()

    def load(self) -> None:
        """Refresh in-memory records from disk."""
        data = read_json(self.path, {"records": []})
        self._records = {
            record.id: record
            for record in (SuccessRecord.from_dict(item) for item in data.get("records", []))
        }

    def save(self) -> None:
        """Persist all success records ordered by creation time."""
        records = sorted(self._records.values(), key=lambda record: record.created_at)
        write_json_atomic(
            self.path,
            {
                "run_id": self.run_id,
                "records": [record.to_dict() for record in records],
            },
        )

    def is_empty(self) -> bool:
        """Return true when no successful candidate is available."""
        return not self._records

    def get_all(self) -> List[SuccessRecord]:
        """Return all records in a stable chronological order."""
        return sorted(self._records.values(), key=lambda record: record.created_at)

    def get(self, record_id: Optional[str]) -> Optional[SuccessRecord]:
        """Return one record by id, or None for missing/null ids."""
        if record_id is None:
            return None
        return self._records.get(record_id)

    def add(self, record: SuccessRecord) -> bool:
        """Add a new success record.

        Returns ``False`` when a record with the same id already exists.  This
        gives ``record_attempt`` a simple idempotency guard.
        """
        if record.id in self._records:
            return False
        self._records[record.id] = record
        self.save()
        return True

    def increment_selection(self, record_id: str) -> None:
        """Optimistically increment how often a record was selected as parent."""
        record = self._records.get(record_id)
        if record is None:
            return
        record.selection_count += 1
        self.save()

    def get_best_record(self) -> Optional[SuccessRecord]:
        """Return the record with highest fitness."""
        if not self._records:
            return None
        return max(self._records.values(), key=lambda record: record.fitness)

    def get_canonical_best_record(self) -> Optional[SuccessRecord]:
        """Return a deterministic best record for use as an anchor parent."""
        if not self._records:
            return None
        return min(
            self._records.values(),
            key=lambda record: (-record.fitness, record.created_at, record.id),
        )

    def infer_next_generation(self, parent_id: Optional[str]) -> int:
        """Infer a child generation from its parent when the caller omitted it."""
        parent = self.get(parent_id)
        if parent is None:
            return 0
        return parent.generation + 1
