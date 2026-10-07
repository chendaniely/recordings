import pytest

import recordings
from recordings.cli import main


def test_version():
    assert recordings.__version__ == "0.1.0"


def test_cli_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert "recordings 0.1.0" in capsys.readouterr().out
