import pytest
from typer.testing import CliRunner

from jap import config
from jap.cli import app
from tests.conftest import ROOT

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROFILE_PATH", tmp_path / "profile" / "master_profile.yaml")
    monkeypatch.setattr(config, "EXAMPLE_PROFILE_DIR", ROOT / "examples" / "profile")
    for name in ("INBOX_DIR", "APPLICATIONS_DIR", "DATA_DIR"):
        monkeypatch.setattr(config, name, tmp_path / name.lower())
    return tmp_path


def test_init_copies_the_example_and_never_overwrites(isolated):
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0 and "created" in result.output
    profile = isolated / "profile" / "master_profile.yaml"
    assert "John Doe" in profile.read_text() and (isolated / "profile" / "tracks.yaml").exists()
    assert all((isolated / d).is_dir() for d in ("inbox_dir", "applications_dir", "data_dir"))

    profile.write_text("person: {name: Me}\n")
    again = runner.invoke(app, ["init"])
    assert again.exit_code == 0 and "left as is" in again.output
    assert profile.read_text() == "person: {name: Me}\n"


def test_init_fills_an_empty_folder(isolated):
    (isolated / "profile").mkdir()  # as Docker leaves a bind mount that didn't exist yet
    assert runner.invoke(app, ["init"]).exit_code == 0
    assert (isolated / "profile" / "master_profile.yaml").exists()


def test_commands_without_a_profile_point_to_init():
    result = runner.invoke(app, ["apply", "https://example.com/job"])
    assert result.exit_code == 2 and "jap init" in result.output
