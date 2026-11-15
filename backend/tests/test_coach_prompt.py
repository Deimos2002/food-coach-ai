"""Prompt contract (v3: v2 rules plus explicit no-"intake" in titles)."""

from app.coach import service


def test_prompt_version_is_v7_and_file_loads():
    assert service.PROMPT_VERSION == "v7"
    assert service.PROMPT_PATH.name == "v7.md"
    assert service.load_prompt()


def test_prompt_wording_rules_and_neutral_examples():
    text = service.load_prompt()
    assert '"intake"' in text and '"apport"' in text and '"recorded"' in text
    assert '"recommend"' in text
    for brand in ("Red Bull", "Coca", "Nutella", "Pepsi"):
        assert brand not in text, brand


def test_prompt_v3_forbids_intake_in_titles():
    text = service.load_prompt()
    assert "including titles" in text
    assert "Energy intake on target" in text  # quoted only as a not-allowed example
