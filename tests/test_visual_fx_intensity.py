from visual_fx import baseline_filters, coerce_fx_intensity


def test_coerce_fx_intensity_clamps_to_valid_range():
    assert coerce_fx_intensity(-1.0) == 0.0
    assert coerce_fx_intensity(5.0) == 2.0
    assert coerce_fx_intensity(0.5) == 0.5


def test_coerce_fx_intensity_defaults_on_garbage_input():
    assert coerce_fx_intensity(None) == 1.0
    assert coerce_fx_intensity("not-a-number") == 1.0
    assert coerce_fx_intensity(float("nan")) == 1.0


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

    assert "contrast=1.3600" in boosted[0]
    assert "contrast=1.1800" in normal[0]

    # A smaller vignette denominator (PI/x) is the stronger effect in this
    # codebase's convention (confirmed against MAXIMUM's PI/4.2 vs.
    # PUNCHY's PI/7 baseline) -- doubling intensity should halve it.
    assert "vignette=PI/3.5000" in boosted[2]
    assert "vignette=PI/7.0000" in normal[2]
