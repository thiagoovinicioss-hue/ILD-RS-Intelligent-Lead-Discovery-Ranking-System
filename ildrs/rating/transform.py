"""Feature transformations (post-normalization).

After normalization each feature value ``u ∈ [0, 1]`` may be passed through a
nonlinear transformation before its weighted contribution:

    raw → normalize → transform → weighted contribution

Supported forms:

    identity      z = u
    quadratic     z = a·u² + b·u + c

Quadratic coefficients are documented hypotheses per feature. They are
applied only where a justified nonlinear response exists (see ``justification``
below). Every other feature stays linear.
"""

from __future__ import annotations

from dataclasses import dataclass

from ildrs.rating.normalize import clamp01


@dataclass(frozen=True)
class TransformSpec:
    kind: str
    a: float = 0.0
    b: float = 0.0
    c: float = 0.0
    justification: str = ""


TRANSFORM_SPECS: dict[str, TransformSpec] = {
    "business_status": TransformSpec(
        kind="quadratic",
        a=1.0,
        b=0.0,
        c=0.0,
        justification=(
            "A non-operational business is disproportionately less valuable "
            "than its linear score implies; z = u² collapses u=0.2 → 0.04 "
            "while leaving OPERATIONAL (u=1.0) unchanged."
        ),
    ),
}


def transform_identity(u: float) -> float:
    return clamp01(u)


def transform_quadratic(u: float, a: float, b: float, c: float) -> float:
    return clamp01(a * u * u + b * u + c)


def transform_feature(key: str, u: float) -> float:
    """Apply the declared transformation for a feature, defaulting to identity."""
    spec = TRANSFORM_SPECS.get(key)
    if spec is None or spec.kind == "identity":
        return transform_identity(u)
    if spec.kind == "quadratic":
        return transform_quadratic(u, spec.a, spec.b, spec.c)
    return transform_identity(u)
