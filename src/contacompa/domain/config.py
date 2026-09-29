from pydantic import BaseModel, ConfigDict, Field


class RunConfig(BaseModel):
    """Frozen description of how one extraction runs. Stored on the job at enqueue time."""

    model_config = ConfigDict(frozen=True)

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1, default="v1")
    parser: str = Field(min_length=1, default="native")
    effort: str | None = None

    def key(self) -> str:
        base = f"{self.provider}/{self.model}/{self.prompt_version}/{self.parser}"
        return f"{base}/{self.effort}" if self.effort else base
