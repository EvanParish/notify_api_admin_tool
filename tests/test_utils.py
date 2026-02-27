from app.utils import extract_placeholders, validate_recipient


def test_extract_placeholders_empty():
    assert extract_placeholders("") == []
    assert extract_placeholders(None) == []


def test_extract_placeholders_single():
    result = extract_placeholders("Hello ((name))")
    assert result == ["name"]


def test_extract_placeholders_multiple():
    result = extract_placeholders(
        "Hello ((first_name)) ((last_name)), your code is ((code))"
    )
    assert result == ["first_name", "last_name", "code"]


def test_extract_placeholders_duplicate():
    result = extract_placeholders("((name)) is ((name))")
    assert result == ["name"]


def test_extract_placeholders_with_spaces():
    result = extract_placeholders("Hello (( name )) (( greeting ))")
    assert result == ["name", "greeting"]


def test_extract_placeholders_no_placeholders():
    result = extract_placeholders("This is just plain text")
    assert result == []


def test_extract_placeholders_mixed_content():
    content = "Subject: Welcome ((title))\n\nDear ((first_name)) ((last_name)),\n\nYour account ((account_id)) is ready."
    result = extract_placeholders(content)
    assert result == ["title", "first_name", "last_name", "account_id"]


def test_extract_placeholders_preserve_order():
    result = extract_placeholders("((z)) ((a)) ((m))")
    assert result == ["z", "a", "m"]


def test_extract_placeholders_empty_placeholder():
    result = extract_placeholders("Hello (()) there")
    assert result == []


def test_validate_recipient_email_valid():
    assert validate_recipient("email", "user@example.com") is True
    assert validate_recipient("email", "test.user@domain.co.uk") is True
    assert validate_recipient("email", "a@b.c") is True


def test_validate_recipient_email_invalid():
    assert validate_recipient("email", "notanemail") is False
    assert validate_recipient("email", "") is False


def test_validate_recipient_sms_valid():
    assert validate_recipient("sms", "1234567890") is True
    assert validate_recipient("sms", "12345678901234") is True


def test_validate_recipient_sms_invalid():
    assert validate_recipient("sms", "123") is False
    assert validate_recipient("sms", "12345") is False
    assert validate_recipient("sms", "abcdefghij") is False
    assert validate_recipient("sms", "") is False
    assert validate_recipient("sms", "123-456-7890") is False


def test_validate_recipient_unknown_type():
    # Default behavior when type is not email
    assert validate_recipient("unknown", "1234567890") is True
    assert validate_recipient("unknown", "abc") is False
