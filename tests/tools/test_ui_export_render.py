from pathlib import Path


def test_render_uses_wider_sidebar_width():
    render_script = (
        Path(__file__).resolve().parents[2] / "tools" / "ui-export" / "render.mjs"
    )

    contents = render_script.read_text()

    assert "--sidebar-width: 340px;" in contents
