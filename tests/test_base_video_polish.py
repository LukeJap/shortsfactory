from base_video_polish import (
    POLISH_PRESETS,
    PRODUCTION_POLISH_PRESET,
    normalize_polish_preset,
    polish_filter_chain,
    polish_filters,
)
from compare_base_video_polish import VARIANTS, base_filter_chain
from render import base_video_filter_chain, standard_portrait_filter_complex
from visual_fx import (
    build_filter_chain,
    build_semantic_filter_chain,
    semantic_event_filters,
)


def test_unknown_preset_normalizes_safely():
    assert normalize_polish_preset(None) == "OFF"
    assert normalize_polish_preset("unknown") == "OFF"
    assert normalize_polish_preset("warm-pop") == "WARM_POP"


def test_off_returns_no_eq_or_unsharp_filter():
    assert polish_filters("OFF") == []
    assert polish_filter_chain("OFF") == ""


def test_pop_contains_expected_values():
    assert polish_filters("POP") == [
        "eq=contrast=1.1000:brightness=0.0040:saturation=1.1800:gamma=1.0000",
        "colorbalance=rs=0.0400:rm=0.0400:rh=0.0200:bs=-0.0400:bm=-0.0400:bh=-0.0200",
        "unsharp=5:5:0.4500:3:3:0.0000",
    ]


def test_warm_pop_contains_exact_expected_values():
    assert polish_filters("WARM_POP") == [
        "eq=contrast=1.1400:brightness=0.0060:saturation=1.2400:gamma=0.9900",
        "colorbalance=rs=0.0800:rm=0.0800:rh=0.0400:bs=-0.0800:bm=-0.0800:bh=-0.0400",
        "unsharp=5:5:0.5500:3:3:0.0000",
    ]


def test_viral_pop_contains_exact_expected_values():
    assert polish_filters("VIRAL_POP") == [
        "eq=contrast=1.1800:brightness=0.0080:saturation=1.3000:gamma=0.9800",
        "colorbalance=rs=0.1200:rm=0.1200:rh=0.0600:bs=-0.1200:bm=-0.1200:bh=-0.0600",
        "unsharp=5:5:0.7000:3:3:0.0000",
    ]


def test_all_non_off_presets_use_no_chroma_sharpening():
    for preset in POLISH_PRESETS:
        if preset == "OFF":
            continue

        filters = polish_filters(preset)

        assert filters[-1].endswith(":3:3:0.0000")


def test_no_base_preset_contains_vignette():
    for preset in POLISH_PRESETS:
        assert "vignette" not in polish_filter_chain(preset)


def test_no_base_preset_contains_drawbox_or_tint():
    forbidden = {
        "drawbox",
        "denoise",
        "tint",
        "hqdn3d",
        "hue",
        "nlmeans",
    }

    for preset in POLISH_PRESETS:
        chain = polish_filter_chain(preset)
        assert not any(token in chain for token in forbidden)


def test_filter_output_is_deterministic():
    assert polish_filters("WARM_POP") == polish_filters("WARM_POP")
    assert polish_filter_chain("D") == polish_filter_chain("VIRAL_POP")


def test_comparison_variants_use_requested_filenames():
    assert VARIANTS == (
        ("A", "OFF", "A_OFF.mp4"),
        ("B", "POP", "B_POP.mp4"),
        ("C", "WARM_POP", "C_WARM_POP.mp4"),
        ("D", "VIRAL_POP", "D_VIRAL_POP.mp4"),
    )


def test_comparison_chain_uses_crop_polish_setsar_and_format():
    chain = base_filter_chain("VIRAL_POP")

    assert chain.startswith(
        "crop=if(gte(iw/ih\\,0.5625)\\,ih*0.5625\\,iw):"
        "if(gte(iw/ih\\,0.5625)\\,ih\\,iw*1.7778):"
        "(iw-ow)/2:(ih-oh)/2,"
        "scale=1080:1920:flags=bicubic,"
        "eq=contrast=1.1800:brightness=0.0080:saturation=1.3000:gamma=0.9800,"
    )
    assert "colorbalance=rs=0.1200:rm=0.1200:rh=0.0600:bs=-0.1200:bm=-0.1200:bh=-0.0600" in chain
    assert "unsharp=5:5:0.7000:3:3:0.0000" in chain
    assert chain.endswith(",setsar=1,format=yuv420p")


def test_production_base_video_finish_uses_viral_pop_polish():
    assert PRODUCTION_POLISH_PRESET == "VIRAL_POP"

    chain = base_video_filter_chain()

    assert chain.startswith(
        "eq=contrast=1.1800:brightness=0.0080:saturation=1.3000:gamma=0.9800,"
    )
    assert "colorbalance=rs=0.1200:rm=0.1200:rh=0.0600:bs=-0.1200:bm=-0.1200:bh=-0.0600" in chain
    assert "unsharp=5:5:0.7000:3:3:0.0000" in chain
    assert chain.endswith(",setsar=1,format=yuv420p")


def test_standard_portrait_filter_complex_reuses_shared_blurred_composition():
    plan = {
        "source_width": 1920,
        "source_height": 1080,
        "active_rect": {"x": 240, "y": 0, "width": 1440, "height": 1080},
        "content_x": 0,
        "content_y": 555,
        "content_width": 1080,
        "content_height": 810,
        "canvas_width": 1080,
        "canvas_height": 1920,
    }

    chain = standard_portrait_filter_complex(plan)

    assert chain.startswith("[0:v]crop=1440:1080:240:0[recap_active_src];")
    assert "split=2" in chain
    assert "gblur=sigma=" in chain
    assert "overlay=0:555[recap_out]" in chain
    assert "[recap_out]setsar=1,format=yuv420p[standard_out]" in chain
    assert chain.count("eq=contrast=1.1800") == 1


def test_existing_visual_fx_chain_keeps_baseline_and_semantic_events():
    events = [
        {
            "start": 1.0,
            "end": 1.2,
            "effect": "contrast_flash",
        }
    ]

    chain = build_filter_chain("PUNCHY", events, 1.0)

    assert chain.startswith(
        "eq=contrast=1.1800:saturation=1.2800:brightness=0.0080,"
        "unsharp=5:5:0.5200:3:3:0.1800,"
        "vignette=PI/7.0000,"
    )
    assert "eq=contrast='1.0000+0.0408*if(between(t,1.000" in chain
    assert "saturation='1.0000+0.0306*if(between(t,1.000" in chain
    assert "eval=frame" in chain
    assert "drawbox=x=0:y=0:w=iw:h=ih:color=white" not in chain
    assert chain.endswith(",format=yuv420p")


def test_production_semantic_chain_excludes_energy_baseline():
    events = [
        {
            "start": 1.0,
            "end": 1.2,
            "effect": "contrast_flash",
        }
    ]

    chain = build_semantic_filter_chain(events, 1.0, "PUNCHY")

    assert not chain.startswith("eq=contrast=1.1800:saturation=1.2800")
    assert "unsharp=5:5:0.5200:3:3:0.1800" not in chain
    assert "vignette=PI/7.0000" not in chain
    assert "eq=contrast='1.0000+0.0408*if(between(t,1.000" in chain
    assert "drawbox=x=0:y=0:w=iw:h=ih:color=white" not in chain
    assert chain.endswith(",format=yuv420p")


def test_zero_intensity_disables_production_semantic_fx():
    chain = build_semantic_filter_chain(
        [
            {
                "start": 1.0,
                "end": 1.2,
                "effect": "contrast_flash",
            }
        ],
        0.0,
    )

    assert chain == "format=yuv420p"


def test_semantic_event_filters_excludes_baseline_and_format():
    filters = semantic_event_filters(
        [
            {
                "start": 2.0,
                "end": 2.4,
                "effect": "desat_hit",
            }
        ]
    )

    assert len(filters) == 1
    chain = ",".join(filters)

    assert "eq=contrast='1.0000+0.0340*if(between(t,2.000,2.136)" in chain
    assert "saturation='1.0000-0.0136*if(between(t,2.000,2.136)" in chain
    assert "brightness='-0.0027*if(between(t,2.000,2.136)" in chain
    assert "eval=frame" in chain
    assert "hue=" not in chain
    assert "drawbox=" not in chain
    assert "format=yuv420p" not in chain
