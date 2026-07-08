"""Pipeline-critical settings are validated at save time — a typo'd filename
template used to save fine and then fail every ingest at the move step.
"""
import pytest
from pydantic import ValidationError

from dragontag.app.config import UserSettings


def test_defaults_are_valid():
    UserSettings()


def test_format_specs_in_templates_are_accepted():
    UserSettings(
        filename_template_single="{track:02d}. {title}.{ext}",
        filename_template_multidisc="{disc}-{track:02d}. {title} [{tracktotal}].{ext}",
        multidisc_folder_template="Disc {disc} of {disctotal}",
    )


def test_unknown_placeholder_is_rejected():
    with pytest.raises(ValidationError):
        UserSettings(filename_template_single="{track:02d} - {name}.{ext}")


def test_broken_brace_syntax_is_rejected():
    with pytest.raises(ValidationError):
        UserSettings(filename_template_multidisc="{track:02d. {title}.{ext}")


def test_disc_folder_template_unknown_placeholder_rejected():
    with pytest.raises(ValidationError):
        UserSettings(multidisc_folder_template="Disc {number}")


def test_score_threshold_bounds():
    with pytest.raises(ValidationError):
        UserSettings(score_threshold=1.5)
    with pytest.raises(ValidationError):
        UserSettings(score_threshold=-0.1)
    UserSettings(score_threshold=0.0)
    UserSettings(score_threshold=1.0)


def test_network_timeout_must_be_positive():
    with pytest.raises(ValidationError):
        UserSettings(network_timeout_seconds=0)
    with pytest.raises(ValidationError):
        UserSettings(network_timeout_seconds=-5)
