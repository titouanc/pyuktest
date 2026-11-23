import logging
import mmap
import os
import subprocess
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

import pexpect

from .image import Image
from .trace import Tracepoints

log = logging.getLogger(__name__)


@dataclass
class PhysicalMemory:
    """Physical RAM of a machine, indexed by physical address

    Supports reading and writing bytes or slices, e.g.:

        memory[0x80000000:0x80000005] = b"hello"
        data = memory[0x80000000:0x80000005]
    """

    buf: mmap.mmap
    base: int

    def __len__(self) -> int:
        return len(self.buf)

    def memcpy(self, addr: int, value: bytes) -> None:
        self[addr : addr + len(value)] = value

    def strcpy(self, addr: int, text: str) -> None:
        self.memcpy(addr, text.encode() + b"\x00")

    def _slice_index(self, addr: slice) -> slice:
        if addr.step not in (None, 1):
            raise ValueError("Memory slices must be contiguous")
        return slice(
            0 if addr.start is None else self._int_index(addr.start),
            len(self) if addr.stop is None else self._int_index(addr.stop),
        )

    def _int_index(self, addr: int) -> int:
        idx = addr - self.base
        if not (0 <= idx < len(self)):
            raise IndexError(f"Address {addr:#x} is not in RAM")
        return idx

    def _offset(self, addr: int | slice) -> int | slice:
        if isinstance(addr, slice):
            return self._slice_index(addr)
        elif isinstance(addr, int):
            return self._int_index(addr)
        else:
            raise TypeError(f"Invalid address {type(addr)}")

    def __getitem__(self, addr: int | slice) -> int | bytes:
        return self.buf[self._offset(addr)]

    def __setitem__(self, addr: int | slice, value: int | bytes):
        offset = self._offset(addr)
        if isinstance(offset, slice):
            if not isinstance(value, bytes):
                raise TypeError(f"Can only write bytes to a memory slice, not {type(value)}")
            ndst = offset.stop - offset.start
            nsrc = len(value)
            if ndst != nsrc:
                raise ValueError(f"Write {nsrc} bytes to {ndst} bytes slice")
            self.buf[offset] = value
        else:
            if not isinstance(value, int):
                raise TypeError(f"Can only write an int to a memory address, not {type(value)}")
            self.buf[offset] = value

    def close(self):
        self.buf.close()


@dataclass(kw_only=True)
class Machine(ABC):
    """A running machine, booted by Platform.run()"""

    images: list[Image]
    # Unikraft source tree, whose tools are used to inspect the images
    unikraft: Path | None = None
    # Physical address where the kernel of each image is loaded, i.e. where
    # its first address (the _base_addr symbol) is mapped
    kernels_phys: dict[Image, int] = field(default_factory=dict)
    _console: pexpect.spawn | None = field(default=None, init=False, repr=False)

    @property
    def console(self) -> pexpect.spawn:
        if self._console is None:
            raise RuntimeError("No console bound")
        return self._console

    @property
    @abstractmethod
    def memory(self) -> PhysicalMemory:
        """Physical memory of the running machine"""

    @property
    @abstractmethod
    def device_tree(self) -> str:
        """Device tree the machine boots with, as device tree source"""

    @property
    def tracepoints(self) -> Tracepoints:
        """Tracepoints recorded by the images, e.g. machine.tracepoints[image]

        Looking up an image reads its trace buffer, and returns an iterator over
        the recorded samples (see TracepointSample). The images must be built
        with CONFIG_LIBUKDEBUG_TRACEPOINTS=y.
        """
        if self.unikraft is None:
            raise RuntimeError("Unikraft source tree is required to read tracepoints")

        def read_memory(addr: int, size: int) -> bytes:
            data = self.memory[addr : addr + size]
            assert isinstance(data, bytes)
            return data

        return Tracepoints(self.unikraft, self.kernels_phys, read_memory)

    @abstractmethod
    def start(self): ...

    @abstractmethod
    def stop(self): ...


@dataclass(kw_only=True)
class QemuMachine(Machine):
    """A machine emulated by QEMU, whose serial console is on stdio"""

    qemu: str
    args: list[str]
    # Scratch directory, valid while the machine is running
    workdir: Path
    # Guest RAM, shared with QEMU through a file in workdir
    memory_base: int
    memory_size: int
    console_timeout: float = 2.5
    _memory: PhysicalMemory | None = field(default=None, init=False, repr=False)

    @property
    def memory(self) -> PhysicalMemory:
        if self._memory is None:
            raise RuntimeError("Machine is not running")
        return self._memory

    def _memory_args(self) -> list[str]:
        """Create the file backing the guest RAM, and map it"""
        size = self.memory_size
        ram = self.workdir / "ram"
        fd = os.open(ram, os.O_RDWR | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.ftruncate(fd, size)
            self._memory = PhysicalMemory(mmap.mmap(fd, size, mmap.MAP_SHARED), self.memory_base)
        finally:
            os.close(fd)
        return [
            "-object",
            f"memory-backend-file,id=ramfile,size={size},mem-path={ram},share=on",
            "-machine",
            "memory-backend=ramfile",
        ]

    @cached_property
    def device_tree(self) -> str:
        """Device tree QEMU hands to the guest, as device tree source

        QEMU is run again with the same arguments to dump its final device
        tree (with the /chosen entries it adds). Random values in it, such as
        /chosen/rng-seed, differ from the ones of the running machine.
        """
        dtb = self.workdir / "machine.dtb"
        subprocess.run(
            [self.qemu, *self.args, "-machine", f"dumpdtb={dtb}"],
            check=True,
            capture_output=True,
        )
        return subprocess.run(
            ["dtc", "-q", "-I", "dtb", "-O", "dts", str(dtb)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout

    def start(self):
        log.info(f"Running {self.qemu} {self.args}")
        self._console = pexpect.spawn(
            self.qemu,
            [*self.args, *self._memory_args()],
            encoding="utf-8",
            codec_errors="replace",
            timeout=self.console_timeout,
            logfile=sys.stdout,
        )

    def stop(self):
        if self._console is not None:
            log.info("Terminating")
            self._console.terminate(force=True)
            self._console = None
        if self._memory is not None:
            self._memory.close()
            self._memory = None
