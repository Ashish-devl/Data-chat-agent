"""Load the made-up garment-factory mini-ERP used for tests, demos and the evaluation set.

Every name, number and person in this dataset is invented. It creates:
  database erp_demo with 8 tables
  user erp_readonly / erp_readonly  (SELECT only: the way customers should connect)
  user erp_writer / erp_writer      (can write: DataChat Agent must refuse it)

Usage:
  python eval/datasets/erp_demo.py postgresql://datachat:datachat@localhost:5432/postgres
  python eval/datasets/erp_demo.py mysql://root:rootpass@localhost:3307/mysql
"""

import datetime as dt
import random
import sys
from decimal import Decimal

from sqlalchemy import (
    Column,
    Date,
    ForeignKey,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    create_engine,
    insert,
    text,
)
from sqlalchemy.engine import make_url

DB = "erp_demo"
TODAY = dt.date(2026, 6, 1)  # fixed so the data never changes

metadata = MetaData()

buyers = Table(
    "buyers",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(100), nullable=False),
    Column("country", String(50), nullable=False),
    Column("payment_terms_days", Integer, nullable=False),
)
suppliers = Table(
    "suppliers",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(100), nullable=False),
    Column("city", String(50), nullable=False),
    Column("category", String(30), nullable=False),
)
materials = Table(
    "materials",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("code", String(20), nullable=False, unique=True),
    Column("name", String(100), nullable=False),
    Column("category", String(30), nullable=False),
    Column("unit", String(10), nullable=False),
    Column("supplier_id", Integer, ForeignKey("suppliers.id"), nullable=False),
)
styles = Table(
    "styles",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("style_no", String(30), nullable=False, unique=True),
    Column("buyer_id", Integer, ForeignKey("buyers.id"), nullable=False),
    Column("description", String(200), nullable=False),
    Column("season", String(10), nullable=False),
    Column("fob_price_usd", Numeric(10, 2), nullable=False),
    Column("order_qty", Integer, nullable=False),
)
purchase_orders = Table(
    "purchase_orders",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("po_no", String(30), nullable=False, unique=True),
    Column("supplier_id", Integer, ForeignKey("suppliers.id"), nullable=False),
    Column("po_date", Date, nullable=False),
    Column("status", String(20), nullable=False),
)
purchase_order_lines = Table(
    "purchase_order_lines",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("po_id", Integer, ForeignKey("purchase_orders.id"), nullable=False),
    Column("material_id", Integer, ForeignKey("materials.id"), nullable=False),
    Column("style_id", Integer, ForeignKey("styles.id"), nullable=False),
    Column("qty", Numeric(12, 2), nullable=False),
    Column("rate", Numeric(10, 2), nullable=False),
    Column("amount", Numeric(14, 2), nullable=False),
)
production_orders = Table(
    "production_orders",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("style_id", Integer, ForeignKey("styles.id"), nullable=False),
    Column("line_no", Integer, nullable=False),
    Column("planned_qty", Integer, nullable=False),
    Column("produced_qty", Integer, nullable=False),
    Column("start_date", Date, nullable=False),
    Column("due_date", Date, nullable=False),
    Column("status", String(20), nullable=False),
)
employees = Table(
    "employees",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("emp_code", String(10), nullable=False, unique=True),
    Column("name", String(100), nullable=False),
    Column("department", String(30), nullable=False),
    Column("designation", String(50), nullable=False),
    Column("line_no", Integer),
    Column("joined_on", Date, nullable=False),
    Column("salary_inr", Integer, nullable=False),  # sensitive: hide in the semantic layer
    Column("phone", String(15), nullable=False),  # sensitive
)


def build_rows() -> dict[Table, list[dict]]:
    r = random.Random(42)
    rows: dict[Table, list[dict]] = {}

    rows[buyers] = [
        {"id": i, "name": n, "country": c, "payment_terms_days": t}
        for i, (n, c, t) in enumerate(
            [
                ("Northwind Apparel", "United Kingdom", 60),
                ("Bluepeak Retail", "Germany", 90),
                ("Coral & Finch", "United States", 45),
                ("Maple Street Kids", "Canada", 60),
                ("Lumen Basics", "Netherlands", 75),
                ("Saffron Lane", "Australia", 30),
            ],
            start=1,
        )
    ]
    rows[suppliers] = [
        {"id": i, "name": n, "city": c, "category": k}
        for i, (n, c, k) in enumerate(
            [
                ("Shree Ganesh Knits", "Tiruppur", "Fabric"),
                ("Vardhan Spinners", "Ludhiana", "Yarn"),
                ("Apex Trims Co", "Delhi", "Trims"),
                ("Kaveri Dye House", "Erode", "Processing"),
                ("Orbit Labels", "Mumbai", "Trims"),
                ("Sutlej Fabrics", "Ludhiana", "Fabric"),
                ("Pioneer Cartons", "Faridabad", "Packing"),
                ("Indus Cotton Mills", "Coimbatore", "Fabric"),
            ],
            start=1,
        )
    ]
    material_defs = [
        ("FAB-001", "Cotton S/J 160 GSM", "Fabric", "KG", 1),
        ("FAB-002", "Cotton S/J 180 GSM", "Fabric", "KG", 8),
        ("FAB-003", "Cotton Rib 1x1", "Fabric", "KG", 6),
        ("FAB-004", "Poly Cotton Pique", "Fabric", "KG", 1),
        ("FAB-005", "Cotton Fleece 280 GSM", "Fabric", "KG", 8),
        ("YRN-001", "30s Combed Cotton Yarn", "Yarn", "KG", 2),
        ("TRM-001", "Main Label Woven", "Trims", "PCS", 5),
        ("TRM-002", "Care Label Printed", "Trims", "PCS", 5),
        ("TRM-003", "Polybag 12x16", "Packing", "PCS", 7),
        ("TRM-004", "Export Carton 5 Ply", "Packing", "PCS", 7),
        ("TRM-005", "Button 18L", "Trims", "PCS", 3),
        ("PRC-001", "Reactive Dyeing", "Processing", "KG", 4),
    ]
    rows[materials] = [
        {"id": i, "code": c, "name": n, "category": k, "unit": u, "supplier_id": s}
        for i, (c, n, k, u, s) in enumerate(material_defs, start=1)
    ]
    descriptions = [
        "Crew Neck Tee",
        "Polo Shirt",
        "Hooded Sweatshirt",
        "Kids Romper",
        "V Neck Tee",
        "Jogger Pants",
        "Tank Top",
        "Long Sleeve Tee",
    ]
    rows[styles] = []
    for i in range(1, 25):
        season = r.choice(["SS26", "AW26", "SS27"])
        rows[styles].append(
            {
                "id": i,
                "style_no": f"{season[-2:]}{chr(64 + (i % 6) + 1)}{1000 + i * 37}",
                "buyer_id": r.randint(1, 6),
                "description": r.choice(descriptions),
                "season": season,
                "fob_price_usd": Decimal(r.randint(250, 1800)) / 100,
                "order_qty": r.choice([1200, 2400, 3600, 5000, 7500, 10000]),
            }
        )
    rows[purchase_orders], rows[purchase_order_lines] = [], []
    line_id = 1
    for i in range(1, 41):
        supplier_id = r.randint(1, 8)
        po_date = TODAY - dt.timedelta(days=r.randint(5, 200))
        status = r.choices(["Open", "Partially Received", "Closed", "Cancelled"], [4, 3, 6, 1])[0]
        rows[purchase_orders].append(
            {
                "id": i,
                "po_no": f"PO/26-27/{4000 + i}",
                "supplier_id": supplier_id,
                "po_date": po_date,
                "status": status,
            }
        )
        options = [m for m in rows[materials] if m["supplier_id"] == supplier_id] or rows[materials]
        for _ in range(r.randint(1, 4)):
            m = r.choice(options)
            qty = Decimal(r.randint(50, 3000))
            rate = (
                Decimal(r.randint(5, 600)) if m["unit"] == "KG" else Decimal(r.randint(1, 40)) / 2
            )
            rows[purchase_order_lines].append(
                {
                    "id": line_id,
                    "po_id": i,
                    "material_id": m["id"],
                    "style_id": r.randint(1, 24),
                    "qty": qty,
                    "rate": rate,
                    "amount": qty * rate,
                }
            )
            line_id += 1
    rows[production_orders] = []
    for i in range(1, 31):
        start = TODAY - dt.timedelta(days=r.randint(0, 60))
        due = start + dt.timedelta(days=r.randint(15, 45))
        planned = r.choice([1200, 2400, 3600, 5000])
        progress = r.uniform(0.2, 1.0) if due > TODAY else r.uniform(0.6, 1.0)
        produced = min(planned, int(planned * progress))
        if produced >= planned:
            status = "Completed"
        elif due < TODAY:
            status = "Delayed"
        else:
            status = "In Progress"
        rows[production_orders].append(
            {
                "id": i,
                "style_id": r.randint(1, 24),
                "line_no": r.randint(1, 8),
                "planned_qty": planned,
                "produced_qty": produced,
                "start_date": start,
                "due_date": due,
                "status": status,
            }
        )
    first = [
        "Arjun",
        "Meena",
        "Ravi",
        "Sunita",
        "Karan",
        "Divya",
        "Imran",
        "Lakshmi",
        "Vikram",
        "Pooja",
        "Suresh",
        "Anita",
        "Manoj",
        "Rekha",
        "Deepak",
        "Kavita",
    ]
    last = ["Sharma", "Iyer", "Verma", "Nair", "Gupta", "Reddy", "Khan", "Patel", "Das", "Rao"]
    roles = [
        ("Production", "Operator"),
        ("Production", "Line Supervisor"),
        ("Quality", "Checker"),
        ("Merchandising", "Merchandiser"),
        ("Stores", "Store Keeper"),
        ("Finance", "Accountant"),
    ]
    rows[employees] = []
    for i in range(1, 61):
        dept, title = r.choices(roles, [10, 2, 3, 2, 1, 1])[0]
        rows[employees].append(
            {
                "id": i,
                "emp_code": f"E{1000 + i}",
                "name": f"{r.choice(first)} {r.choice(last)}",
                "department": dept,
                "designation": title,
                "line_no": r.randint(1, 8) if dept in ("Production", "Quality") else None,
                "joined_on": TODAY - dt.timedelta(days=r.randint(30, 3000)),
                "salary_inr": r.randint(14000, 90000),
                "phone": f"9{r.randint(100000000, 999999999)}",
            }
        )
    return rows


def load(admin_url: str) -> None:
    url = make_url(admin_url)
    backend = url.get_backend_name()
    dialect = "postgresql" if backend in ("postgres", "postgresql") else "mysql"
    driver = "postgresql+psycopg" if dialect == "postgresql" else "mysql+pymysql"
    admin = create_engine(url.set(drivername=driver), isolation_level="AUTOCOMMIT")

    with admin.connect() as conn:
        if dialect == "postgresql":
            conn.execute(text(f"DROP DATABASE IF EXISTS {DB} WITH (FORCE)"))
            conn.execute(text(f"CREATE DATABASE {DB}"))
            for user in ("erp_readonly", "erp_writer"):
                exists = conn.execute(
                    text("SELECT 1 FROM pg_roles WHERE rolname = :u"), {"u": user}
                )
                if exists.scalar() is None:
                    conn.execute(text(f"CREATE ROLE {user} LOGIN PASSWORD '{user}'"))
        else:
            conn.execute(text(f"DROP DATABASE IF EXISTS {DB}"))
            conn.execute(text(f"CREATE DATABASE {DB} CHARACTER SET utf8mb4"))
            for user in ("erp_readonly", "erp_writer"):
                conn.execute(text(f"CREATE USER IF NOT EXISTS '{user}'@'%' IDENTIFIED BY '{user}'"))
    admin.dispose()

    target = create_engine(url.set(drivername=driver, database=DB))
    metadata.create_all(target)
    data = build_rows()
    with target.begin() as conn:
        for table in metadata.sorted_tables:
            conn.execute(insert(table), data[table])
        if dialect == "postgresql":
            conn.execute(text(f"GRANT CONNECT ON DATABASE {DB} TO erp_readonly, erp_writer"))
            conn.execute(text("GRANT USAGE ON SCHEMA public TO erp_readonly, erp_writer"))
            conn.execute(text("GRANT SELECT ON ALL TABLES IN SCHEMA public TO erp_readonly"))
            conn.execute(text("GRANT ALL ON ALL TABLES IN SCHEMA public TO erp_writer"))
            conn.execute(text("ANALYZE"))
        else:
            conn.execute(text(f"GRANT SELECT ON {DB}.* TO 'erp_readonly'@'%'"))
            conn.execute(text(f"GRANT ALL ON {DB}.* TO 'erp_writer'@'%'"))
    target.dispose()
    counts = ", ".join(f"{t.name} {len(data[t])}" for t in metadata.sorted_tables)
    print(f"Loaded {DB} ({dialect}): {counts}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    load(sys.argv[1])
