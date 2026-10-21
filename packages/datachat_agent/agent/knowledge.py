"""Business terms and worked examples for a connection, chosen for each question."""

import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from datachat_agent.server.models import GlossaryTerm, SqlExample
from datachat_agent.sql.generator import Example

ALL_TERMS_UP_TO = 15  # small glossaries are sent whole
MAX_EXAMPLES = 3


@dataclass
class Knowledge:
    terms: list[str] = field(default_factory=list)  # "term: definition"
    examples: list[Example] = field(default_factory=list)
    matched_definitions: list[str] = field(default_factory=list)  # terms named in the question


def term_text(term: str, definition: str) -> str:
    return f"{term}: {definition}"


def _mentions(question: str, term: str) -> bool:
    return re.search(rf"\b{re.escape(term.lower())}s?\b", question.lower()) is not None


async def load_knowledge(
    session: AsyncSession, connection_id, question: str, question_vector: list[float]
) -> Knowledge:
    k = Knowledge()
    terms = list(
        await session.scalars(
            select(GlossaryTerm).where(GlossaryTerm.connection_id == connection_id)
        )
    )
    if len(terms) > ALL_TERMS_UP_TO:
        ranked = await session.scalars(
            select(GlossaryTerm)
            .where(GlossaryTerm.connection_id == connection_id, GlossaryTerm.embedding.is_not(None))
            .order_by(GlossaryTerm.embedding.cosine_distance(question_vector))
            .limit(ALL_TERMS_UP_TO)
        )
        chosen = {t.id: t for t in ranked}
        # A term named in the question is always included, whatever its rank.
        chosen.update({t.id: t for t in terms if _mentions(question, t.term)})
        terms = list(chosen.values())
    k.terms = [term_text(t.term, t.definition) for t in terms]
    k.matched_definitions = [t.definition for t in terms if _mentions(question, t.term)]

    examples = await session.scalars(
        select(SqlExample)
        .where(SqlExample.connection_id == connection_id, SqlExample.embedding.is_not(None))
        .order_by(SqlExample.embedding.cosine_distance(question_vector))
        .limit(MAX_EXAMPLES)
    )
    k.examples = [Example(question=e.question, sql=e.sql) for e in examples]
    return k
