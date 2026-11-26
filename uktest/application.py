import hashlib
import logging
import os
import textwrap
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Self

log = logging.getLogger(__name__)


def default_build_dir() -> Path:
    return Path(os.environ.get("UKTEST_BUILD_DIR") or "uktest_cache")


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

    @classmethod
    def simple(
        cls,
        source_code: str,
        defconfig: Sequence[str] = (),
        libs: Sequence[str | Path] = (),
        source_dir: str | Path | None = None,
    ) -> Self:
        """Application made of a single C source file given inline

        The application directory is generated in source_dir, which defaults to
        a directory named after the source code, in the uktest build directory.
        """
        source_code = textwrap.dedent(source_code).strip() + "\n"
        if source_dir is None:
            digest = hashlib.sha256(source_code.encode()).hexdigest()[:12]
            source_dir = default_build_dir() / "src" / f"app-{digest}"
        source_dir = Path(source_dir)
        source_dir.mkdir(parents=True, exist_ok=True)

        makefile = "$(eval $(call addlib,app))\n\nAPP_SRCS-y += $(APP_BASE)/main.c\n"
        for name, content in (("Makefile.uk", makefile), ("main.c", source_code)):
            # Only write files whose content changed, to avoid needless rebuilds
            file = source_dir / name
            if not file.is_file() or file.read_text() != content:
                file.write_text(content)

        return cls(source_dir, list(defconfig), list(libs))

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
