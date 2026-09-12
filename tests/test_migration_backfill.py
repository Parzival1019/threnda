"""Additional migration-level coverage for the Milestone 3 legacy backfill.

docs/milestone-3-review.md, finding 1, required the upgrade itself to get
pre-Milestone-3 unlinked findings into the Exposure/review queue, using the
same deterministic correlation rules a live import uses, idempotently, and
without ever overwriting an existing link.
``tests/test_independent_milestone3_migration.py`` (reviewer-authored, not
modified here) covers the single-unlinked-finding base case; this file
extends it with the cross-source EXACT-match, POSSIBLE-review-candidate,
never-overwrite, and idempotency-across-a-replay scenarios the fix is
specifically responsible for.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

MILESTONE_2_REVISION = "f6319dd94ee1"
NOW = "2026-01-01T00:00:00+00:00"


def run_alembic(db_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "alembic", *args],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "THRENDA_DATABASE_URL": f"sqlite:///{db_path}"},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def seed_legacy_workspace(connection: sqlite3.Connection) -> None:
    connection.execute(
        "INSERT INTO workspaces (id, name, created_at) VALUES (1, ?, ?)",
        ("Synthetic legacy workspace", NOW),
    )
    connection.execute(
        "INSERT INTO sources (id, workspace_id, name, source_type, metadata, created_at) "
        "VALUES (1, 1, ?, 'json', '{}', ?)",
        ("Synthetic legacy source A", NOW),
    )
    connection.execute(
        "INSERT INTO sources (id, workspace_id, name, source_type, metadata, created_at) "
        "VALUES (2, 1, ?, 'json', '{}', ?)",
        ("Synthetic legacy source B", NOW),
    )
    for source_id in (1, 2):
        connection.execute(
            "INSERT INTO import_runs "
            "(id, workspace_id, source_id, filename, file_hash, started_at, status, "
            "total_records, successful_records, failed_records) "
            "VALUES (?, 1, ?, 'legacy.json', ?, ?, 'completed', 1, 1, 0)",
            (source_id, source_id, "0" * 64, NOW),
        )


def seed_legacy_finding(
    connection: sqlite3.Connection,
    *,
    finding_id: int,
    source_id: int,
    fingerprint: str,
    source_finding_id: str,
    title: str,
    cve: str | None = None,
    exposure_id: int | None = None,
) -> None:
    connection.execute(
        "INSERT INTO findings "
        "(id, workspace_id, fingerprint, source_id, import_run_id, source_finding_id, "
        'title, severity, cve, exposure_id, "references", raw_data, first_seen, last_seen, '
        "created_at, updated_at) VALUES "
        "(?, 1, ?, ?, ?, ?, ?, 'high', ?, ?, '[]', '{}', ?, ?, ?, ?)",
        (
            finding_id,
            fingerprint,
            source_id,
            source_id,
            source_finding_id,
            title,
            cve,
            exposure_id,
            NOW,
            NOW,
            NOW,
            NOW,
        ),
    )


def seed_legacy_exposure(connection: sqlite3.Connection, *, exposure_id: int, title: str) -> None:
    connection.execute(
        "INSERT INTO exposures "
        "(id, workspace_id, canonical_fingerprint, title, severity, risk_score, status, "
        "acknowledged, first_seen, last_seen, created_at, updated_at) "
        "VALUES (?, 1, ?, ?, 'high', 0, 'open', 0, ?, ?, ?, ?)",
        (exposure_id, f"synthetic-{exposure_id}", title, NOW, NOW, NOW, NOW),
    )


def test_legacy_exact_match_across_sources_correlates_during_backfill(tmp_path: Path) -> None:
    """Two legacy findings from different sources sharing the identical
    fingerprint are exactly the "deterministic cross-source correlation
    where safe" the review asked for — they must land on the same exposure
    after the upgrade, not two separate ones."""
    db_path = tmp_path / "synthetic-exact-backfill.db"
    assert run_alembic(db_path, "upgrade", MILESTONE_2_REVISION).returncode == 0
    with sqlite3.connect(db_path) as connection:
        seed_legacy_workspace(connection)
        seed_legacy_finding(
            connection,
            finding_id=1,
            source_id=1,
            fingerprint="shared-legacy-fingerprint",
            source_finding_id="legacy-a",
            title="Synthetic shared issue",
        )
        seed_legacy_finding(
            connection,
            finding_id=2,
            source_id=2,
            fingerprint="shared-legacy-fingerprint",
            source_finding_id="legacy-b",
            title="Synthetic shared issue",
        )
        connection.commit()

    result = run_alembic(db_path, "upgrade", "head")
    assert result.returncode == 0, result.stderr

    with sqlite3.connect(db_path) as connection:
        rows = connection.execute("SELECT id, exposure_id FROM findings ORDER BY id").fetchall()
        assert rows[0][1] is not None
        assert rows[0][1] == rows[1][1], "both findings must land on the same exposure"
        history_count = connection.execute(
            "SELECT COUNT(*) FROM exposure_history WHERE exposure_id = ?", (rows[0][1],)
        ).fetchone()[0]
        assert history_count == 1, "only the second (linked-after-the-fact) finding is audited"


def test_legacy_possible_match_creates_review_candidate_during_backfill(tmp_path: Path) -> None:
    """A POSSIBLE-band pair (fuzzy-similar titles gated on a shared CVE,
    the exact scores already verified in test_dedup_correlation.py) must
    become a pending review candidate during backfill, never an automatic
    merge."""
    db_path = tmp_path / "synthetic-possible-backfill.db"
    assert run_alembic(db_path, "upgrade", MILESTONE_2_REVISION).returncode == 0
    with sqlite3.connect(db_path) as connection:
        seed_legacy_workspace(connection)
        seed_legacy_finding(
            connection,
            finding_id=1,
            source_id=1,
            fingerprint="legacy-possible-a",
            source_finding_id="legacy-a",
            title="SQL injection in login form",
            cve="CVE-2099-00002",
        )
        seed_legacy_finding(
            connection,
            finding_id=2,
            source_id=2,
            fingerprint="legacy-possible-b",
            source_finding_id="legacy-b",
            title="SQL injection vulnerability in the login form",
            cve="CVE-2099-00002",
        )
        connection.commit()

    result = run_alembic(db_path, "upgrade", "head")
    assert result.returncode == 0, result.stderr

    with sqlite3.connect(db_path) as connection:
        exposure_ids = [
            row[0]
            for row in connection.execute("SELECT exposure_id FROM findings ORDER BY id").fetchall()
        ]
        pending = connection.execute(
            "SELECT finding_id, status FROM possible_duplicates"
        ).fetchall()
        # The first finding processed (ascending id) has no candidate yet
        # and becomes a standalone exposure; the second is the one that
        # finds a POSSIBLE-band match against it and must NOT be linked.
        assert exposure_ids[0] is not None
        assert exposure_ids[1] is None
        assert len(pending) == 1
        assert pending[0] == (2, "pending")


def test_backfill_never_overwrites_an_existing_link(tmp_path: Path) -> None:
    """A legacy finding that already has an exposure_id must be left
    exactly as-is — the backfill only ever acts on unlinked findings."""
    db_path = tmp_path / "synthetic-preexisting-link.db"
    assert run_alembic(db_path, "upgrade", MILESTONE_2_REVISION).returncode == 0
    with sqlite3.connect(db_path) as connection:
        seed_legacy_workspace(connection)
        seed_legacy_exposure(connection, exposure_id=1, title="Synthetic pre-linked exposure")
        seed_legacy_finding(
            connection,
            finding_id=1,
            source_id=1,
            fingerprint="already-linked-fingerprint",
            source_finding_id="legacy-a",
            title="Synthetic already-linked finding",
            exposure_id=1,
        )
        connection.commit()

    result = run_alembic(db_path, "upgrade", "head")
    assert result.returncode == 0, result.stderr

    with sqlite3.connect(db_path) as connection:
        exposure_id = connection.execute(
            "SELECT exposure_id FROM findings WHERE id = 1"
        ).fetchone()[0]
        assert exposure_id == 1
        history_count = connection.execute(
            "SELECT COUNT(*) FROM exposure_history WHERE exposure_id = 1"
        ).fetchone()[0]
        assert history_count == 0, "an already-linked finding must not be re-audited either"


def test_backfill_is_idempotent_across_a_downgrade_and_reupgrade(tmp_path: Path) -> None:
    """Replaying this migration's upgrade() (via a downgrade then a
    re-upgrade of the same database) must not re-link or re-audit a finding
    the first run already resolved — the exposure_id guard makes the
    backfill idempotent regardless of how many times upgrade() executes."""
    db_path = tmp_path / "synthetic-idempotent-backfill.db"
    assert run_alembic(db_path, "upgrade", MILESTONE_2_REVISION).returncode == 0
    with sqlite3.connect(db_path) as connection:
        seed_legacy_workspace(connection)
        seed_legacy_finding(
            connection,
            finding_id=1,
            source_id=1,
            fingerprint="idempotent-shared-fingerprint",
            source_finding_id="legacy-a",
            title="Synthetic idempotent issue",
        )
        seed_legacy_finding(
            connection,
            finding_id=2,
            source_id=2,
            fingerprint="idempotent-shared-fingerprint",
            source_finding_id="legacy-b",
            title="Synthetic idempotent issue",
        )
        connection.commit()

    assert run_alembic(db_path, "upgrade", "head").returncode == 0
    with sqlite3.connect(db_path) as connection:
        first_run_exposure_ids = connection.execute(
            "SELECT exposure_id FROM findings ORDER BY id"
        ).fetchall()
        shared_exposure_id = first_run_exposure_ids[0][0]
        first_run_history_count = connection.execute(
            "SELECT COUNT(*) FROM exposure_history WHERE exposure_id = ?", (shared_exposure_id,)
        ).fetchone()[0]

    downgrade = run_alembic(db_path, "downgrade", MILESTONE_2_REVISION)
    assert downgrade.returncode == 0, downgrade.stderr
    reupgrade = run_alembic(db_path, "upgrade", "head")
    assert reupgrade.returncode == 0, reupgrade.stderr

    with sqlite3.connect(db_path) as connection:
        second_run_exposure_ids = connection.execute(
            "SELECT exposure_id FROM findings ORDER BY id"
        ).fetchall()
        second_run_history_count = connection.execute(
            "SELECT COUNT(*) FROM exposure_history WHERE exposure_id = ?", (shared_exposure_id,)
        ).fetchone()[0]

    assert second_run_exposure_ids == first_run_exposure_ids, (
        "a replayed backfill must not move an already-linked finding to a different exposure"
    )
    assert second_run_history_count == first_run_history_count, (
        "a replayed backfill must not create a duplicate audit entry for an already-linked finding"
    )
