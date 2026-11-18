"""The viewer's JS config is generated from the Python source. If this fails, run: python scripts/sync_viewer_config.py"""
from scripts.sync_viewer_config import OUT, render


def test_viewer_generated_files_are_current():
    for name, text in render().items():
        assert (OUT / name).read_text() == text, f"{name} is stale"
