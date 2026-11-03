"""The DataChat Python class and mounting the API inside another FastAPI app."""

import asyncio
import importlib.util
import json
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from datachat_agent import DataChat, mount
from datachat_agent.server.services import ScanRefused
from datachat_agent.sql.validator import SQLValidationError
from tests.fakes import ScriptedChat, sql
from tests.integration.conftest import PG_READONLY, PG_WRITER

ROOT = Path(__file__).resolve().parents[2]
PLAN_NO_ITEMS = json.dumps({"need_items": False, "document": None, "item": None})


@pytest.fixture(scope="module")
def manual(tmp_path_factory) -> Path:
    spec = importlib.util.spec_from_file_location("make_pdfs", ROOT / "eval/datasets/make_pdfs.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    out = tmp_path_factory.mktemp("lib_pdfs")
    module.manual(out / "manual.pdf")
    return out / "manual.pdf"


def name() -> str:
    return f"t-{uuid.uuid4().hex[:8]}"


def test_library_database_and_documents(databases, manual):
    llm = ScriptedChat(
        [
            sql("SELECT COUNT(*) AS buyers FROM public.buyers"),
            "There are six buyers.",
            "documents",
            PLAN_NO_ITEMS,
            "Cartons show the style number [1].",
        ]
    )
    with DataChat(PG_READONLY, llm=llm, name=name()) as dc:
        assert dc.scan_schema()["tables"] == 8

        a = dc.ask("How many buyers?", session_id="lib-1")
        assert a.answer == "There are six buyers." and str(a) == a.answer
        assert a.rows == [{"buyers": 6}] and a.route == "database" and a.session_id == "lib-1"

        info = dc.add_pdf(manual)
        assert info.status == "ready" and info.pages == 2 and info.created
        again = dc.add_pdf(manual.read_bytes(), filename="copy.pdf")
        assert again.id == info.id and not again.created
        assert [d.filename for d in dc.documents()] == ["manual.pdf"]

        b = dc.ask("What does the manual say about carton marking?")
        assert b.route == "documents" and b.citations[0]["page"] == 2


def test_library_semantic_layer(databases):
    llm = ScriptedChat(
        [
            sql("SELECT department, AVG(salary_inr) FROM employees GROUP BY department"),
            sql("SELECT department, AVG(salary_inr) FROM employees GROUP BY department"),
            sql(
                "SELECT COUNT(*) AS n FROM purchase_orders WHERE status IN ('Open', "
                "'Partially Received')"
            ),
            "There are open POs.",
        ]
    )
    with DataChat(PG_READONLY, llm=llm, name=name()) as dc:
        dc.scan_schema()
        dc.hide("employees", "salary_inr")
        with pytest.raises(KeyError):
            dc.hide("no_such_table")
        refused = dc.ask("Average salary by department?")
        assert refused.rows == [] and "hidden" in refused.error

        dc.add_term("open PO", "a purchase order whose status is Open or Partially Received")
        dc.add_example(
            "Suppliers in Ludhiana", "select name from suppliers where city = 'Ludhiana'"
        )
        with pytest.raises(SQLValidationError):
            dc.add_example("bad", "DELETE FROM suppliers")
        dc.ask("How many open POs?")
        prompt = llm.prompt_text(2)
        assert "open PO: a purchase order" in prompt and "Suppliers in Ludhiana" in prompt


def test_library_async_api(databases):
    llm = ScriptedChat([sql("SELECT COUNT(*) AS n FROM public.suppliers"), "Eight suppliers."])

    async def main():
        with DataChat(PG_READONLY, llm=llm, name=name()) as dc:
            await dc.ascan_schema()
            return await dc.aask("How many suppliers?")

    assert asyncio.run(main()).rows == [{"n": 8}]


def test_library_refuses_a_user_that_can_write(databases):
    with DataChat(PG_WRITER, llm=ScriptedChat([]), name=name()) as dc:
        with pytest.raises(ScanRefused, match="can write"):
            dc.scan_schema()


def test_library_without_database_answers_from_documents_only(databases, manual):
    llm = ScriptedChat([PLAN_NO_ITEMS, "Use 5 ply cartons [1]."])
    with DataChat(llm=llm, name=name()) as dc:
        dc.add_pdf(manual)
        assert dc.ask("Which cartons?").route == "documents"


def test_closed_instance_refuses_work(databases):
    dc = DataChat(llm=ScriptedChat([]), name=name())
    dc.close()
    dc.close()  # twice is fine
    with pytest.raises(RuntimeError, match="closed"):
        dc.documents()


def test_library_secret_is_created_once(monkeypatch, tmp_path):
    from datachat_agent.client import _library_secret

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    first, second = _library_secret(), _library_secret()
    assert first == second and (tmp_path / ".config/datachat-agent/secret.key").exists()


def test_mount_inside_another_fastapi_app(databases):
    host = FastAPI()

    @host.get("/hello")
    def hello():
        return {"app": "host"}

    mount(host, prefix="/ai")
    with TestClient(host) as c:
        assert c.get("/hello").json() == {"app": "host"}
        assert c.get("/ai/v1/health").json()["status"] == "ok"
        assert c.get("/ai/v1/keys").status_code == 401
