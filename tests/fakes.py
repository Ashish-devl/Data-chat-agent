"""A scripted chat model that records every prompt it receives."""

from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult


class ScriptedChat(BaseChatModel):
    responses: list[str | Exception]
    seen: list[list[BaseMessage]] = []
    tokens_per_call: int = 10

    def __init__(self, responses: list[str | Exception], **kw: Any):
        super().__init__(responses=list(responses), seen=[], **kw)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kw):
        self.seen.append(list(messages))
        if not self.responses:
            raise RuntimeError("ScriptedChat ran out of responses")
        nxt = self.responses.pop(0)
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

    def prompt_text(self, call: int) -> str:
        return "\n".join(str(m.content) for m in self.seen[call])


def sql(text: str) -> str:
    return f"```sql\n{text}\n```"
