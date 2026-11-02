import pytest

from datachat_agent.core.pii import PiiMasker, StreamUnmasker


@pytest.mark.parametrize(
    ("value", "kind"),
    [
        ("ravi.k@example.com", "EMAIL"),
        ("a.b+c@d.co.in", "EMAIL"),
        ("9876543210", "PHONE"),
        ("98765-43210", "PHONE"),
        ("+91 98765 43210", "PHONE"),
        ("044 2345 6789", "PHONE"),
        ("4111 1111 1111 1111", "CARD"),  # passes the Luhn check
        ("2345 6789 0123", "AADHAAR"),
        ("ABCDE1234F", "PAN"),
    ],
)
def test_masks_personal_data(value, kind):
    m = PiiMasker()
    masked = m.mask_text(f"value: {value}.")
    assert value not in masked and f"[{kind}_1]" in masked
    assert m.unmask(masked) == f"value: {value}."


@pytest.mark.parametrize(
    "value",
    [
        "2026-05-12",  # a date
        "PO/26-27/4001",  # a document number
        "KE/26-27/8231",
        "4,94,400.00",  # an amount
        "1234 5678 9012 3456",  # card-shaped but fails the Luhn check
        "26C1740",  # a style number
    ],
)
def test_leaves_business_values_alone(value):
    assert PiiMasker().mask_text(value) == value


def test_same_value_gets_the_same_token_and_numbers_are_untouched():
    m = PiiMasker()
    out = m.mask([{"a": "x@y.com", "b": "x@y.com", "c": "z@y.com", "n": 9876543210}])
    assert out == [{"a": "[EMAIL_1]", "b": "[EMAIL_1]", "c": "[EMAIL_2]", "n": 9876543210}]
    assert m.masked_count == 2


def test_unknown_tokens_are_left_as_written():
    assert PiiMasker().unmask("see [PHONE_9]") == "see [PHONE_9]"


@pytest.mark.parametrize(
    "chunks",
    [
        ["Call [PHO", "NE_1] now"],
        ["Call [", "PHONE_1", "] now"],
        ["Call [PHONE_1] now"],
        ["C", "all ", "[PHONE_1", "]", " now"],
    ],
)
def test_stream_unmasker_never_splits_a_placeholder(chunks):
    m = PiiMasker()
    m.mask_text("9876543210")
    s = StreamUnmasker(m)
    pieces = [s.feed(c) for c in chunks] + [s.flush()]
    assert "".join(pieces) == "Call 9876543210 now"
    assert all("[PHONE" not in p for p in pieces)


def test_stream_unmasker_releases_ordinary_brackets():
    s = StreamUnmasker(PiiMasker())
    assert s.feed("see [1] and [2") + s.feed(" more text that is long enough") + s.flush() == (
        "see [1] and [2 more text that is long enough"
    )


def test_stream_unmasker_passthrough_without_masker():
    s = StreamUnmasker(None)
    assert s.feed("[PHO") == "[PHO" and s.flush() == ""
