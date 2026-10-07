import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def brand_css():
    spec = importlib.util.spec_from_file_location("brand_css", REPO / "scripts" / "brand_css.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_theme_css_is_up_to_date():
    committed = (REPO / "packages/ui/frontend/src/theme.css").read_text(encoding="utf-8")
    assert committed == brand_css().render(REPO), "theme.css is stale: run `make brand`"


def contrast(a: str, b: str) -> float:
    def lum(h):
        c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    hi, lo = sorted([lum(a), lum(b)], reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_text_colours_meet_wcag_aa_in_both_modes():
    module = brand_css()
    for mode in ("light", "dark"):
        t = module.tokens(REPO, mode)
        for fg in ("foreground", "muted-foreground", "primary"):
            for bg in ("background", "card", "muted", "sidebar"):
                assert contrast(t[fg], t[bg]) >= 4.5, f"{mode}: {fg} on {bg}"
        assert contrast(t["primary-foreground"], t["primary"]) >= 4.5, f"{mode}: button text"


def test_orange_is_never_a_light_mode_text_colour():
    # why: 3.2:1 on white, so markers and outlines only
    assert contrast(brand_css().tokens(REPO, "light")["brand-orange"], "#FFFFFF") < 4.5
