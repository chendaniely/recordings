import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def test_every_compose_variable_is_documented_in_the_template():
    compose = (REPO / "docker" / "compose.yml").read_text(encoding="utf-8")
    template = (REPO / "docker" / "deploy.example.env").read_text(encoding="utf-8")
    used = set(re.findall(r"\$\{(\w+)", compose))
    documented = set(re.findall(r"^(\w+)=", template, re.M))
    assert used, "compose.yml uses no variables?"
    assert used <= documented, f"undocumented: {sorted(used - documented)}"


def test_the_template_holds_no_secret_names():
    template = (REPO / "docker" / "deploy.example.env").read_text(encoding="utf-8")
    for name in ("TOKEN", "API_KEY", "PASSWORD", "SECRET="):
        assert name not in template
