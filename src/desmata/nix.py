from logging import Logger
import json
from pathlib import Path
from typing import cast, Iterator
from collections import deque

from desmata.tool import Cmd, MajorMinorPatch, Tool
from desmata.messages import NixPathInfo

from dataclasses import dataclass

@dataclass
class NixDependencyTree:
    size: int
    info: NixPathInfo
    immediate_dependencies: list['NixDependencyTree']

class Nix(Tool):
    _version: MajorMinorPatch | None = None
    _flakes_enabled: bool | None = None

    def __init__(self, cwd: Path, log: Logger):
        def flakes_and_cmd(cmd: Cmd) -> Cmd:
            return [
                *["--extra-experimental-features", "nix-command"],
                *["--extra-experimental-features", "flakes"],
                *cmd,
            ]

        super().__init__(name="nix", cmd_filter=flakes_and_cmd, cwd=cwd, log=log.getChild("nix"))

    def build(
        self, output_name: str
    ) -> tuple[Path, list[NixPathInfo]]:
        path_str = self(
                    "build",
                    f".#{output_name}",
                    "--print-out-paths",
                    "--no-link",
                ).strip()
        dependency_info: list[NixPathInfo] = []
        raw = json.loads(self("path-info", "--json", "-r", path_str))
        # Older Nix returns a JSON array of info objects (each with a "path"
        # field); newer Nix returns a JSON object keyed by store path, with the
        # path omitted from the value. Normalize both into a list of objects
        # that carry their own "path".
        if isinstance(raw, dict):
            info_objs = [{**value, "path": path} for path, value in raw.items()]
        else:
            info_objs = raw
        for info_obj in info_objs:
            info = NixPathInfo.model_validate(info_obj)
            dependency_info.append(info)
        return Path(path_str), dependency_info
            


    def get_nar_sha256_hex(self, path: Path) -> str:
        raise NotImplementedError()

    @property
    def version(self) -> MajorMinorPatch:
        if not Nix._version:
            Nix._version = cast(
                MajorMinorPatch,
                tuple(map(int, self("--version").split(" ")[-1].split("."))),
            )
        return Nix._version

    def check(self) -> None:
        min_ver = (2, 0, 0)
        if not min_ver <= self.version:
            raise EnvironmentError(f"{self.name} is at {self.version} but {min_ver} is required")

    @staticmethod
    def get_id(path: Path) -> str:
        return str(path).replace("/nix/store/", "")


    @staticmethod
    def dep_dags(info: list[NixPathInfo], root_path: str) -> Iterator[NixDependencyTree]:
        """
        Returns dependencies in topological order (leaves first, root last).
        This ensures that when internalizing dependencies, all dependencies
        of a node are processed before the node itself.
        """
        path_infos: dict[str, NixPathInfo] = {str(x.path): x for x in info}
        built_trees: dict[str, NixDependencyTree] = {}
        
        # Get all nodes and build an adjacency list
        all_nodes = set()
        edges = {}
        reverse_edges = {}
        
        # Build the graph
        for path_info in path_infos.values():
            path_str = str(path_info.path)
            all_nodes.add(path_str)
            edges[path_str] = set()
            
            for ref in path_info.references:
                ref_str = str(ref)
                if ref_str in path_infos:  # Only include references we have info for
                    edges[path_str].add(ref_str)
                    if ref_str not in reverse_edges:
                        reverse_edges[ref_str] = set()
                    reverse_edges[ref_str].add(path_str)
        
        # Find leaf nodes (nodes with no dependencies)
        leaf_nodes = []
        for node in all_nodes:
            if node in edges and not edges[node]:
                leaf_nodes.append(node)
        
        # Process nodes in topological order (leaves first)
        processed = set()
        to_process = deque(leaf_nodes)
        
        while to_process:
            current_path = to_process.popleft()
            
            if current_path in processed:
                continue
                
            # Check if all dependencies have been processed
            if current_path in edges:
                deps = edges[current_path]
                if not all(dep in processed for dep in deps):
                    # Put it back in the queue to process later
                    to_process.append(current_path)
                    continue
            
            processed.add(current_path)
            
            # Skip paths that don't have info
            if current_path not in path_infos:
                continue
                
            curr_info = path_infos[current_path]
            
            # Build dependencies list from already built trees
            deps = [
                built_trees[str(ref)] 
                for ref in curr_info.references 
                if str(ref) in path_infos and str(ref) in built_trees
            ]
            
            # Calculate total size recursively - include current node (1) plus all dependencies
            total_size = 1 + sum(dep.size for dep in deps)
            
            tree = NixDependencyTree(
                size=total_size,
                info=curr_info,
                immediate_dependencies=deps
            )
            built_trees[current_path] = tree
            
            # Yield the current tree
            yield tree
            
            # Add any parents whose dependencies are now all processed
            if current_path in reverse_edges:
                for parent in reverse_edges[current_path]:
                    # Check if all dependencies of parent are now processed
                    if all(dep in processed for dep in edges.get(parent, set())):
                        to_process.append(parent)
        
        # If root_path wasn't processed, process it now
        if root_path not in processed and root_path in path_infos:
            curr_info = path_infos[root_path]
            deps = [
                built_trees[str(ref)] 
                for ref in curr_info.references 
                if str(ref) in path_infos and str(ref) in built_trees
            ]
            total_size = 1 + sum(dep.size for dep in deps)
            tree = NixDependencyTree(
                size=total_size,
                info=curr_info,
                immediate_dependencies=deps
            )
            yield tree

