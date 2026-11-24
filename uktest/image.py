import json
from dataclasses import dataclass
from math import ceil
from pathlib import Path
from typing import Self


@dataclass
class BinaryLoader:
    # Compatible string in device tree
    compatible: str
    # File to load
    file: Path
    # Physical load address for the binary
    addr: int
    # Size of the binary
    size: int
    # Reserved physical memory (>=file size for alignment)
    reserved_size: int
    # Boot arguments
    bootargs: str | None = None

    @classmethod
    def create(
        cls,
        compatible: str,
        file: Path,
        addr: int,
        align: int,
        bootargs: str | None = None,
    ) -> Self:
        if addr % align:
            raise ValueError("start address is not aligned")
        size = file.stat().st_size
        return cls(
            compatible=compatible,
            file=file,
            addr=addr,
            size=size,
            reserved_size=ceil(size / align) * align,
            bootargs=bootargs,
        )

    @property
    def end(self) -> int:
        return self.addr + self.reserved_size

    @property
    def qemu_args(self) -> list[str]:
        return [
            "-device",
            f"loader,file={self.file},addr={self.addr:#x},force-raw=on",
        ]

    @property
    def dts_fragment(self) -> str:
        """Device tree node describing this binary as a multiboot module

        Addresses and sizes are expressed with 2 cells each.
        """
        lines = [
            f"module@{self.addr:x} {{",
            f'\tcompatible = {json.dumps(self.compatible)}, "multiboot,module";',
            f"\treg = <0x0 {self.addr:#x} 0x0 {self.size:#x}>;",
        ]
        if self.bootargs is not None:
            lines.append(f"\tbootargs = {json.dumps(self.bootargs)};")
        lines.append("};")
        return "\n".join(lines)


@dataclass(frozen=True)
class Image:
    kernel: Path
    rootfs: Path | None = None
    dtb: Path | None = None
    debug_elf: Path | None = None

    @classmethod
    def linux_from_dir(
        cls,
        location: str,
        kernel_name: str = "Image",
        rootfs_name: str = "rootfs.cpio.gz",
        dtb_name: str | None = None,
    ) -> Self:

        def validate_file(p: Path) -> Path:
            if not p.exists():
                raise FileNotFoundError(str(p))
            return p

        dir = Path(location)
        kernel = validate_file(dir / kernel_name)
        rootfs = validate_file(dir / rootfs_name)
        dtb = None if dtb_name is None else validate_file(dir / dtb_name)

        return cls(kernel=kernel, rootfs=rootfs, dtb=dtb)

    def binary_loaders(
        self, start_addr: int, align: int, bootargs: str | None = None
    ) -> list[BinaryLoader]:
        res = [BinaryLoader.create("multiboot,kernel", self.kernel, start_addr, align, bootargs)]
        if self.rootfs is not None:
            res.append(BinaryLoader.create("multiboot,ramdisk", self.rootfs, res[-1].end, align))
        if self.dtb is not None:
            res.append(BinaryLoader.create("multiboot,device-tree", self.dtb, res[-1].end, align))
        return res
