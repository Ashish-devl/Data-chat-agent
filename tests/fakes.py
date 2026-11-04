"""A scripted chat model that records every prompt it receives."""

from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult


def _prompt_kind(messages: list[BaseMessage]) -> str:
    system = str(messages[0].content) if messages else ""
    if system.startswith("Classify where the answer"):
        return "route"
    if system.startswith("You write one read-only SQL query"):
        return "sql"
    if system.startswith("Decide what to fetch from the uploaded documents"):
        return "plan"
    return "other"


def _response_kind(response: str | Exception) -> str | None:
    if isinstance(response, Exception):
        return None
    text = response.strip()
    if text.lower().strip(".") in ("database", "documents", "both"):
        return "route"
    if text.startswith("```sql") or text.upper().startswith("NO_SQL"):
        return "sql"
    if text.startswith("{") and '"need_items"' in text:
        return "plan"
    return "other"


class ScriptedChat(BaseChatModel):
    """Replies are handed out in order, except that a reply of the wrong kind (a route word, a
    SQL block, a plan, anything else) is skipped for the next one of the right kind. The agent
    makes some calls at the same time, so their order is not fixed; matching by kind keeps
    the scripts readable as "route, SQL, plan, answer". An exception is used where it stands.
    """

    responses: list[str | Exception]
    seen: list[list[BaseMessage]] = []
    tokens_per_call: int = 10

    def __init__(self, responses: list[str | Exception], **kw: Any):
        super().__init__(responses=list(responses), seen=[], **kw)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def _next(self, messages: list[BaseMessage]) -> str | Exception:
        self.seen.append(list(messages))
        if not self.responses:
            raise RuntimeError("ScriptedChat ran out of responses")
        want = _prompt_kind(messages)
        head = _response_kind(self.responses[0])
        if head is None or head == want or want == "other":
            return self.responses.pop(0)
        for i, response in enumerate(self.responses):
            if _response_kind(response) == want:
                return self.responses.pop(i)
        return self.responses.pop(0)

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kw):
        nxt = self._next(messages)
        if isinstance(nxt, Exception):
            raise nxt
        msg = AIMessage(
            nxt,
            usage_metadata={
                "input_tokens": self.tokens_per_call // 2,
                "output_tokens": self.tokens_per_call // 2,
                "total_tokens": self.tokens_per_call,
            },
        )
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def _stream(self, messages: list[BaseMessage], stop=None, run_manager=None, **kw):
        """Stream the next response word by word, with token usage on the last chunk."""
        nxt = self._next(messages)
        if isinstance(nxt, Exception):
            raise nxt
        words = nxt.split(" ")
        for i, word in enumerate(words):
            last = i == len(words) - 1
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    content=word if last else word + " ",
                    usage_metadata={"input_tokens": 5, "output_tokens": 5, "total_tokens": 10}
                    if last
                    else None,
                )
            )

    def prompt_text(self, call: int) -> str:
        return "\n".join(str(m.content) for m in self.seen[call])


def sql(text: str) -> str:
    return f"```sql\n{text}\n```"
