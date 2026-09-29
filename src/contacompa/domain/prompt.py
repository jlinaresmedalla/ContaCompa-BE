from pydantic import BaseModel, ConfigDict


class Prompt(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    version: str
    system: str
    user: str

    def render_user(self, **values: str) -> str:
        return self.user.format_map(values)
