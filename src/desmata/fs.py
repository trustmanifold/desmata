from dataclasses import dataclass
from pathlib import Path
from typing import cast
import os

from xdg_base_dirs import xdg_cache_home, xdg_config_home, xdg_data_home, xdg_state_home

from desmata.consts import desmata
from desmata.lower_protocols import Loggers, UserspaceFiles, InternalPath, ExternalPath


class CreatePathAttrs:
    def __post_init__(self):
        for attr, attr_type in UserspaceFiles.__annotations__.items():
            if attr_type == Path:
                path = getattr(self, attr)
                if not path.exists():
                    self.log.msg.debug(f"Creating {path}")
                    path.mkdir(parents=True)


@dataclass
class CellFiles(CreatePathAttrs):
    home: Path

    def env(self, path: Path | list[Path] = []) -> dict[str, str]:
        def to_str(path: Path) -> str:
            return str(path.resolve())

        pathvar = (
            ":".join(map(to_str, path)) if isinstance(path, list) else to_str(path)
        )
        return {"HOME": to_str(self.dir), "PATH": pathvar}


@dataclass
class DesmataFiles(CreatePathAttrs, UserspaceFiles):
    log: Loggers
    deps_by_id: InternalPath
    deps_by_hash: InternalPath
    home: InternalPath = cast(InternalPath, Path.home())
    config: InternalPath = cast(InternalPath, xdg_config_home() / desmata)
    cache: InternalPath = cast(InternalPath, xdg_cache_home() / desmata)
    data: InternalPath = cast(InternalPath, xdg_data_home() / desmata)
    state: InternalPath = cast(InternalPath, xdg_state_home() / desmata)



    @staticmethod
    def sandbox(
        parent: Path | str, log: Loggers
    ) -> "DesmataFiles":
        """
        Instead of the user's home dir, use a parent path.
        Useful for testing.
        """
        if isinstance(parent, str):
            parent = Path(parent)

        parent = cast(InternalPath, parent)
        data = parent / "data" / desmata
        return DesmataFiles(
            log=log,
            home=parent,
            config=parent / "config" / desmata,
            cache=parent / "cache" / desmata,
            data=data,
            state=parent / "state" / desmata,
            deps_by_id=data / "deps" / "by_id",
            deps_by_hash= data / "deps" / "by_hash"
        )

    def __post_init__(self):
        super().__post_init__()

    # def home_for(self, *, cell_type: type[CellBase]) -> Path:
    #     # cell names might resemble the path of cell.py
    #     parts = cell_type.cell_path().parts  # /foo/bar/baz/cell.py
    #     name_candidates = [
    #         "/".join(parts[-1 * i :]) for i in range(1, len(parts))
    #     ]  # ["baz", "bar/baz", "foo/bar/baz"]
    #     self.log.msg.debug(f"Cell name candidates: {name_candidates}")

    #     # explore existing cells
    #     with Session(self.engine) as session:
    #         cells = session.exec(
    #             select(Cell).where(Cell.name.in_(name_candidates))
    #         ).all()
    #         self.log.msg.debug(f"found: {[x.name for x in cells]}")

def create_hard_links(src_dir: ExternalPath | InternalPath, dst_dir: InternalPath):
    """Create hard links from src_dir to dst_dir recursively, falling back to copying when cross-device."""
    import shutil
    
    dst_dir.mkdir(parents=True, exist_ok=True)
    
    def safe_link_or_copy(src_file: Path, dst_file: Path):
        """Create a hard link if possible, otherwise copy the file"""
        if not dst_file.exists():
            try:
                # Try to create a hard link first
                os.link(src_file, dst_file)
            except OSError as e:
                # Fall back to copying if linking fails (e.g., cross-device link)
                shutil.copy2(src_file, dst_file)
    
    if src_dir.is_dir():
        for root, dirs, files in os.walk(src_dir):
            rel_path = Path(root).relative_to(src_dir)
            
            # Create directories
            for dirname in dirs:
                (dst_dir / rel_path / dirname).mkdir(parents=True, exist_ok=True)
            
            # Create hard links or copies for files
            for filename in files:
                src_file = Path(root) / filename
                dst_file = dst_dir / rel_path / filename
                dst_file.parent.mkdir(parents=True, exist_ok=True)
                safe_link_or_copy(src_file, dst_file)
    else:
        # Handle a single file
        dst_file = dst_dir / src_dir.name
        safe_link_or_copy(src_dir, dst_file)
