from visual_fx import (
    baseline_filters,
    coerce_fx_intensity,
    event_strength,
    fx_intensity_strength,
)


def test_coerce_fx_intensity_clamps_to_valid_range():
    assert coerce_fx_intensity(-1.0) == 0.0
    assert coerce_fx_intensity(5.0) == 2.0
    assert coerce_fx_intensity(0.5) == 0.5


def test_coerce_fx_intensity_defaults_on_garbage_input():
    assert coerce_fx_intensity(None) == 1.0
    assert coerce_fx_intensity("not-a-number") == 1.0
    assert coerce_fx_intensity(float("nan")) == 1.0


def test_fx_intensity_strength_preserves_normal_look_and_overdrives_upper_half():
    assert fx_intensity_strength(0.0) == 0.0
    assert fx_intensity_strength(0.5) == 0.5
    assert fx_intensity_strength(1.0) == 1.0
    assert fx_intensity_strength(1.5) == 1.8
    assert fx_intensity_strength(2.0) == 3.0


def test_semantic_effect_strength_uses_the_same_overdrive_curve():
    event = {"intensity": 0.4}

    assert round(event_strength(event, 1.0), 3) == 0.4
    assert round(event_strength(event, 1.5), 3) == 0.72
    assert round(event_strength(event, 2.0), 3) == 1.2


def test_default_intensity_matches_original_hardcoded_values():
    # intensity=1.0 must reproduce exactly what baseline_filters() returned
    # before the intensity parameter was added, for every energy tier --
    # a render with no manual adjustment must be unaffected by this change.
    assert baseline_filters("LOW", 1.0) == [
        "eq=contrast=1.0800:saturation=1.1200:brightness=0.0040",
        "unsharp=5:5:0.3200:3:3:0.1200",
    ]
    assert baseline_filters("PUNCHY", 1.0) == [
        "eq=contrast=1.1800:saturation=1.2800:brightness=0.0080",
        "unsharp=5:5:0.5200:3:3:0.1800",
        "vignette=PI/7.0000",
    ]
    assert baseline_filters("MAXIMUM", 1.0) == [
        "eq=contrast=1.4200:saturation=1.6200:brightness=0.0100:gamma=0.9600",
        "unsharp=5:5:0.8600:3:3:0.2600",
        "drawbox=x=0:y=0:w=iw:h=ih:color=black@0.0350:t=fill",
        "vignette=PI/4.2000",
    ]


def test_zero_intensity_neutralizes_the_color_grade():
    filters = baseline_filters("MAXIMUM", 0.0)

    assert filters[0] == "eq=contrast=1.0000:saturation=1.0000:brightness=0.0000:gamma=1.0000"
    assert filters[1] == "unsharp=5:5:0.0000:3:3:0.0000"
    # No vignette or darken box at zero intensity.
    assert not any("vignette" in f for f in filters)
    assert not any("drawbox" in f for f in filters)


def test_higher_intensity_exaggerates_contrast_and_strengthens_vignette():
    normal = baseline_filters("PUNCHY", 1.0)
    boosted = baseline_filters("PUNCHY", 2.0)
    strong = baseline_filters("PUNCHY", 1.5)

    assert "contrast=1.3240" in strong[0]
    assert "contrast=1.5400" in boosted[0]
    assert "contrast=1.1800" in normal[0]
    assert "unsharp=5:5:1.5000:3:3:0.5400" in boosted[1]

    # A smaller vignette denominator (PI/x) is the stronger effect in this
    # codebase's convention (confirmed against MAXIMUM's PI/4.2 vs.
    # PUNCHY's PI/7 baseline) -- higher effective strength reduces it.
    assert "vignette=PI/2.3333" in boosted[2]
    assert "vignette=PI/7.0000" in normal[2]
