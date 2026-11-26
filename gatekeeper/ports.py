from typing import Protocol


class LanguageModel(Protocol):
    """Синхронный порт языковой модели.

    В Streamlit вызывается напрямую. В FastAPI тот же вызов можно отдать
    в asyncio.to_thread, не меняя промпты и правила.
    """

    def complete(self, *, system: str, user: str) -> str: ...
