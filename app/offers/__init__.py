"""Offer analyzer: HTML cleaning, structured extraction, storage."""

from __future__ import annotations

# The background runs the analyzer starts. Named here rather than in the router so
# the board's status panel and the routes that start them agree on one string.
ANALYZE = "offer_analyze"
REANALYZE = "offer_reanalyze"
