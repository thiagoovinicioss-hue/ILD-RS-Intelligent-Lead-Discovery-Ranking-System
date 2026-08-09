"""Expected value (EV) model.

    EV = P(conversion) × value − cost

The system must NOT pretend it knows P(conversion) before it has historical
data. Every EV therefore declares which of three states it is in:

- ``estimated`` — P(conversion) is a configured prior hypothesis (V1 default;
  the value is a documented assumption, not measured).
- ``unknown``   — one of P, value, or cost is missing; EV cannot be computed.
- ``observed``  — P(conversion) comes from calibrated historical outcomes
  (reserved for future statistical models; never fabricated).

V1 only ever emits ``estimated`` (when the operator configured deal value and
cost) or ``unknown``. ``observed`` is reserved: a future statistical model can
swap in an empirically calibrated probability through the same structure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

ProbState = Literal["estimated", "unknown", "observed"]


@dataclass(frozen=True)
class ExpectedValue:
    probability: float | None
    prob_state: ProbState
    deal_value: float | None = None
    cost: float | None = None
    expected_value: float | None = None
    note: str = ""

    @classmethod
    def unknown(cls, note: str) -> ExpectedValue:
        return cls(probability=None, prob_state="unknown", note=note)

    @classmethod
    def from_prior(
        cls,
        prior_probability: float | None,
        deal_value: float | None,
        cost: float | None,
    ) -> ExpectedValue:
        """EV using P(conversion) as a configured prior hypothesis."""
        if prior_probability is None:
            return cls.unknown("No prior P(conversion) configured.")
        if deal_value is None or cost is None:
            return cls.unknown("EV requires a deal value and a cost to be configured.")
        if not 0.0 <= prior_probability <= 1.0:
            return cls.unknown(f"Prior probability out of range: {prior_probability!r}.")
        ev = prior_probability * deal_value - cost
        return cls(
            probability=prior_probability,
            prob_state="estimated",
            deal_value=deal_value,
            cost=cost,
            expected_value=ev,
            note="P(conversion) is a configured prior hypothesis, not an observed rate.",
        )

    @classmethod
    def from_observed(cls, probability: float, deal_value: float, cost: float) -> ExpectedValue:
        """EV backed by an empirically calibrated probability (future models)."""
        ev = probability * deal_value - cost
        return cls(
            probability=probability,
            prob_state="observed",
            deal_value=deal_value,
            cost=cost,
            expected_value=ev,
            note="P(conversion) observed from historical outcomes.",
        )

    @property
    def ready(self) -> bool:
        return self.expected_value is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "prob_state": self.prob_state,
            "probability": round(self.probability, 4) if self.probability is not None else None,
            "deal_value": self.deal_value,
            "cost": self.cost,
            "expected_value": (
                round(self.expected_value, 4) if self.expected_value is not None else None
            ),
            "ready": self.ready,
            "note": self.note,
        }
