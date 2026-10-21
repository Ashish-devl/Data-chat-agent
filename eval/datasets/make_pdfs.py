"""Generate the made-up sample PDFs used for tests, demos and the evaluation set.

Every company, person, number and address here is invented. Output is deterministic.

Usage:
  python eval/datasets/make_pdfs.py <output folder>
"""

import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

STYLES = getSampleStyleSheet()
GRID = TableStyle(
    [
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
    ]
)

# (invoice no, date, seller, seller city, buyer, header labels, lines)
INVOICES = [
    (
        "KE/26-27/8231",
        "12-05-2026",
        "Kaveri Exports",
        "Erode",
        "Knitwell Demo Mills",
        ["S.No", "Description", "Qty", "Unit", "Rate", "Amount"],
        [
            ("Cotton S/J 160 GSM", 1200, "KG", 412.00),
            ("Cotton Rib 1x1", 300, "KG", 455.50),
            ("Reactive Dyeing", 1500, "KG", 95.00),
        ],
    ),
    (
        "SGK/1187",
        "03-06-2026",
        "Shree Ganesh Knits",
        "Tiruppur",
        "Knitwell Demo Mills",
        ["Sr", "Particulars", "Quantity", "UOM", "Price", "Value"],
        [
            ("Cotton S/J 160 GSM", 800, "KG", 438.00),
            ("Poly Cotton Pique", 450, "KG", 389.00),
        ],
    ),
    (
        "ICM-2026-0442",
        "21-06-2026",
        "Indus Cotton Mills",
        "Coimbatore",
        "Knitwell Demo Mills",
        ["#", "Item", "Qty", "Unit", "Rate", "Total"],
        [
            ("Cotton S/J 180 GSM", 950, "KG", 401.25),
            ("Cotton Fleece 280 GSM", 600, "KG", 472.00),
            ("Cotton S/J 160 GSM", 500, "KG", 405.00),
        ],
    ),
    (
        "OL/INV/0093",
        "28-06-2026",
        "Orbit Labels",
        "Mumbai",
        "Knitwell Demo Mills",
        ["S.No", "Description", "Qty", "Unit", "Rate", "Amount"],
        [
            ("Main Label Woven", 25000, "PCS", 1.80),
            ("Care Label Printed", 25000, "PCS", 0.95),
        ],
    ),
]

MANUAL = [
    (
        "1. Scope",
        "This manual sets out the packing, labelling and shipping rules that every "
        "supplier must follow for Northwind Apparel orders. It applies to all knitted and woven "
        "garments from season SS26 onward.",
    ),
    (
        "2. Garment Folding",
        "Fold tees to a finished size of 25 cm by 30 cm with the front facing "
        "up. Insert one tissue sheet for kidswear. Never use metal pins or clips.",
    ),
    (
        "3. Polybags",
        "Each garment goes into an individual polybag of at least 50 micron "
        "thickness, printed with the suffocation warning in English and French. Polybags must be "
        "recyclable LDPE.",
    ),
    (
        "4. Carton Marking",
        "Every export carton must show the style number, colour, size ratio, "
        "carton number in the form 1 of N, gross weight and net weight in kilograms, and the "
        "destination port. Marking must be printed in black ink on two opposite sides. Handwritten "
        "marks are rejected. Carton weight may not exceed 15 kg.",
    ),
    (
        "5. Carton Construction",
        "Use 5 ply export cartons with a bursting strength of at least "
        "16 kg per square centimetre. Seal with brown tape in an H pattern.",
    ),
    (
        "6. Shipping Documents",
        "Send the packing list, commercial invoice and inspection "
        "certificate by email 48 hours before the goods leave the factory.",
    ),
    (
        "7. Penalties",
        "Cartons with wrong or missing marks are charged a re-handling fee of "
        "USD 4 per carton. Repeated failures may lead to order cancellation.",
    ),
]


def invoice(path: Path, no, date, seller, city, buyer, header, lines) -> None:
    doc = SimpleDocTemplate(str(path), pagesize=A4, title=f"Invoice {no}")
    rows = [header]
    total = 0.0
    for i, (desc, qty, unit, rate) in enumerate(lines, start=1):
        amount = round(qty * rate, 2)
        total += amount
        rows.append([str(i), desc, f"{qty:,}", unit, f"{rate:,.2f}", f"{amount:,.2f}"])
    story = [
        Paragraph(f"<b>{seller}</b>, {city}", STYLES["Title"]),
        Paragraph("TAX INVOICE", STYLES["Heading2"]),
        Paragraph(f"Invoice No: {no}", STYLES["Normal"]),
        Paragraph(f"Invoice Date: {date}", STYLES["Normal"]),
        Paragraph(f"Bill To: {buyer}", STYLES["Normal"]),
        Spacer(1, 6 * mm),
        Table(rows, style=GRID, colWidths=[12 * mm, 62 * mm, 22 * mm, 16 * mm, 24 * mm, 30 * mm]),
        Spacer(1, 4 * mm),
        Paragraph(f"<b>Grand Total: INR {total:,.2f}</b>", STYLES["Normal"]),
        Paragraph("Payment terms: 45 days from invoice date.", STYLES["Normal"]),
    ]
    doc.build(story)


def manual(path: Path) -> None:
    doc = SimpleDocTemplate(str(path), pagesize=A4, title="Northwind Apparel Supplier Manual")
    story = [Paragraph("Northwind Apparel Supplier Packing Manual", STYLES["Title"])]
    for i, (heading, body) in enumerate(MANUAL):
        if i == 3:
            story.append(PageBreak())  # Carton Marking starts page 2
        story += [Paragraph(heading, STYLES["Heading2"]), Paragraph(body, STYLES["BodyText"])]
    doc.build(story)


def price_list(path: Path) -> None:
    doc = SimpleDocTemplate(str(path), pagesize=A4, title="Vardhan Spinners Price List")
    rows = [
        ["Count", "Description", "Unit", "Rate"],
        ["30s", "Combed Cotton Yarn", "KG", "298.00"],
        ["40s", "Combed Cotton Yarn", "KG", "331.50"],
        ["30s", "Organic Cotton Yarn", "KG", "362.00"],
    ]
    doc.build(
        [
            Paragraph("Vardhan Spinners, Ludhiana", STYLES["Title"]),
            Paragraph("Yarn Price List, valid from 01-07-2026", STYLES["Heading2"]),
            Table(rows, style=GRID),
        ]
    )


def scanned(path: Path) -> None:
    """A PDF with no text layer, like a scan: only drawn shapes."""
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=A4)
    for i in range(12):
        c.rect(30 * mm, (250 - i * 15) * mm, 150 * mm, 6 * mm, fill=1)
    c.save()


def make_all(out: Path) -> dict[str, Path]:
    out.mkdir(parents=True, exist_ok=True)
    files: dict[str, Path] = {}
    for inv in INVOICES:
        name = "invoice_" + inv[0].replace("/", "-") + ".pdf"
        invoice(out / name, *inv)
        files[name] = out / name
    for name, fn in [
        ("northwind_supplier_manual.pdf", manual),
        ("vardhan_price_list.pdf", price_list),
        ("scanned_delivery_note.pdf", scanned),
    ]:
        fn(out / name)
        files[name] = out / name
    return files


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    for name in make_all(Path(sys.argv[1])):
        print(name)
