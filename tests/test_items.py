import pytest

from datachat_agent.docs.extract import ExtractedTable
from datachat_agent.docs.items import TOTAL_ROW, map_header, parse_number, rows_from_table


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (
            ["S.No", "Description", "Qty", "Unit", "Rate", "Amount"],
            {1: "item", 2: "qty", 3: "unit", 4: "rate", 5: "amount"},
        ),
        (
            ["Sr", "Particulars", "Quantity", "UOM", "Price", "Value"],
            {1: "item", 2: "qty", 3: "unit", 4: "rate", 5: "amount"},
        ),
        # Units in brackets do not decide the column; "Unit Price" is a rate, not a unit.
        (
            ["Style", "Description", "Qty (PCS)", "Unit Price (USD)", "Amount (USD)"],
            {1: "item", 2: "qty", 3: "rate", 4: "amount"},
        ),
        (
            ["Sr", "Item Description", "HSN", "Qty", "UOM", "Rate", "Taxable Value"],
            {1: "item", 3: "qty", 4: "unit", 5: "rate", 6: "amount"},
        ),
    ],
)
def test_header_mapping(header, expected):
    assert map_header(header) == expected


def test_tables_that_are_not_line_items():
    assert map_header(["Test", "Requirement", "Result", "Verdict"]) is None
    assert map_header(["Style", "Cartons", "Pcs per Carton"]) is None


@pytest.mark.parametrize(
    ("text", "skip"),
    [
        ("CGST 2.5%", True),
        ("IGST 18%", True),
        ("GST @5%", True),
        ("Less: Discount", True),
        ("Freight charges", True),
        ("Grand Total", True),
        ("Round off", True),
        ("Cotton S/J 160 GSM", False),
        ("Taxi service", False),
        ("Address label", False),
    ],
)
def test_rows_that_are_not_goods(text, skip):
    assert bool(TOTAL_ROW.search(text)) is skip


def test_tax_rows_are_dropped_from_a_gst_invoice():
    table = ExtractedTable(
        1,
        [
            ["Sr", "Item Description", "HSN", "Qty", "UOM", "Rate", "Taxable Value"],
            ["1", "Reactive Dyeing", "998821", "2,400", "KG", "92.00", "220,800.00"],
            ["", "CGST 2.5%", "", "", "", "", "5,520.00"],
            ["", "Grand Total", "", "", "", "", "231,840.00"],
        ],
    )
    items = rows_from_table(table, map_header(table.rows[0]))
    assert [(i.item, float(i.amount)) for i in items] == [("Reactive Dyeing", 220800.0)]


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("1,200", "1200"),
        ("4,94,400.00", "494400.00"),
        ("INR 412.00", "412.00"),
        ("(100)", "-100"),
        ("$3.85", "3.85"),
        ("abc", None),
        ("", None),
    ],
)
def test_parse_number(text, value):
    result = parse_number(text)
    assert (str(result) if result is not None else None) == value
