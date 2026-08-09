"""Rating engine — the mathematical core.

Standalone: this package depends only on the domain entities and configuration;
it never touches the UI, API, or pipeline internals. It receives a structured
feature vector and produces an explainable, deterministic rating plus a
separate confidence score.
"""

from ildrs.domain.entities import RatingResult
from ildrs.rating.base import (
    FitReport,
    ModelNotImplemented,
    ModelNotReadyError,
    OutcomeSample,
    RatingModel,
    RatingModelError,
    scale_to_100,
)
from ildrs.rating.confidence import confidence_from_features, confidence_label
from ildrs.rating.config import RatingConfig, normalize_weights
from ildrs.rating.decay import (
    days_between,
    decay_rate_from_half_life,
    exponential_decay,
    half_life,
    parse_timestamp,
    time_decay_signal,
)
from ildrs.rating.ev import ExpectedValue
from ildrs.rating.explain import build_explanation, explain_feature, format_contribution
from ildrs.rating.future import (
    FutureMLModel,
    FutureProbabilisticModel,
    FutureStatisticalModel,
)
from ildrs.rating.normalize import (
    clamp01,
    normalize_binary,
    normalize_categorical,
    normalize_count_log,
    normalize_feature,
    normalize_passthrough,
    normalize_score01,
)
from ildrs.rating.registry import available_models, create_model
from ildrs.rating.transform import transform_feature, transform_identity, transform_quadratic
from ildrs.rating.weighted import WeightedRatingModel, WeightedScoringModel

__all__ = [
    "RatingModel",
    "WeightedRatingModel",
    "WeightedScoringModel",
    "FutureStatisticalModel",
    "FutureProbabilisticModel",
    "FutureMLModel",
    "RatingConfig",
    "RatingResult",
    "OutcomeSample",
    "FitReport",
    "ExpectedValue",
    "RatingModelError",
    "ModelNotReadyError",
    "ModelNotImplemented",
    "normalize_weights",
    "normalize_feature",
    "normalize_binary",
    "normalize_score01",
    "normalize_count_log",
    "normalize_categorical",
    "normalize_passthrough",
    "clamp01",
    "transform_feature",
    "transform_identity",
    "transform_quadratic",
    "exponential_decay",
    "decay_rate_from_half_life",
    "half_life",
    "days_between",
    "parse_timestamp",
    "time_decay_signal",
    "confidence_from_features",
    "confidence_label",
    "explain_feature",
    "format_contribution",
    "build_explanation",
    "create_model",
    "available_models",
    "scale_to_100",
]
