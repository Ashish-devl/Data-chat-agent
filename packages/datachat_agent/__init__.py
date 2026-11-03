"""DataChat Agent: answer questions from your SQL database and PDFs."""

__version__ = "0.1.0"
__all__ = ["DataChat", "mount"]


def __getattr__(name: str):
    # Imported on first use so `import datachat_agent` stays light.
    if name == "DataChat":
        from datachat_agent.client import DataChat

        return DataChat
    if name == "mount":
        from datachat_agent.server.app import mount

        return mount
    raise AttributeError(name)
