"""A scripted chat model that records every prompt it receives."""

from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult


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

    def _stream(self, messages: list[BaseMessage], stop=None, run_manager=None, **kw):
        """Stream the next response word by word, with token usage on the last chunk."""
        self.seen.append(list(messages))
        if not self.responses:
            raise RuntimeError("ScriptedChat ran out of responses")
        nxt = self.responses.pop(0)
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
