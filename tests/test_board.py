"""The board's rows: the one join of offers, ranking, applications and tracking.

Each feature has its own tests for its own rules; what is checked here is the join
itself — that a row carries each feature's verdict, and that filtering and sorting
mean what the board's controls promise.
"""

from __future__ import annotations

from datetime import date

from app.config import Settings
from app.models import (
    Application,
    Elimination,
    OfferDraft,
    OfferRecord,
    Profile,
    RankingRecord,
    Score,
    ScoringGrid,
)
from app.offers import store as offers_store
from app.ranking import score
from app.ranking import service as ranking_service
from app.web import board

TODAY = date(2026, 9, 19)
PROFILE = Profile()


def _offer(title: str = "An offer") -> OfferRecord:
    offer = OfferDraft(title=title, company="Acme").to_offer([])
    return offers_store.save_offer(offer, raw="", cleaned="", source="text")


def _score_with(total: int) -> Score:
    result = score.build_score(ScoringGrid())
    result.total = total
    return result


def _ranking(offer_id: int, value: Score | None, *, fingerprint: str = "") -> RankingRecord:
    return RankingRecord(
        offer_id=offer_id, elimination=Elimination(), score=value, fingerprint=fingerprint
    )


def _rows() -> list[board.Row]:
    first = _offer("Senior Backend Engineer")
    second = _offer("Data Engineer")
    rows = board.build_rows(
        [first, second],
        {
            first.id: _ranking(first.id, _score_with(90)),
            second.id: _ranking(second.id, _score_with(40)),
        },
        {
            second.id: Application(
                offer_id=second.id, status="applied", applied_on="2026-09-01"
            ),
        },
        Settings(),
        PROFILE,
        TODAY,
    )
    return rows


def _by_title(rows: list[board.Row]) -> dict[str, board.Row]:
    return {row.title: row for row in rows}


def test_a_row_carries_status_score_dates_and_followup():
    rows = _by_title(_rows())

    senior = rows["Senior Backend Engineer"]
    assert senior.status == "analyzed"  # no row means the default status
    assert senior.total == 90
    assert senior.eliminated is False
    assert senior.followup_due is False
    assert senior.applied is False
    assert senior.prepared is False

    data = rows["Data Engineer"]
    assert data.status == "applied"
    assert data.applied_on == "2026-09-01"
    assert data.followup_due is True
    assert data.days_since_contact == 18


def test_an_eliminated_offer_keeps_its_verdict():
    record = _offer()
    ranking = RankingRecord(
        offer_id=record.id,
        elimination=Elimination(eliminated=True, rule="Clearance", excerpt="SC"),
        score=None,
    )

    rows = board.build_rows([record], {record.id: ranking}, {}, Settings(), PROFILE, TODAY)

    assert rows[0].eliminated is True
    assert rows[0].ranking is not None
    assert rows[0].ranking.elimination.rule == "Clearance"


def test_the_default_order_lists_the_best_matches_and_the_eliminated_last():
    weak = _offer("Weak")
    strong = _offer("Strong")
    gone = _offer("Eliminated")
    unranked = _offer("Unranked")
    rankings = {
        weak.id: _ranking(weak.id, _score_with(40)),
        strong.id: _ranking(strong.id, _score_with(90)),
        gone.id: RankingRecord(
            offer_id=gone.id,
            elimination=Elimination(eliminated=True, rule="A rule", excerpt="An excerpt"),
            score=None,
        ),
    }

    rows = board.build_rows(
        [weak, strong, gone, unranked], rankings, {}, Settings(), PROFILE, TODAY
    )
    ordered = board.sort_rows(rows, "score", "desc")

    assert [row.title for row in ordered] == ["Strong", "Weak", "Unranked", "Eliminated"]
    assert [row.total for row in ordered[:3]] == [90, 40, None]
    assert ordered[2].needs_score is False  # never ranked is not "needs a score"


def test_a_changed_criterion_marks_the_row_out_of_date():
    record = _offer()
    settings = Settings(filter_rules="Eliminate clearance roles.")
    ranking = _ranking(
        record.id,
        _score_with(74),
        fingerprint=ranking_service.fingerprint(settings, PROFILE, record.offer),
    )

    fresh = board.build_rows([record], {record.id: ranking}, {}, settings, PROFILE, TODAY)
    assert fresh[0].stale is False

    changed = settings.model_copy(update={"filter_rules": "Something else entirely."})
    rows = board.build_rows([record], {record.id: ranking}, {}, changed, PROFILE, TODAY)
    assert rows[0].stale is True


def test_filter_rows():
    rows = _rows()
    assert len(board.filter_rows(rows, "all")) == 2
    assert [row.title for row in board.filter_rows(rows, "applied")] == ["Data Engineer"]
    assert [row.title for row in board.filter_rows(rows, "not_applied")] == [
        "Senior Backend Engineer"
    ]
    assert [row.title for row in board.filter_rows(rows, "followup")] == ["Data Engineer"]
    assert board.filter_rows(rows, "nonsense") == rows  # falls back to "all"


def test_sort_rows():
    rows = _rows()

    by_score = board.sort_rows(rows, "score", "desc")
    assert [row.total for row in by_score] == [90, 40]
    by_score_asc = board.sort_rows(rows, "score", "asc")
    assert [row.total for row in by_score_asc] == [40, 90]

    by_role = board.sort_rows(rows, "role", "asc")
    assert [row.title for row in by_role] == ["Data Engineer", "Senior Backend Engineer"]

    by_status = board.sort_rows(rows, "status", "asc")
    assert [row.status for row in by_status] == ["analyzed", "applied"]

    # An unknown key falls back to the default instead of raising.
    assert board.sort_rows(rows, "drop table", "desc")[0].total == 90


def test_summary():
    assert board.summary(_rows()) == {"total": 2, "due": 1, "applied": 1, "not_applied": 1}


def test_a_row_knows_when_its_documents_are_prepared():
    from app.tracker import store as tracker_store
    from app.writer import store as writer_store

    record = _offer()
    folder = writer_store.create("2026-09-acme-senior-backend-engineer")
    tracker_store.set_folder(record.id, folder.name)

    rows = board.build_rows(
        [record], {}, tracker_store.list_applications(), Settings(), PROFILE, TODAY
    )

    assert rows[0].prepared is True
    assert rows[0].folder == folder.name
    assert rows[0].status_label == "Analyzed"


def test_a_row_without_a_folder_is_not_prepared():
    record = _offer()

    rows = board.build_rows([record], {}, {}, Settings(), PROFILE, TODAY)

    assert rows[0].prepared is False
    assert rows[0].folder == ""
