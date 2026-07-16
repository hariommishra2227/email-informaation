"""Smoke checks that Streamlit pages have visible entrypoints."""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_all_streamlit_pages_render_without_blank_output() -> None:
    """Every page should define and call a render entrypoint with a visible title."""
    expected_titles = {
        "app.py": "Dashboard",
        "pages/customer_registry.py": "Customer Registry",
        "pages/manual_extraction.py": "Manual Extraction",
        "pages/Outlook Connector.py": "Customer Email Extraction",
        "pages/settings.py": "Settings",
    }

    for relative_path, title in expected_titles.items():
        source = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")
        assert f'st.title("{title}")' in source
        assert "st.error(" in source
        if relative_path == "app.py":
            assert "if __name__ == \"__main__\":" in source
            assert "main()" in source
        else:
            assert "def render_page() -> None:" in source
            assert "if __name__ == \"__main__\":" in source
            assert "render_page()" in source
