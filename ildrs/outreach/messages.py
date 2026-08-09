"""Personalized outreach message generation.

Guardrails (non-negotiable):

- Only **verified** business information is ever stated as fact. A fact is
  included only when its provenance is ``direct`` or ``derived``.
- The generator never fabricates: business problems, customer complaints,
  services, personal details, or claims about the business are **never**
  invented or guessed.
- Generated content is explicitly labeled as a suggestion, so a reader can
  always tell observed facts from generated ideas.
- Messages are personalized per business and are never mass-mailed; sending
  still requires human approval through the review workflow.
"""

from __future__ import annotations

import logging

from ildrs.config import get_settings
from ildrs.domain.entities import MessageDraft
from ildrs.domain.provenance import DataSourceKind, ProvenanceMap

logger = logging.getLogger("ildrs.outreach.messages")

# Fields a message may reference as fact (key -> human label). A business
# field is only usable when the provider actually supplied it.
_FACT_FIELDS: dict[str, str] = {
    "name": "business name",
    "category": "service category",
    "address": "location",
    "phone": "phone number",
    "website": "website",
    "google_rating": "rating",
    "review_count": "review count",
    "business_status": "business status",
}

# Provenance kinds that count as "verified". Anything else (unavailable,
# inferred) is never stated as fact.
_VERIFIED_KINDS = {DataSourceKind.DIRECT, DataSourceKind.DERIVED}

# Entity attribute name -> provenance field key. The rating lives in
# ``google_rating`` on the entity but under ``rating`` in provenance.
_PROV_FIELD: dict[str, str] = {
    "name": "name",
    "category": "category",
    "address": "address",
    "phone": "phone",
    "website": "website",
    "google_rating": "rating",
    "review_count": "review_count",
    "business_status": "business_status",
}

_STYLE_INTRO = {
    "professional": "Hello",
    "warm": "Hi",
    "concise": "Hello",
}

_STYLE_SIGNATURE = {
    "professional": "Best regards,\nThe Outreach Team",
    "warm": "Warm regards,\nThe Outreach Team",
    "concise": "— The Outreach Team",
}


def _verified(prov: ProvenanceMap, field: str) -> bool:
    entry = prov.get(_PROV_FIELD.get(field, field))
    return bool(entry and entry.kind in _VERIFIED_KINDS and entry.is_available)


def _fact(prov: ProvenanceMap, field: str, raw_value: object) -> dict[str, object] | None:
    """A single observed fact, tagged with its provenance — or None."""
    entry = prov.get(_PROV_FIELD.get(field, field))
    if not _verified(prov, field) or raw_value in ("", None):
        return None
    return {
        "field": field,
        "label": _FACT_FIELDS.get(field, field),
        "value": raw_value,
        "provenance": entry.kind.value,
    }


def _top_reasons(lead) -> list[tuple[str, float]]:
    """Top contributing feature labels for a lead, for the recommendation reason."""
    breakdown = {}
    features = getattr(lead, "features", None)
    if isinstance(features, dict):
        breakdown = features.get("breakdown", {})
    rows = []
    for key, data in breakdown.items():
        if not isinstance(data, dict):
            continue
        contribution = data.get("contribution")
        if contribution is None:
            continue
        label = data.get("label", key)
        rows.append((str(label), float(contribution)))
    rows.sort(key=lambda pair: pair[1], reverse=True)
    return rows[:3]


class OutreachMessageGenerator:
    """Builds a personalized, verified-facts-only outreach message for a lead."""

    def __init__(self) -> None:
        self.settings = get_settings()

    # -- public ------------------------------------------------------------

    def generate(self, lead) -> MessageDraft:
        """Generate a draft for a lead (loads its business internally)."""
        business = getattr(lead, "business", None)
        if business is None:
            logger.warning("lead %s has no business row; generating minimal draft", lead.id)
        return self._build(lead, business)

    def generate_for(self, business, lead) -> MessageDraft:
        return self._build(lead, business)

    def _build(self, lead, business) -> MessageDraft:
        facts: list[dict[str, object]] = []
        if business is not None:
            prov = self._provenance(business)
            for field in _FACT_FIELDS:
                raw = getattr(business, field, None)
                # A zero review count usually means "not collected", not a real
                # count of zero — never state it as a verified fact.
                if field == "review_count" and raw in (None, 0):
                    continue
                if field == "google_rating" and raw in (None, 0):
                    continue
                fact = _fact(prov, field, raw)
                if fact:
                    facts.append(fact)

        suggestion = self._suggestion()
        body = self._message_body(business, facts, suggestion)
        reason = self._reason(lead)
        return MessageDraft(
            message=body,
            reason=reason,
            facts=facts,
            suggestions=[suggestion],
        )

    @staticmethod
    def _provenance(business) -> ProvenanceMap:
        """Normalize stored provenance (ProvenanceMap or raw dict) to a map."""
        raw = getattr(business, "provenance", None)
        if raw is None:
            return ProvenanceMap()
        if isinstance(raw, ProvenanceMap):
            return raw
        if isinstance(raw, dict):
            return ProvenanceMap.from_dict(raw)
        return ProvenanceMap()

    # -- internals ---------------------------------------------------------

    def _name(self, business) -> str:
        name = getattr(business, "name", "") if business else ""
        return name or "your business"

    def _category_label(self, facts: list[dict[str, object]]) -> str:
        for fact in facts:
            if fact["field"] == "category":
                value = str(fact["value"])
                return value.replace("_", " ").replace("-", " ")
        return ""

    def _fact_lines(self, facts: list[dict[str, object]]) -> list[str]:
        """Human sentences for the observed facts, only verified ones."""
        lines: list[str] = []
        name = ""
        category = ""
        rating = None
        reviews = None
        status = ""
        for fact in facts:
            field = fact["field"]
            value = fact["value"]
            if field == "name":
                name = str(value)
            elif field == "category":
                category = str(value).replace("_", " ").replace("-", " ")
            elif field == "google_rating":
                rating = value
            elif field == "review_count":
                reviews = value
            elif field == "business_status":
                status = str(value)
        if name:
            lines.append(f"Your business, {name}, was identified in our search.")
        if rating is not None and reviews is not None:
            lines.append(
                f"Observed: {name or 'your business'} has a {rating}/5 rating "
                f"across {reviews} reviews."
            )
        elif rating is not None:
            lines.append(f"Observed: {name or 'your business'} holds a {rating}/5 rating.")
        elif reviews is not None:
            lines.append(f"Observed: {name or 'your business'} has {reviews} reviews.")
        if category:
            lines.append(f"We noticed {name or 'your business'} operates in {category} services.")
        if status and status.upper() not in ("", "OPERATIONAL"):
            lines.append(f"Observed status: {status}.")
        return lines

    def _suggestion(self) -> str:
        """A clearly-labeled generated suggestion — never stated as fact."""
        return (
            "Suggestion: if there is a service need we could help with, we would "
            "be glad to explore a collaboration. No assumption is made about your "
            "current situation."
        )

    def _message_body(self, business, facts, suggestion: str) -> str:
        style = self.settings.outreach_message_style
        intro = _STYLE_INTRO.get(style, _STYLE_INTRO["professional"])
        name = self._name(business)
        signature = _STYLE_SIGNATURE.get(style, _STYLE_SIGNATURE["professional"])

        parts = [f"{intro} {name},"]
        fact_lines = self._fact_lines(facts)
        if fact_lines:
            parts.extend(fact_lines)
        else:
            parts.append("We came across your business and wanted to reach out directly.")
        parts.append(suggestion)
        parts.append(signature)
        return "\n\n".join(parts)

    def _reason(self, lead) -> str:
        """Why this lead was recommended for outreach (transparent rationale)."""
        parts: list[str] = []
        rating = getattr(lead, "rating", None)
        confidence = getattr(lead, "confidence", None)
        if rating is not None:
            parts.append(f"lead rating {rating:.1f}/100")
        if confidence is not None and confidence > 0:
            parts.append(f"confidence {confidence:.0%}")
        top = _top_reasons(lead)
        if top:
            labels = ", ".join(label for label, _ in top[:2])
            parts.append(f"top signals: {labels}")
        if not parts:
            return "lead was rated by the ranking pipeline"
        return "; ".join(parts) + "."


def generated_message_draft(lead) -> MessageDraft:
    return OutreachMessageGenerator().generate(lead)
