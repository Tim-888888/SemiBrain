import pytest
from pydantic import ValidationError
from semibrain_business.knowledge import chunk_blocks, validate_path
from semibrain_business.parsing import Block, ParseResult
from semibrain_business.tools import register, result_state
from semibrain_conversation.auth import AuthInput, hasher, verify_password


def test_password_is_argon2id_and_preserves_password_case():
    encoded = hasher.hash("Example123")
    assert encoded.startswith("$argon2id$")
    assert verify_password(encoded, "Example123")
    assert not verify_password(encoded, "example123")


@pytest.mark.parametrize(
    "username", ["123456", "_demo", "qa-user", "name@example.com", "qa+demo@example.org"]
)
def test_username_accepts_documented_characters(username):
    form = AuthInput(username=username, password="Example123", challenge_id="test", answer="234AB")
    assert form.username == username


def test_username_trim_does_not_trim_password():
    form = AuthInput(
        username=" qa@example.org ", password=" Example123 ", challenge_id="test", answer="234AB"
    )
    assert form.username == "qa@example.org"
    assert form.password == " Example123 "


@pytest.mark.parametrize("username", ["abc", "has space", "path/name"])
def test_invalid_username_is_rejected_without_echoing_credentials(username):
    with pytest.raises(ValidationError):
        AuthInput(username=username, password="Example123", challenge_id="test", answer="234AB")


@pytest.mark.parametrize("path", ["../private.md", "/etc/file.md", "folder/../../x.md"])
def test_folder_traversal_is_rejected(path):
    with pytest.raises(ValueError):
        validate_path(path)


def test_chunks_preserve_numeric_and_location_data():
    parsed = ParseResult(
        status="staged",
        source_hash="f" * 64,
        blocks=[
            Block(kind="table", text="| stage | yield |\n| FT | 91.5% |", location={"page": 0})
        ],
    )
    chunks = chunk_blocks(parsed, "doc-id", "version-id")
    assert chunks[0]["location"]["page"] == 0
    assert "91.5%" in chunks[0]["text"]
    assert chunks[0]["document_id"] == "doc-id"


def test_tools_cannot_shadow_existing_names():
    with pytest.raises(ValueError, match="TOOL_NAME_CONFLICT"):
        register("business.search_lots", None, None, "shadow")


def test_empty_and_truncated_results_are_not_confused():
    assert result_state({"row_count": 0, "rows": []}) == "empty"
    assert result_state({"denominator": 0, "value": None}) == "empty"
    assert result_state({"row_count": 0, "rows": [], "truncated": True}) == "partial"
    assert result_state({"row_count": 3, "truncated": False}) == "complete"
