"""Thirteen more made-up PDFs for the evaluation set (20 in all with make_pdfs.py).

They test harder cases: a table that runs onto page 2, GST rows to skip, a USD export invoice,
a three-page manual, contract clauses, and documents that are not invoices at all.
Every company, person and number is invented.
"""

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table

from eval.datasets.make_pdfs import GRID, STYLES, invoice

BUYER = "Knitwell Demo Mills"


def _doc(path: Path, title: str, story: list) -> None:
    SimpleDocTemplate(str(path), pagesize=A4, title=title).build(story)


def _sections(title: str, sections: list[tuple[str, str]], breaks: set[int]) -> list:
    story = [Paragraph(title, STYLES["Title"])]
    for i, (heading, body) in enumerate(sections):
        if i in breaks:
            story.append(PageBreak())
        story += [Paragraph(heading, STYLES["Heading2"]), Paragraph(body, STYLES["BodyText"])]
    return story


def tax_invoice(path: Path) -> None:
    """GST lines and totals below the items must not become line items."""
    lines = [
        ("Reactive Dyeing", "998821", 2400, "KG", 92.00),
        ("Enzyme Wash", "998821", 2400, "KG", 18.50),
    ]
    rows = [["Sr", "Item Description", "HSN", "Qty", "UOM", "Rate", "Taxable Value"]]
    taxable = 0.0
    for i, (d, hsn, q, u, r) in enumerate(lines, 1):
        taxable += q * r
        rows.append([str(i), d, hsn, f"{q:,}", u, f"{r:,.2f}", f"{q * r:,.2f}"])
    gst = round(taxable * 0.025, 2)
    rows += [
        ["", "CGST 2.5%", "", "", "", "", f"{gst:,.2f}"],
        ["", "SGST 2.5%", "", "", "", "", f"{gst:,.2f}"],
        ["", "Grand Total", "", "", "", "", f"{taxable + 2 * gst:,.2f}"],
    ]
    _doc(
        path,
        "Invoice KD/2026/077",
        [
            Paragraph("<b>Kaveri Dye House</b>, Erode", STYLES["Title"]),
            Paragraph("TAX INVOICE", STYLES["Heading2"]),
            Paragraph("Invoice No: KD/2026/077", STYLES["Normal"]),
            Paragraph("Invoice Date: 18-07-2026", STYLES["Normal"]),
            Paragraph(f"Bill To: {BUYER}", STYLES["Normal"]),
            Spacer(1, 6 * mm),
            Table(rows, style=GRID),
            Paragraph("GSTIN of supplier: 33ABCDE1234F1Z5", STYLES["Normal"]),
        ],
    )


TRIMS = [
    "Button 18L",
    "Button 24L",
    "Metal Zip 15 cm",
    "Metal Zip 20 cm",
    "Main Label Woven",
    "Size Label",
    "Care Label Printed",
    "Hang Tag",
    "Barcode Sticker",
    "Elastic 25 mm",
]


def long_invoice(path: Path) -> None:
    """40 lines: the table continues on page 2 with its header repeated."""
    rows = [["S.No", "Description", "Qty", "Unit", "Rate", "Amount"]]
    total = 0.0
    for i in range(1, 41):
        item = f"{TRIMS[(i - 1) % 10]} lot {((i - 1) // 10) + 1}"
        qty, rate = 1000 + i * 50, round(0.5 + (i % 7) * 0.35, 2)
        total += qty * rate
        rows.append([str(i), item, f"{qty:,}", "PCS", f"{rate:,.2f}", f"{qty * rate:,.2f}"])
    _doc(
        path,
        "Invoice ATC-5502",
        [
            Paragraph("<b>Apex Trims Co</b>, Delhi", STYLES["Title"]),
            Paragraph("TAX INVOICE", STYLES["Heading2"]),
            Paragraph("Invoice No: ATC-5502", STYLES["Normal"]),
            Paragraph("Invoice Date: 22-07-2026", STYLES["Normal"]),
            Paragraph(f"Bill To: {BUYER}", STYLES["Normal"]),
            Spacer(1, 4 * mm),
            Table(rows, style=GRID, repeatRows=1),
            Paragraph(f"<b>Grand Total: INR {total:,.2f}</b>", STYLES["Normal"]),
        ],
    )


def export_invoice(path: Path) -> None:
    rows = [
        ["Style", "Description", "Qty (PCS)", "Unit Price (USD)", "Amount (USD)"],
        ["26C1740", "Crew Neck Tee", "2,400", "3.85", "9,240.00"],
        ["27B1703", "Kids Romper", "1,200", "5.10", "6,120.00"],
    ]
    _doc(
        path,
        "Commercial Invoice EXP/26/014",
        [
            Paragraph(f"<b>{BUYER}</b>, Tiruppur", STYLES["Title"]),
            Paragraph("COMMERCIAL INVOICE", STYLES["Heading2"]),
            Paragraph("Invoice No: EXP/26/014", STYLES["Normal"]),
            Paragraph("Invoice Date: 05-08-2026", STYLES["Normal"]),
            Paragraph("Consignee: Northwind Apparel, London, United Kingdom", STYLES["Normal"]),
            Paragraph(
                "Terms: FOB Chennai. Payment by letter of credit at 60 days.", STYLES["Normal"]
            ),
            Spacer(1, 4 * mm),
            Table(rows, style=GRID),
            Paragraph("<b>Total: USD 15,360.00</b>", STYLES["Normal"]),
        ],
    )


def quality_manual(path: Path) -> None:
    _doc(
        path,
        "Bluepeak Retail Quality Manual",
        _sections(
            "Bluepeak Retail Supplier Quality Manual",
            [
                (
                    "1. Final Inspection",
                    "Final inspection uses AQL 2.5 for major defects and AQL 4.0 "
                    "for minor defects. Critical defects are never accepted.",
                ),
                (
                    "2. Measurements",
                    "Allowed tolerance is plus or minus 1 cm on chest and body "
                    "length, and plus or minus 0.5 cm on sleeve length.",
                ),
                (
                    "3. Shade Approval",
                    "Shade bands must be approved by Bluepeak before bulk cutting. "
                    "The colour difference to the approved standard may not exceed Delta E 1.0.",
                ),
                (
                    "4. Wash Tests",
                    "After three home washes at 40 degrees, shrinkage may not exceed 5% "
                    "in length and 3% in width.",
                ),
                (
                    "5. Labels",
                    "The care label is sewn into the left side seam, 8 cm above the hem.",
                ),
                (
                    "6. Needle Detection",
                    "Every garment passes a needle detector set to 1.2 mm "
                    "ferrous. Broken needles are logged and kept for 12 months.",
                ),
            ],
            breaks={2, 4},
        ),
    )


def contract(path: Path) -> None:
    _doc(
        path,
        "Supply Agreement Sutlej Fabrics",
        _sections(
            "Supply Agreement between Knitwell Demo Mills and Sutlej Fabrics",
            [
                ("1. Term", "This agreement runs from 1 August 2026 to 31 July 2027."),
                ("2. Payment", "Invoices are paid within 60 days of delivery."),
                (
                    "3. Late Delivery",
                    "For each week of late delivery the supplier pays a penalty of "
                    "1% of the order value, capped at 10% of the order value.",
                ),
                ("4. Quality", "Rejected lots are replaced within 15 days at the supplier's cost."),
                (
                    "5. Termination",
                    "Either party may end this agreement with 90 days written notice.",
                ),
                ("6. Governing Law", "Disputes go to the courts of Ludhiana, India."),
            ],
            breaks={3},
        ),
    )


def purchase_order(path: Path) -> None:
    rows = [
        ["S.No", "Description", "Qty", "Unit", "Rate", "Amount"],
        ["1", "Cotton Fleece 280 GSM", "1,500", "KG", "465.00", "697,500.00"],
        ["2", "Cotton S/J 160 GSM", "2,000", "KG", "409.00", "818,000.00"],
    ]
    _doc(
        path,
        "Purchase Order PO/26-27/4101",
        [
            Paragraph(f"<b>{BUYER}</b>, Tiruppur", STYLES["Title"]),
            Paragraph("PURCHASE ORDER", STYLES["Heading2"]),
            Paragraph("PO No: PO/26-27/4101", STYLES["Normal"]),
            Paragraph("PO Date: 02-08-2026", STYLES["Normal"]),
            Paragraph("Supplier: Indus Cotton Mills, Coimbatore", STYLES["Normal"]),
            Paragraph("Deliver by: 30-08-2026", STYLES["Normal"]),
            Spacer(1, 4 * mm),
            Table(rows, style=GRID),
            Paragraph("<b>PO Value: INR 1,515,500.00</b>", STYLES["Normal"]),
        ],
    )


def packing_list(path: Path) -> None:
    rows = [
        ["Style", "Cartons", "Pcs per Carton", "Gross Weight (kg)", "Net Weight (kg)"],
        ["26C1740", "80", "30", "992", "880"],
        ["27B1703", "40", "30", "496", "440"],
    ]
    _doc(
        path,
        "Packing List EXP/26/014",
        [
            Paragraph(f"<b>{BUYER}</b>", STYLES["Title"]),
            Paragraph("PACKING LIST for invoice EXP/26/014", STYLES["Heading2"]),
            Paragraph("Port of discharge: Felixstowe, United Kingdom", STYLES["Normal"]),
            Spacer(1, 4 * mm),
            Table(rows, style=GRID),
            Paragraph(
                "Total: 120 cartons, gross weight 1,488 kg, net weight 1,320 kg.", STYLES["Normal"]
            ),
        ],
    )


def test_report(path: Path) -> None:
    rows = [
        ["Test", "Requirement", "Result", "Verdict"],
        ["Fabric weight (GSM)", "160 +/- 5", "162", "Pass"],
        ["Shrinkage, length", "max 5%", "4.2%", "Pass"],
        ["Shrinkage, width", "max 3%", "3.6%", "Fail"],
        ["Colour fastness to washing", "min grade 3-4", "grade 4", "Pass"],
    ]
    _doc(
        path,
        "Test Report TR-2026-552",
        [
            Paragraph("Indus Textile Testing Lab, Coimbatore", STYLES["Title"]),
            Paragraph("TEST REPORT TR-2026-552", STYLES["Heading2"]),
            Paragraph(
                f"Customer: {BUYER}. Sample: Cotton S/J 160 GSM from Shree Ganesh Knits.",
                STYLES["Normal"],
            ),
            Paragraph("Report date: 12-08-2026", STYLES["Normal"]),
            Spacer(1, 4 * mm),
            Table(rows, style=GRID),
            Paragraph(
                "<b>Overall verdict: FAIL (width shrinkage above limit)</b>", STYLES["Normal"]
            ),
        ],
    )


def trims_price_list(path: Path) -> None:
    rows = [
        ["Item", "Unit", "Price"],
        ["Button 18L", "PCS", "0.85"],
        ["Metal Zip 15 cm", "PCS", "6.40"],
        ["Main Label Woven", "PCS", "1.75"],
        ["Hang Tag", "PCS", "0.60"],
    ]
    _doc(
        path,
        "Apex Trims Price List",
        [
            Paragraph("Apex Trims Co, Delhi", STYLES["Title"]),
            Paragraph(
                "Trims Price List, valid from 01-08-2026 (prices in INR)", STYLES["Heading2"]
            ),
            Table(rows, style=GRID),
        ],
    )


def credit_note(path: Path) -> None:
    _doc(
        path,
        "Credit Note CN-0042",
        [
            Paragraph("<b>Shree Ganesh Knits</b>, Tiruppur", STYLES["Title"]),
            Paragraph("CREDIT NOTE CN-0042", STYLES["Heading2"]),
            Paragraph("Date: 20-08-2026", STYLES["Normal"]),
            Paragraph(f"To: {BUYER}. Against invoice SGK/1187.", STYLES["Normal"]),
            Paragraph(
                "Reason: short supply of 25 KG Cotton S/J 160 GSM at INR 438.00 per KG.",
                STYLES["Normal"],
            ),
            Paragraph("<b>Credit amount: INR 10,950.00</b>", STYLES["Normal"]),
        ],
    )


def challan(path: Path) -> None:
    rows = [
        ["S.No", "Description", "Qty", "Unit", "Cartons"],
        ["1", "Main Label Woven", "25,000", "PCS", "18"],
        ["2", "Care Label Printed", "25,000", "PCS", "12"],
    ]
    _doc(
        path,
        "Delivery Challan DC-3307",
        [
            Paragraph("Orbit Labels, Mumbai", STYLES["Title"]),
            Paragraph("DELIVERY CHALLAN DC-3307", STYLES["Heading2"]),
            Paragraph("Date: 29-06-2026. Against invoice OL/INV/0093.", STYLES["Normal"]),
            Paragraph("Transporter: Swift Cargo. Vehicle: MH 04 AB 1234.", STYLES["Normal"]),
            Table(rows, style=GRID),
            Paragraph(
                "Total cartons: 30. Received by: Stores, Knitwell Demo Mills.", STYLES["Normal"]
            ),
        ],
    )


def make_more(out: Path) -> dict[str, Path]:
    out.mkdir(parents=True, exist_ok=True)
    simple = [
        (
            "invoice_SF-2026-118.pdf",
            (
                "SF/2026/118",
                "10-07-2026",
                "Sutlej Fabrics",
                "Ludhiana",
                BUYER,
                ["S.No", "Description", "Qty", "Unit", "Rate", "Amount"],
                [("Cotton Rib 1x1", 640, "KG", 448.00), ("Cotton S/J 180 GSM", 900, "KG", 396.50)],
            ),
        ),
        (
            "invoice_VS-7781.pdf",
            (
                "VS-7781",
                "15-07-2026",
                "Vardhan Spinners",
                "Ludhiana",
                BUYER,
                ["S.No", "Particulars", "Quantity", "UOM", "Price", "Value"],
                [
                    ("30s Combed Cotton Yarn", 2000, "KG", 305.00),
                    ("40s Combed Cotton Yarn", 1200, "KG", 338.00),
                ],
            ),
        ),
    ]
    files: dict[str, Path] = {}
    for name, args in simple:
        invoice(out / name, *args)
        files[name] = out / name
    for name, fn in [
        ("invoice_KD-2026-077.pdf", tax_invoice),
        ("invoice_ATC-5502.pdf", long_invoice),
        ("invoice_EXP-26-014.pdf", export_invoice),
        ("bluepeak_quality_manual.pdf", quality_manual),
        ("supply_agreement_sutlej.pdf", contract),
        ("po_PO-26-27-4101.pdf", purchase_order),
        ("packing_list_EXP-26-014.pdf", packing_list),
        ("test_report_TR-2026-552.pdf", test_report),
        ("apex_trims_price_list.pdf", trims_price_list),
        ("credit_note_CN-0042.pdf", credit_note),
        ("challan_DC-3307.pdf", challan),
    ]:
        fn(out / name)
        files[name] = out / name
    return files
