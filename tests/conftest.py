import os
from pathlib import Path

import pytest

TEST_PROJECT = Path("Might Last Forever.logicx")
HAS_TEST_PROJECT = TEST_PROJECT.exists() and (TEST_PROJECT / "Resources" / "ProjectInformation.plist").exists()

HAS_VST3 = Path(os.environ.get("VST3_PATH", "C:/Program Files/Common Files/VST3")).exists()


def pytest_collection_modifyitems(config, items):
    skip_no_project = pytest.mark.skip(reason="test .logicx project not available")
    skip_no_vst3 = pytest.mark.skip(reason="VST3 plugins not available")

    for item in items:
        if "needs_test_project" in item.keywords and not HAS_TEST_PROJECT:
            item.add_marker(skip_no_project)
        if "needs_vst3" in item.keywords and not HAS_VST3:
            item.add_marker(skip_no_vst3)
