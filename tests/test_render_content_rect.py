from render import content_rect_for_source


def test_landscape_source_letterboxed_vertically():
    # Matches the real sample video in this repo (638x480, ~4:3) -- fits
    # to full canvas width, letterboxed top/bottom. Cross-checked against
    # ffmpeg's own cropdetect on the real rendered output (crop=1080:812:
    # 0:552), which lands within 1px of this.
    content_x, content_y, content_width, content_height = (
        content_rect_for_source(638, 480)
    )

    assert content_width == 1080
    assert content_x == 0
    assert content_height < 1920
    # Vertically centered: equal black bar above and below.
    assert content_y == (1920 - content_height) // 2


def test_source_matching_canvas_aspect_ratio_has_no_letterboxing():
    content_x, content_y, content_width, content_height = (
        content_rect_for_source(1080, 1920)
    )

    assert (content_x, content_y) == (0, 0)
    assert (content_width, content_height) == (1080, 1920)


def test_portrait_source_narrower_than_canvas_letterboxed_horizontally():
    # A source taller/narrower than 9:16 (e.g. a phone video shot in a
    # tighter aspect ratio) should get horizontal, not vertical, bars.
    content_x, content_y, content_width, content_height = (
        content_rect_for_source(900, 1920)
    )

    assert content_height == 1920
    assert content_y == 0
    assert content_width < 1080
    assert content_x == (1080 - content_width) // 2


def test_invalid_source_dimensions_falls_back_to_full_canvas():
    assert content_rect_for_source(0, 0) == (0, 0, 1080, 1920)
    assert content_rect_for_source(-5, 480) == (0, 0, 1080, 1920)
