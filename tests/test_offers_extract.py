from __future__ import annotations

import asyncio
import json
import sys
import types

from app.config import Settings
from app.models import Keyword
from app.offers import extract


def test_clean_keywords_drops_a_variant_equal_to_its_term():
    cleaned = extract.clean_keywords(
        [
            Keyword(term="Rust", variants=["Rust", "rustc"]),
            Keyword(term="kubernetes", variants=["K8s", "KUBERNETES"]),
        ]
    )
    assert cleaned == [
        Keyword(term="Rust", variants=["rustc"]),
        Keyword(term="kubernetes", variants=["K8s"]),
    ]


def test_clean_keywords_drops_empty_terms_and_duplicates():
    cleaned = extract.clean_keywords(
        [Keyword(term="  "), Keyword(term="Kafka"), Keyword(term="kafka", variants=["Apache Kafka"])]
    )
    assert cleaned == [Keyword(term="Kafka")]


def test_extract_offer_applies_the_keyword_cleanup(monkeypatch):
    payload = json.dumps(
        {
            "title": "Senior Backend Engineer",
            "keywords": [
                {"term": "Kubernetes", "variants": ["Kubernetes", "K8s"]},
                {"term": "Rust", "variants": []},
            ],
        }
    )

    async def acompletion(**kwargs):
        message = types.SimpleNamespace(content=payload)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])

    module = types.ModuleType("litellm")
    monkeypatch.setattr(module, "acompletion", acompletion, raising=False)
    monkeypatch.setitem(sys.modules, "litellm", module)

    draft = asyncio.run(
        extract.extract_offer(Settings(model="openai/gpt-4o"), "cleaned offer text")
    )

    assert draft.title == "Senior Backend Engineer"
    assert draft.keywords == [
        Keyword(term="Kubernetes", variants=["K8s"]),
        Keyword(term="Rust"),
    ]
