from pathlib import Path

from pydantic import BaseModel

class NixPathInfo(BaseModel):
    deriver: Path | None = None
    narHash: str
    narSize: int
    path: Path
    references: list[Path]
    registrationTime: int
    signatures: list[str] = []
    valid: bool = True

    model_config = {
        "arbitrary_types_allowed": True,
        "json_encoders": {
            Path: str
        }
    }
