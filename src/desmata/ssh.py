import subprocess
from logging import Logger
from typing import cast

from desmata.tool import MajorMinorPatch, Tool


class Ssh(Tool):
    """A trusted bootstrap tool. Like nix and git, the user is expected to have
    installed ssh; desmata only verifies its interface. ssh is what lets nix move
    a closure between peers (``nix copy --from ssh://...``) so a peer who lacks
    ipfs can still receive it -- the chicken-and-egg breaker for peer bootstrap.
    """

    _version: MajorMinorPatch | None = None

    def __init__(self, log: Logger):
        super().__init__(name="ssh", log=log)

    @property
    def version(self) -> MajorMinorPatch:
        if not Ssh._version:
            # `ssh -V` writes to stderr, e.g. "OpenSSH_9.6p1, LibreSSL 3.3.6"
            proc = subprocess.run(["ssh", "-V"], capture_output=True, text=True)
            line = (proc.stderr or proc.stdout).strip()
            token = line.split(",", 1)[0]                  # OpenSSH_9.6p1
            ver = token.split("_", 1)[-1].split("p", 1)[0]  # 9.6
            nums = [int(x) for x in ver.split(".") if x.isdigit()]
            while len(nums) < 3:
                nums.append(0)
            Ssh._version = cast(MajorMinorPatch, tuple(nums[:3]))
        return Ssh._version

    def check(self) -> None:
        min_ver = (7, 0, 0)
        if not min_ver <= self.version:
            raise EnvironmentError(
                f"{self.name} is at {self.version} but {min_ver} is required"
            )
