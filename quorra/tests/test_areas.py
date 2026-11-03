from pathlib import Path

from gitfix import make_repo
from qindex import areas, build

FX = Path(__file__).parent / "fixtures"


def test_cap(tmp_path):
    files = {f"src/Service/S{i}Service.php": f"<?php class S{i}Service {{}}" for i in range(10)}
    repo, sha = make_repo(tmp_path, files)
    build.build_index("x", repo, "php", sha, tmp_path / "i.sqlite")
    assert len(areas.area_files(repo, sha, "php", tmp_path / "i.sqlite", cap=3)["layout"]) == 3
