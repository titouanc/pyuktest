"""Unikraft tracepoints (lib/ukdebug, CONFIG_LIBUKDEBUG_TRACEPOINTS)

The tracepoint definitions are read from the image's debug ELF, and the
samples from the trace buffer in the memory of the running machine. Both are
parsed with Unikraft's own parser (support/scripts/uk_trace/parse.py).
"""

import importlib.util
import re
import subprocess
import sys
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from types import ModuleType
from typing import Protocol

from .image import Image

# Address of the first byte of the image
BASE_SYMBOL = "_base_addr"
BUFFER_SYMBOL = "uk_trace_buffer"


class TracepointDefinition(Protocol):
    name: str


class TracepointSample(Protocol):
    tp: TracepointDefinition
    time: int
    args: list[int | str]
    formatted_args: str


@cache
def uk_trace_parser(unikraft: Path) -> ModuleType:
    """Import the tracepoint parser of the given Unikraft source tree"""
    path = unikraft / "support" / "scripts" / "uk_trace" / "parse.py"
    spec = importlib.util.spec_from_file_location("uk_trace_parse", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load Unikraft trace parser from {path}")
    module = importlib.util.module_from_spec(spec)
    # Needed by the dataclasses it defines
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@dataclass(frozen=True)
class ElfSymbol:
    addr: int
    size: int


def elf_symbols(elf: Path) -> dict[str, ElfSymbol]:
    out = subprocess.run(
        ["readelf", "-sW", str(elf)], check=True, capture_output=True, text=True
    ).stdout
    # Num: Value Size Type Bind Vis Ndx Name
    sym_re = re.compile(r"^\s*\d+:\s+([0-9a-f]+)\s+(\S+)\s+\S+\s+\S+\s+\S+\s+\S+\s+(\S+)$")
    res = {}
    for line in out.splitlines():
        if m := sym_re.match(line):
            size = int(m[2], 16) if m[2].startswith("0x") else int(m[2])
            res[m[3]] = ElfSymbol(int(m[1], 16), size)
    return res


def elf_ptr_size(elf: Path) -> int:
    with open(elf, "rb") as f:
        ident = f.read(5)
    if ident[:4] != b"\x7fELF":
        raise ValueError(f"{elf} is not an ELF file")
    return {1: 4, 2: 8}[ident[4]]


def read_tracepoints(
    unikraft: Path,
    image: Image,
    read_memory: Callable[[int, int], bytes],
    kernel_phys: int,
) -> Iterator[TracepointSample]:
    """Read the tracepoints recorded by an image in a running machine

    read_memory(addr, size) reads the machine physical memory, and
    kernel_phys is the physical address where the image base is loaded.
    """
    if image.debug_elf is None:
        raise ValueError("Reading tracepoints requires the image debug ELF")
    parser = uk_trace_parser(unikraft)
    elf = image.debug_elf
    symbols = elf_symbols(elf)
    if BUFFER_SYMBOL not in symbols:
        raise ValueError(f"No trace buffer in {elf}, enable CONFIG_LIBUKDEBUG_TRACEPOINTS")
    buf = symbols[BUFFER_SYMBOL]
    buf_phys = kernel_phys + buf.addr - symbols[BASE_SYMBOL].addr
    return parser.sample_parser(
        parser.get_keyvals(elf),
        parser.get_tp_sections(elf),
        read_memory(buf_phys, buf.size),
        elf_ptr_size(elf),
    )


class Tracepoints(Mapping[Image, Iterator[TracepointSample]]):
    """Tracepoints of the images of a machine, read when looked up"""

    def __init__(
        self,
        unikraft: Path,
        kernels_phys: Mapping[Image, int],
        read_memory: Callable[[int, int], bytes],
    ):
        self._unikraft = unikraft
        self._kernels_phys = kernels_phys
        self._read_memory = read_memory

    def __getitem__(self, image: Image) -> Iterator[TracepointSample]:
        return read_tracepoints(self._unikraft, image, self._read_memory, self._kernels_phys[image])

    def __iter__(self) -> Iterator[Image]:
        return iter(self._kernels_phys)

    def __len__(self) -> int:
        return len(self._kernels_phys)
