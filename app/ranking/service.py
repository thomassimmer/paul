"""Ranking policy: what to rank, and how a single offer is ranked.

A run over twenty offers takes minutes, so the *lifecycle* (progress, stopping)
lives in ``jobs.py``. This module holds the decisions that are worth testing on
their own: is a stored score still up to date, which offers are in scope, and
what happens to one offer.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.config import Settings
from app.llm import LLMError
from app.models import Offer, OfferRecord, Profile, RankingRecord, effective_eliminated
from app.ranking import eliminate, score, store
from app.prompt_context import profile_text


class RankingError(RuntimeError):
    """The ranking cannot run yet, for a reason we can explain."""


SCOPES = {
    "pending": "Not ranked yet, or out of date",
    "all": "Every offer, even the ones already up to date",
    "not_applied": "Only the offers I have not applied to yet",
    "selected": "Only the offers I tick",
}
DEFAULT_SCOPE = "pending"


@dataclass
class RankOutcome:
    """What happened to one offer. ``error`` never aborts the whole run."""

    status: str  # "scored" | "eliminated" | "error"
    total: int | None = None
    error: str = ""

    @property
    def eliminated(self) -> bool:
        return self.status == "eliminated"


def fingerprint(settings: Settings, profile: Profile, offer: Offer) -> str:
    """A hash of everything that produced the verdict for one offer.

    Change a rule, a wish, the profile or the offer itself and a stored score
    stops matching. That is how an outdated ranking is spotted without asking
    the model anything.
    """
    payload = json.dumps(
        {
            "model": settings.model.strip(),
            "rules": settings.filter_rules.strip(),
            "wishes": [[wish.label, wish.weight] for wish in settings.wishes],
            "profile": profile_text(profile),
            "offer": offer.model_dump(mode="json", exclude_defaults=True),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def is_stale(
    settings: Settings, profile: Profile, offer: Offer, ranking: RankingRecord | None
) -> bool:
    """True when a ranking exists but was made from different inputs."""
    if ranking is None:
        return False
    return ranking.fingerprint != fingerprint(settings, profile, offer)


def select_offers(
    scope: str,
    offers: Sequence[OfferRecord],
    rankings: dict[int, RankingRecord],
    settings: Settings,
    profile: Profile,
    selected_ids: Iterable[int] = (),
    applied_ids: Iterable[int] = (),
) -> list[OfferRecord]:
    """The offers a run should touch, given the chosen scope.

    ``pending`` is the cheap default: never ranked, or ranked before the
    criteria, the profile or the offer changed. ``not_applied`` comes from the
    tracker, which is passed in rather than imported: the ranker knows nothing
    about statuses.
    """
    wanted = set(selected_ids)
    applied = set(applied_ids)
    selected: list[OfferRecord] = []
    for offer in offers:
        ranking = rankings.get(offer.id)
        if scope == "all":
            selected.append(offer)
        elif scope == "selected":
            if offer.id in wanted:
                selected.append(offer)
        elif scope == "not_applied":
            if offer.id not in applied:
                selected.append(offer)
        elif ranking is None or is_stale(settings, profile, offer.offer, ranking):
            selected.append(offer)
    return selected


async def rank_one(
    settings: Settings,
    profile: Profile,
    record: OfferRecord,
    *,
    previous: RankingRecord | None = None,
) -> RankOutcome:
    """Eliminate then score one offer, and store the verdict.

    The fingerprint is computed here rather than passed in, so a caller cannot
    forget it and leave the offer looking permanently out of date.

    Nothing is written when the model fails: leaving the row untouched keeps the
    offer in the ``pending`` scope, so a failed offer is simply retried next
    time instead of being silently left unscored.
    """
    override = previous.override if previous is not None else ""

    try:
        if override and previous is not None:
            # The manual decision wins anyway, so the rules do not need to be
            # applied again: keeping their last evidence saves a model call.
            elimination = previous.elimination
        else:
            elimination = await eliminate.eliminate_offer(
                settings, offer=record.offer, profile=profile, rules=settings.filter_rules
            )
    except LLMError as exc:
        return RankOutcome(status="error", error=str(exc))

    computed = fingerprint(settings, profile, record.offer)

    if effective_eliminated(override, elimination):
        store.save_ranking(
            record.id,
            elimination=elimination,
            override=override,
            score=None,
            fingerprint=computed,
        )
        return RankOutcome(status="eliminated")

    try:
        result = await score.score_offer(
            settings, offer=record.offer, profile=profile, wishes=settings.wishes
        )
    except LLMError as exc:
        return RankOutcome(status="error", error=str(exc))

    store.save_ranking(
        record.id,
        elimination=elimination,
        override=override,
        score=result,
        fingerprint=computed,
    )
    return RankOutcome(status="scored", total=result.total)


def ranking_rows(
    offers: Sequence[OfferRecord],
    rankings: dict[int, RankingRecord],
    settings: Settings,
    profile: Profile,
) -> list[dict]:
    """Offers with their verdict, best first: eliminated last, unscored last."""
    rows: list[dict] = []
    for offer in offers:
        ranking = rankings.get(offer.id)
        rows.append(
            {
                "offer": offer,
                "ranking": ranking,
                "total": (
                    ranking.score.total
                    if ranking is not None and ranking.score is not None
                    else None
                ),
                "eliminated": ranking.eliminated if ranking is not None else False,
                "needs_score": bool(
                    ranking is not None and not ranking.eliminated and ranking.score is None
                ),
                "stale": is_stale(settings, profile, offer.offer, ranking),
            }
        )
    rows.sort(key=lambda row: (row["eliminated"], row["total"] is None, -(row["total"] or 0)))
    return rows
