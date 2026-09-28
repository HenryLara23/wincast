"""
The model file: one JSON document that holds everything needed to turn a live
feature vector into a win probability.

Why JSON and not pickle: the file gets downloaded from GitHub and opened on a
gaming PC. Unpickling runs arbitrary code, so a pickle from the internet is a
security hole. JSON is data only. It is also small (a few KB), readable, and
diffable in git.

Why it carries so much metadata: the app must be able to REFUSE a file that
doesn't match its code. `feature_set_version` and the feature list are checked
on load, so a model trained on spec 1.2.0 can never silently score vectors
built by a 1.3.0 extractor.

What it must NEVER carry: match ids, Riot IDs, PUUIDs or anything else about a
player. Only coefficients and aggregate scores. `validate()` enforces the shape.

Prediction here is numpy only (no scikit-learn), because the gaming PC doesn't
need the training stack. `tests/test_bundle.py` checks it matches the
training-side PhaseLogistic + Temperature to 1e-9.
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone

import numpy as np

from ..features import spec

FORMAT = "lolwp-model"
FORMAT_VERSION = 1


# --------------------------------------------------------------------- writing
def from_trained(calibrated, meta: dict) -> dict:
    """PhaseLogistic wrapped in Temperature -> the bundle dict."""
    from . import metrics

    base = calibrated.model
    return {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "kind": "live",
        "feature_set_version": spec.FEATURE_SET_VERSION,
        "feature_names": list(spec.FEATURE_NAMES),
        "scales": [float(s) for s in spec.SCALES],
        "model": {
            "type": "phase_logistic",
            "features": [int(i) for i in base.features],
            "time_index": int(spec.INDEX["game_time_s"]),
            "bucket_minutes": metrics.BUCKET_MINUTES,
            "first_bucket_min": metrics.FIRST_BUCKET_MIN,
            "last_bucket_min": metrics.LAST_BUCKET_MIN,
            "crossfade_s": float(base.crossfade_s),
            "fallback": [float(v) for v in base.fallback.coef_[0]],
            "buckets": {str(b): [float(v) for v in m.coef_[0]]
                        for b, m in sorted(base.models.items())},
            "temperature": float(calibrated.t),
        },
        "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **meta,
    }


def save(bundle: dict, path: str) -> None:
    validate(bundle)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(bundle, fh, indent=1, sort_keys=False)
        fh.write("\n")
    os.replace(tmp, path)          # never leave a half-written model behind


# --------------------------------------------------------------------- reading
class BundleError(ValueError):
    pass


_ALLOWED_TOP = {"format", "format_version", "kind", "feature_set_version",
                "feature_names", "scales", "model", "created_utc", "name", "patch",
                "patches_used", "games", "metrics", "gate", "code"}


def validate(b: dict) -> None:
    if b.get("format") != FORMAT:
        raise BundleError("not a lolwp model file")
    if b.get("format_version") != FORMAT_VERSION:
        raise BundleError(f"model format {b.get('format_version')} but this code "
                          f"reads {FORMAT_VERSION}; update the app")
    extra = set(b) - _ALLOWED_TOP
    if extra:
        raise BundleError(f"unexpected top-level fields {sorted(extra)}")
    m = b.get("model") or {}
    n = len(m.get("features") or [])
    if m.get("type") != "phase_logistic" or n == 0:
        raise BundleError("unsupported model type")
    for vec in [m.get("fallback")] + list((m.get("buckets") or {}).values()):
        if not isinstance(vec, list) or len(vec) != n or not all(
                isinstance(v, (int, float)) and math.isfinite(v) for v in vec):
            raise BundleError("bad coefficient vector")
    t = m.get("temperature")
    if not isinstance(t, (int, float)) or not (0.1 < t < 10):
        raise BundleError("bad temperature")


class Model:
    """A loaded model file. `predict(x)` takes the 37-feature live vector."""

    def __init__(self, bundle: dict, check_spec=True):
        validate(bundle)
        if check_spec:
            if bundle["feature_set_version"] != spec.FEATURE_SET_VERSION:
                raise BundleError(
                    f"model was trained on feature set {bundle['feature_set_version']}, "
                    f"this code builds {spec.FEATURE_SET_VERSION}. Use a matching model "
                    "or update the app.")
            if list(bundle["feature_names"]) != list(spec.FEATURE_NAMES):
                raise BundleError("feature list differs from this code's spec")
        self.bundle = bundle
        m = bundle["model"]
        self.scales = np.asarray(bundle["scales"], dtype=np.float32)
        self.features = np.asarray(m["features"], dtype=int)
        self.time_index = m["time_index"]
        self.bmin, self.first, self.last = (m["bucket_minutes"], m["first_bucket_min"],
                                            m["last_bucket_min"])
        self.crossfade_s = m["crossfade_s"]
        self.fallback = np.asarray(m["fallback"], dtype=np.float64)
        self.coefs = {int(k): np.asarray(v, dtype=np.float64)
                      for k, v in m["buckets"].items()}
        self.t = float(m["temperature"])

    @classmethod
    def load(cls, path, check_spec=True):
        with open(path, encoding="utf-8") as fh:
            try:
                b = json.load(fh)
            except json.JSONDecodeError as exc:
                raise BundleError(f"{path} is not valid JSON: {exc}") from None
        return cls(b, check_spec=check_spec)

    @property
    def name(self):
        return self.bundle.get("name", "?")

    @property
    def patch(self):
        return self.bundle.get("patch")

    def _bucket(self, t_s):
        minutes = np.asarray(t_s, dtype=np.float64) / 60.0
        b = np.floor((minutes - self.first) / self.bmin).astype(int) + 1
        b = np.where(minutes < self.first, 0, b)
        return np.clip(b, 0, (self.last - self.first) // self.bmin + 1)

    def _logit(self, xs, bucket):
        return xs @ self.coefs.get(int(bucket), self.fallback)

    def predict(self, X) -> np.ndarray:
        """P(blue wins) for each row of X (raw, unscaled features)."""
        # float32 on purpose: training scales float32 features, and matching that
        # keeps the file's predictions identical to the trained model's
        X = np.atleast_2d(np.asarray(X, dtype=np.float32))
        xs = (X / self.scales)[:, self.features]
        t = X[:, self.time_index]
        buckets = self._bucket(t)
        logit = np.array([self._logit(xs[i], buckets[i]) for i in range(len(X))])
        if self.crossfade_s > 0:
            upper = (self.first + buckets * self.bmin) * 60.0
            to_edge = upper - t
            near = (to_edge >= 0) & (to_edge < self.crossfade_s) & (t / 60.0 >= self.first)
            for i in np.flatnonzero(near):
                w = 0.5 * (1.0 - to_edge[i] / self.crossfade_s)
                logit[i] = (1 - w) * logit[i] + w * self._logit(xs[i], buckets[i] + 1)
        p = np.clip(1.0 / (1.0 + np.exp(-logit)), 1e-7, 1 - 1e-7)
        return 1.0 / (1.0 + np.exp(-np.log(p / (1 - p)) / self.t))

    def predict_one(self, x) -> float:
        return float(self.predict(np.asarray(x)[None, :])[0])
