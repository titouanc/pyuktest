import hashlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Self

log = logging.getLogger(__name__)


@dataclass
class Application:
    # Path to the Unikraft application directory (the one with Makefile.uk)
    source_dir: str | Path
    # kconfig for the applications (lines like "CONFIG_FOO=y")
    defconfig: list[str] = field(default_factory=list)
    # External libraries the application depends on: either paths, or names
    # resolved relative to the platform's libs directory
    libs: list[str | Path] = field(default_factory=list)

    @classmethod
    def from_dir(cls, location: str | Path) -> Self:
        source_dir = Path(location)
        if not source_dir.is_dir():
            raise ValueError(f"{source_dir=} is not a directory")

        if (cfg := source_dir / "defconfig").is_file():
            log.info(f"Reading defconfig from {cfg}")
            defconfig = [line for line in cfg.read_text().splitlines() if line]
        else:
            defconfig = []

        return cls(source_dir, defconfig)

    @property
    def path(self) -> Path:
        return Path(self.source_dir).resolve()

    @property
    def name(self) -> str:
        return self.path.name

    def with_extra(self, defconfig: Sequence[str] = (), libs: Sequence[str | Path] = ()) -> Self:
        return replace(
            self,
            defconfig=[*self.defconfig, *defconfig],
            libs=[*self.libs, *libs],
        )

    def fingerprint(self, *extra: str) -> str:
        """Short hash identifying this build configuration"""
        h = hashlib.sha256()
        for item in (str(self.path), *self.defconfig, *map(str, self.libs), *extra):
            h.update(item.encode())
            h.update(b"\0")
        return h.hexdigest()[:12]
