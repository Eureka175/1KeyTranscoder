"""Generic scaling engine: 4K60 reference profiles -> EffectiveParams.

Driven entirely by x265_scaling.json. This engine resolves VALUES only:
it knows nothing about FFmpeg command-line syntax or x265 parameter
serialization (that is encoders/x265.py). No numerical tuning value is
hardcoded here; every ratio/threshold/clamp lives in the JSON config.

Scaling modes (selected per parameter by param_rules in the JSON):

    fixed       keep the base-profile value (default for unlisted params)
    fps         evaluate FR* expressions against source fps, optional cap
    sqrt_pixels scale by sqrt((W*H) / (refW*refH)), optional clamp
    pixel_rate  scale by (W*H*fps) / (refW*refH*refFps), optional clamp

Dynamic VBV (ob_ratio family) is resolved per profile + source class:

    dynamic_min    = OB * min_ratio
    dynamic_target = OB * target_ratio
    dynamic_max    = OB * max_ratio
    final_maxrate  = clamp(dynamic_target, dynamic_min, dynamic_max)
    bufsize        = round(final_maxrate * bufsize_factor)
"""

from __future__ import annotations

import math
from typing import Any

from .models import (
    EffectiveParams,
    ScalingContext,
    SourceClassification,
    SourceInfo,
)

# Behavior used when scaling is disabled ("enabled": false): exactly the
# legacy pre-scaling semantics (FPS evaluation of the lookahead /
# min-keyint fields with a 200-frame LA cap, static VBV).
LEGACY_RULES: dict[str, dict[str, Any]] = {
    "rc_lookahead": {"mode": "fps", "cap": 200},
    "gop_lookahead": {"mode": "fps", "cap": 200},
    "min_keyint": {"mode": "fps"},
}


def ceil_expression(
    expr: Any,
    fps: float,
    cap: int | None = None,
) -> int:
    """
    Convert numbers or FR* expressions to integer frames.

    For lookahead fields, the caller supplies a cap from
    x265_scaling.json (param_rules.*.cap). This means, with cap=200:
        FR*3 at 59.94 -> 180
        FR*3 at 119.88 -> 200, not 360
    """
    if isinstance(expr, bool):
        value = int(expr)
    elif isinstance(expr, int):
        value = expr
    elif isinstance(expr, float):
        value = int(math.ceil(expr))
    else:
        text = str(expr).strip().upper().replace(" ", "")
        if text.startswith("FR*"):
            factor = float(text[3:])
            value = int(math.ceil(fps * factor))
        else:
            value = int(math.ceil(float(text)))

    if cap is not None:
        value = min(value, cap)

    return max(1, value)


def _clamp_round(
    calculated: float,
    lo: Any,
    hi: Any,
) -> tuple[float, int]:
    clamped = calculated
    if lo is not None:
        clamped = max(float(lo), clamped)
    if hi is not None:
        clamped = min(float(hi), clamped)
    return clamped, max(1, int(math.floor(clamped + 0.5)))


class ScalingEngine:
    """
    Resolves base-profile values against SourceInfo and a
    SourceClassification into EffectiveParams. Iterates the encoder's
    parameter namespace (supplied by the caller as `param_order`) so it
    stays encoder-agnostic.
    """

    def __init__(self, scaling_config: dict[str, Any]) -> None:
        self.enabled = bool(scaling_config.get("enabled", True))

        ref = scaling_config.get("reference", {})
        self.ref_width = int(ref.get("width", 3840))
        self.ref_height = int(ref.get("height", 2160))
        self.ref_fps = float(ref.get("fps", 59.94))

        self.rules: dict[str, Any] = scaling_config.get(
            "param_rules", {}
        )
        self.vbv_rules: dict[str, Any] = scaling_config.get(
            "dynamic_vbv", {}
        )

        rc_rule = self.rules.get("rc_lookahead", {})
        gop_rule = self.rules.get("gop_lookahead", {})
        self.la_cap = int(rc_rule.get("cap", 200))
        self.gop_la_cap = int(gop_rule.get("cap", 200))

    def factors(self, src: SourceInfo) -> tuple[float, float, float]:
        ref_px = self.ref_width * self.ref_height
        px = src.width * src.height
        fps = src.fps if src.fps > 0 else self.ref_fps

        spatial = (
            math.sqrt(px / ref_px) if px > 0 and ref_px > 0 else 1.0
        )
        temporal = fps / self.ref_fps if self.ref_fps > 0 else 1.0
        pixel_rate = (
            (px * fps) / (ref_px * self.ref_fps)
            if px > 0 and ref_px > 0 and self.ref_fps > 0
            else 1.0
        )
        return spatial, temporal, pixel_rate

    def _dynamic_vbv(
        self,
        preset: str,
        src: SourceInfo,
        source_class: str,
    ) -> dict[str, Any] | None:
        """
        Source-relative VBV. Returns None (static base-profile VBV is
        kept) when scaling is disabled, no rule exists for this
        profile/class, or OB is unknown.
        """
        if not self.enabled or src.ob_kbps <= 0:
            return None

        rule = self.vbv_rules.get(preset, {}).get(source_class)
        if rule is None:
            return None

        min_ratio = float(rule["min_ratio"])
        target_ratio = float(rule["target_ratio"])
        max_ratio = float(rule["max_ratio"])
        bufsize_factor = float(rule.get("bufsize_factor", 3.0))

        dynamic_min = src.ob_kbps * min_ratio
        dynamic_target = src.ob_kbps * target_ratio
        dynamic_max = src.ob_kbps * max_ratio

        final = min(max(dynamic_target, dynamic_min), dynamic_max)
        final_kbps = max(1, int(math.floor(final + 0.5)))
        bufsize_kbps = max(
            1, int(math.floor(final_kbps * bufsize_factor + 0.5))
        )

        return {
            "mode": "dynamic_vbv",
            "ob_kbps": round(src.ob_kbps, 3),
            "source_class": source_class,
            "min_ratio": min_ratio,
            "target_ratio": target_ratio,
            "max_ratio": max_ratio,
            "dynamic_min_kbps": round(dynamic_min, 1),
            "dynamic_target_kbps": round(dynamic_target, 1),
            "dynamic_max_kbps": round(dynamic_max, 1),
            "final_maxrate_kbps": final_kbps,
            "bufsize_factor": bufsize_factor,
            "final_bufsize_kbps": bufsize_kbps,
        }

    def build(
        self,
        profile: dict[str, Any],
        preset: str,
        src: SourceInfo,
        classification: SourceClassification,
        param_order: dict[str, str],
        format_fixed: Any,
    ) -> EffectiveParams:
        """
        param_order: mapping of profile JSON key -> parameter name
                     (iteration order defines report/serialization order;
                     supplied by the encoder layer).
        format_fixed: callable(json_key, raw_value, fps) -> str | None
                     used for mode="fixed" values (encoder-specific
                     formatting lives in the encoder layer).
        """
        spatial, temporal, pixel_rate = self.factors(src)
        fps = src.fps if src.fps > 0 else self.ref_fps

        context = ScalingContext(
            reference_width=self.ref_width,
            reference_height=self.ref_height,
            reference_fps=self.ref_fps,
            spatial_factor=spatial,
            temporal_factor=temporal,
            pixel_rate_factor=pixel_rate,
            source_class=classification.source_class,
            normalized_ob=classification.normalized_ob,
        )

        rules = self.rules if self.enabled else LEGACY_RULES
        vbv = self._dynamic_vbv(
            preset, src, classification.source_class,
        )

        values: dict[str, str] = {}
        audit: dict[str, dict[str, Any]] = {}

        for json_key, xkey in param_order.items():
            if json_key not in profile:
                continue

            raw = profile[json_key]

            if (
                json_key in ("vbv_maxrate", "vbv_bufsize")
                and vbv is not None
            ):
                if json_key == "vbv_maxrate":
                    values[xkey] = str(vbv["final_maxrate_kbps"])
                else:
                    values[xkey] = str(vbv["final_bufsize_kbps"])
                continue

            rule = rules.get(json_key)
            mode = (
                rule.get("mode", "fixed")
                if isinstance(rule, dict)
                else "fixed"
            )

            if mode == "fps":
                calculated = ceil_expression(raw, fps)
                cap = rule.get("cap") if rule else None
                cap = int(cap) if cap else None
                final = min(calculated, cap) if cap else calculated
                values[xkey] = str(final)
                audit[xkey] = {
                    "mode": "fps",
                    "base": raw,
                    "fps": round(fps, 6),
                    "calculated": calculated,
                    "cap": cap,
                    "final": final,
                }

            elif mode in ("sqrt_pixels", "pixel_rate"):
                factor = (
                    spatial if mode == "sqrt_pixels" else pixel_rate
                )
                try:
                    base = float(raw)
                except (TypeError, ValueError):
                    base = None

                if base is None:
                    # Non-numeric base cannot be scaled; keep it fixed.
                    v = format_fixed(json_key, raw, fps)
                    if v is not None:
                        values[xkey] = v
                        audit[xkey] = {
                            "mode": mode,
                            "error": "non-numeric base, kept fixed",
                            "final": v,
                        }
                    continue

                calculated = base * factor
                clamped, final = _clamp_round(
                    calculated,
                    rule.get("min"),
                    rule.get("max"),
                )

                values[xkey] = str(final)
                audit[xkey] = {
                    "mode": mode,
                    "base": base,
                    "factor": round(factor, 6),
                    "calculated": round(calculated, 3),
                    "min": rule.get("min"),
                    "max": rule.get("max"),
                    "final": final,
                }

            else:
                v = format_fixed(json_key, raw, fps)
                if v is None:
                    continue
                values[xkey] = v

        if vbv is not None:
            audit["vbv-maxrate"] = vbv
            audit["vbv-bufsize"] = {
                "mode": "dynamic_vbv",
                "bufsize_factor": vbv["bufsize_factor"],
                "final": vbv["final_bufsize_kbps"],
            }
        else:
            audit["vbv-maxrate"] = {
                "mode": "static",
                "final": values.get("vbv-maxrate", ""),
                "note": (
                    "base-profile VBV kept (scaling disabled, no rule "
                    "for this class, or OB unknown)"
                ),
            }

        return EffectiveParams(
            values=values,
            audit=audit,
            context=context,
        )
