import hashlib
import logging
import os
import platform as host_platform
import subprocess
import tempfile
import textwrap
from abc import ABC, abstractmethod
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from pexpect.exceptions import TIMEOUT

from .application import Application
from .image import BinaryLoader, Image, dts_string
from .machine import Machine, QemuMachine

log = logging.getLogger(__name__)


class BuildFailed(RuntimeError):
    pass


class BootFailed(RuntimeError):
    pass


def _env_path(name: str) -> Path | None:
    return Path(p) if (p := os.environ.get(name)) else None


def _resolve(path: str | Path | None) -> Path | None:
    return Path(path).expanduser().resolve() if path else None


def _dtc(src: str, out: Path, *, input_format: str = "dts", output_format: str = "dtb") -> str:
    """Run the device tree compiler on src, return its standard output"""
    try:
        return subprocess.run(
            ["dtc", "-q", "-I", input_format, "-O", output_format, "-o", str(out), "-"],
            input=src,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except subprocess.CalledProcessError as err:
        raise BuildFailed(f"Invalid device tree:\n{err.stderr}\n{src}") from err


def compile_dts(dts: str, out_dir: Path) -> Path:
    """Compile device tree source to a dtb in out_dir, named after its content"""
    dts = textwrap.dedent(dts)
    dtb = out_dir / f"{hashlib.sha256(dts.encode()).hexdigest()[:12]}.dtb"
    if not dtb.exists():
        _dtc(dts, dtb)
    return dtb


def dtb_to_dts(dtb: Path) -> str:
    return subprocess.run(
        ["dtc", "-q", "-I", "dtb", "-O", "dts", str(dtb)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


@dataclass(kw_only=True)
class Platform(ABC):
    build_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("UKTEST_BUILD_DIR") or "uktest_cache")
    )

    def __post_init__(self):
        self.build_dir = Path(self.build_dir).expanduser().resolve()

    @abstractmethod
    def build(self, app: Application, dts: str | None = None) -> Image:
        """Build the application for this platform

        dts is an optional device tree source, compiled into the image's dtb.
        """

    @abstractmethod
    @contextmanager
    def run(self, **kwargs) -> Generator[Machine, None, None]:
        """Boot and run the platform, yielding the running machine"""


@dataclass(kw_only=True)
class UnikraftMakePlatform(Platform):
    """Platform building Unikraft applications with its Make-based build system"""

    # Kconfig lines selecting the platform and architecture
    base_defconfig: ClassVar[list[str]]
    # Target architecture, as reported by `uname -m`
    arch: ClassVar[str]
    # Suffix of the kernel produced by the build, e.g. "xen-arm64"
    kernel_suffix: ClassVar[str]
    # Suffix appended to the kernel file name to get its debug ELF
    debug_elf_suffix: ClassVar[str] = ".dbg"

    # Unikraft source tree
    unikraft: Path | None = field(default_factory=lambda: _env_path("UK_ROOT"))
    # Directory where external libraries are looked up by name
    libs_dir: Path | None = field(default_factory=lambda: _env_path("UK_LIBS"))
    # Toolchain prefix, guessed from `arch` when cross-compiling if not given
    cross_compile: str | None = field(default_factory=lambda: os.environ.get("CROSS_COMPILE"))
    jobs: int = field(default_factory=lambda: os.cpu_count() or 1)

    def __post_init__(self):
        super().__post_init__()
        self.unikraft = _resolve(self.unikraft)
        if self.unikraft is None:
            raise ValueError("Unikraft source tree not given (pass unikraft= or set UK_ROOT)")
        self.libs_dir = _resolve(self.libs_dir)
        if self.cross_compile is None and host_platform.machine() != self.arch:
            self.cross_compile = f"{self.arch}-linux-gnu-"

    def _resolve_lib(self, lib: str | Path) -> Path:
        path = Path(lib)
        if not path.is_absolute() and not path.exists() and self.libs_dir:
            path = self.libs_dir / path
        if not path.is_dir():
            raise FileNotFoundError(f"Unikraft library not found: {lib}")
        return path.resolve()

    def _make(self, app: Application, out_dir: Path, *targets: str, **variables: str):
        cmd = [
            "make",
            "-C",
            str(self.unikraft),
            f"A={app.path}",
            f"C={out_dir / '.config'}",
            f"O={out_dir}",
            f"-j{self.jobs}",
        ]
        if app.libs:
            cmd.append("L=" + ":".join(str(self._resolve_lib(lib)) for lib in app.libs))
        if self.cross_compile:
            cmd.append(f"CROSS_COMPILE={self.cross_compile}")
        cmd += [f"{k}={v}" for k, v in variables.items()]
        cmd += targets
        log.info("Running %s", " ".join(cmd))
        subprocess.run(cmd, check=True)

    def build(self, app: Application, dts: str | None = None) -> Image:
        defconfig = [*self.base_defconfig, *app.defconfig]
        out_dir = self.build_dir / f"{app.name}-{app.fingerprint(*defconfig)}"
        out_dir.mkdir(parents=True, exist_ok=True)

        defconfig_file = out_dir / "uktest.defconfig"
        defconfig_file.write_text("\n".join(defconfig) + "\n")

        try:
            self._make(app, out_dir, "defconfig", UK_DEFCONFIG=str(defconfig_file))
            self._make(app, out_dir)
        except subprocess.CalledProcessError as err:
            raise BuildFailed() from err

        kernels = [p for p in out_dir.glob(f"*_{self.kernel_suffix}") if p.is_file()]
        if len(kernels) != 1:
            raise RuntimeError(f"Expected one kernel in {out_dir}, found {kernels}")
        kernel = kernels[0]
        debug_elf = kernel.with_name(kernel.name + self.debug_elf_suffix)
        return Image(
            kernel=kernel,
            dtb=None if dts is None else compile_dts(dts, out_dir),
            debug_elf=debug_elf if debug_elf.exists() else None,
        )


@dataclass(kw_only=True)
class QemuArm64Platform(UnikraftMakePlatform):
    """Platform emulated by QEMU on the arm64 virt machine"""

    qemu: str = "qemu-system-aarch64"
    memory_mb: int = 2048
    cpus: int = 2

    arch: ClassVar[str] = "aarch64"
    # Boot the machine at EL2, to run a hypervisor
    virtualization: ClassVar[bool] = False
    RAM_BASE: ClassVar[int] = 0x40000000
    base_defconfig: ClassVar[list[str]] = [
        "CONFIG_ARCH_ARM_64=y",
        "CONFIG_LIBUKINTCTLR_GIC=y",
        "CONFIG_LIBUKINTCTLR_GICV3=y",
    ]

    def _qemu_machine_args(self) -> list[str]:
        virtualization = "on" if self.virtualization else "off"
        return [
            "-machine",
            f"virt,virtualization={virtualization},gic-version=3",
            "-cpu",
            "cortex-a72",
            "-smp",
            str(self.cpus),
            "-m",
            f"{self.memory_mb}M",
            "-nographic",
        ]

    @contextmanager
    def _run_qemu(
        self,
        images: list[Image],
        args: list[str],
        workdir: Path,
        console_timeout: float,
    ) -> Generator[QemuMachine, None, None]:
        machine = QemuMachine(
            images=images,
            qemu=self.qemu,
            args=[*self._qemu_machine_args(), *args],
            workdir=workdir,
            memory_base=self.RAM_BASE,
            memory_size=self.memory_mb * 1024 * 1024,
            console_timeout=console_timeout,
        )
        machine.start()
        try:
            yield machine
        finally:
            machine.stop()


@dataclass(kw_only=True)
class QemuAarch64(QemuArm64Platform):
    """Unikraft booted directly by QEMU on the arm64 virt machine (KVM platform)

    Only a single image can be run at a time.
    """

    bootargs: str = ""

    base_defconfig: ClassVar[list[str]] = [
        *QemuArm64Platform.base_defconfig,
        "CONFIG_PLAT_KVM=y",
        "CONFIG_KVM_VMM_QEMU=y",
    ]
    kernel_suffix: ClassVar[str] = "qemu-arm64"

    @contextmanager
    def run(
        self,
        image: Image,
        console_timeout: float = 2.5,
    ) -> Generator[QemuMachine, None, None]:
        args = ["-kernel", str(image.kernel)]
        if self.bootargs:
            args += ["-append", self.bootargs]
        if image.rootfs is not None:
            args += ["-initrd", str(image.rootfs)]
        if image.dtb is not None:
            args += ["-dtb", str(image.dtb)]

        with (
            tempfile.TemporaryDirectory(prefix="uktest-qemu-") as tmp,
            self._run_qemu([image], args, Path(tmp), console_timeout) as machine,
        ):
            yield machine


@dataclass(kw_only=True)
class XenHyperlaunch(QemuArm64Platform):
    """Xen on arm64, emulated by QEMU, booting the images as dom0less domains

    Every domU image is started directly by Xen at boot time, as described in
    the device tree /chosen node. Optionally, a dom0 (e.g. Linux) can be booted
    alongside them: its modules are then placed directly under /chosen, as
    Xen expects for the hardware domain.

    dts_fragment is extra device tree source, applied on top of the generated
    one. The domUs nodes are labelled domU1, domU2... (in the order of the
    domus given to run()), so that the fragment can extend them, e.g.:

        &domU1 {
            shared-mem@60000000 { ... };
        };
    """

    # Xen hypervisor binary
    xen: Path | None = field(default_factory=lambda: _env_path("XEN_IMAGE"))
    domu_memory_mb: int = 128
    domu_cpus: int = 1
    dom0_memory_mb: int = 1024
    # guest_loglvl=all: print guests' HYPERVISOR_console_io output (logged at debug level)
    xen_bootargs: str = "console=dtuart bootscrub=0 guest_loglvl=all"
    domu_bootargs: str = ""
    dom0_bootargs: str = "console=hvc0 earlyprintk=xen root=/dev/ram0 rw"
    # Extra device tree source, applied after the generated one
    dts_fragment: str = ""

    base_defconfig: ClassVar[list[str]] = [
        *QemuArm64Platform.base_defconfig,
        "CONFIG_PLAT_XEN=y",
        # Disable Xen console, only available to PV guests (which is not the
        # case for arm64)
        "# CONFIG_LIBXENCONS is not set",
        "CONFIG_ARM64_ERRATUM_858921=n",
        "CONFIG_ARM64_ERRATUM_835769=n",
        "CONFIG_ARM64_ERRATUM_843419=n",
    ]
    kernel_suffix: ClassVar[str] = "xen-arm64"
    debug_elf_suffix: ClassVar[str] = ".elf.dbg"
    virtualization: ClassVar[bool] = True

    # Where QEMU loads the domain modules (kernels, ramdisks)
    MODULES_BASE: ClassVar[int] = 0x50000000
    MODULES_ALIGN: ClassVar[int] = 0x200000

    def __post_init__(self):
        super().__post_init__()
        self.xen = _resolve(self.xen)
        if self.xen is None:
            raise ValueError("Xen image not given (pass xen= or set XEN_IMAGE)")

    def _qemu_machine_args(self) -> list[str]:
        # Two serial ports, so that QEMU creates a second PL011 (at 0x9040000)
        # that can be passed through to a domU. Xen uses the first one. Both
        # are multiplexed on stdio, with the monitor, so that the console
        # shows the output of both.
        return [
            *super()._qemu_machine_args(),
            "-chardev",
            "stdio,mux=on,id=console",
            "-serial",
            "chardev:console",
            "-serial",
            "chardev:console",
            "-mon",
            "chardev=console",
        ]

    def _combine_binaries(self, domains: list[tuple[Image, str]]) -> list[list[BinaryLoader]]:
        """Place the binaries of every domain one after the other in RAM

        Takes (image, kernel bootargs) for each domain, and returns the binary
        loaders of each domain, in the same order.
        """
        res = []
        addr = self.MODULES_BASE
        for img, bootargs in domains:
            loaders = img.binary_loaders(
                start_addr=addr,
                align=self.MODULES_ALIGN,
                bootargs=bootargs,
            )
            addr = loaders[-1].end
            res.append(loaders)
        if addr > self.RAM_BASE + self.memory_mb * 1024 * 1024:
            raise ValueError("Images do not fit in the emulated RAM, increase memory_mb")
        return res

    def _chosen_dts(
        self,
        dom0: list[BinaryLoader] | None,
        domus: list[list[BinaryLoader]],
    ) -> str:
        xen_bootargs = self.xen_bootargs
        if dom0 is not None:
            xen_bootargs += f" dom0_mem={self.dom0_memory_mb}M"
        lines = [
            "/ {",
            "\tchosen {",
            "\t\t#address-cells = <0x2>;",
            "\t\t#size-cells = <0x2>;",
            f"\t\txen,xen-bootargs = {dts_string(xen_bootargs)};",
        ]
        # The dom0 modules sit directly in /chosen, not in a xen,domain node
        for loader in dom0 or []:
            lines.append(textwrap.indent(loader.dts_fragment, "\t\t"))
        for i, loaders in enumerate(domus, start=1):
            lines += [
                f"\t\tdomU{i}: domU{i} {{",
                '\t\t\tcompatible = "xen,domain";',
                "\t\t\t#address-cells = <0x2>;",
                "\t\t\t#size-cells = <0x2>;",
                f"\t\t\tmemory = <0x0 {self.domu_memory_mb * 1024:#x}>;",
                f"\t\t\tcpus = <{self.domu_cpus}>;",
                "\t\t\tvpl011;",
            ]
            for loader in loaders:
                lines.append(textwrap.indent(loader.dts_fragment, "\t\t\t"))
            lines.append("\t\t};")
        lines += ["\t};", "};", ""]
        return "\n".join(lines)

    def _make_dtb(
        self,
        workdir: Path,
        dom0: list[BinaryLoader] | None,
        domus: list[list[BinaryLoader]],
    ) -> Path:
        base_dtb = workdir / "virt.dtb"
        subprocess.run(
            [self.qemu, *self._qemu_machine_args(), "-machine", f"dumpdtb={base_dtb}"],
            check=True,
            capture_output=True,
        )
        base_dts = dtb_to_dts(base_dtb)
        # dtc merges the second root node into the first one
        dts = workdir / "xen.dts"
        dts.write_text(
            "\n".join(
                [
                    base_dts,
                    self._chosen_dts(dom0, domus),
                    textwrap.dedent(self.dts_fragment),
                ]
            )
        )
        dtb = workdir / "xen.dtb"
        try:
            subprocess.run(
                ["dtc", "-q", "-I", "dts", "-O", "dtb", "-o", str(dtb), str(dts)],
                check=True,
            )
        except subprocess.CalledProcessError as err:
            raise BuildFailed(f"Invalid device tree {dts.read_text()}") from err
        return dtb

    @contextmanager
    def run(
        self,
        dom0: Image | None = None,
        domus: Sequence[Image] = (),
        console_timeout: float = 2.5,
    ) -> Generator[QemuMachine, None, None]:
        """Boot Xen with the given domains

        dom0 is the hardware domain (e.g. Linux), optional. Its device tree is
        generated by Xen from the host one, so it cannot have its own dtb.
        domus are started as dom0less domains, each one with a vpl011 console.
        A domU image dtb is used as its passthrough device tree, e.g. to give
        it the second QEMU UART (at 0x9040000), whose output also shows up on
        the console.
        """
        if dom0 is None and not domus:
            raise ValueError("At least one domain is required")
        if dom0 is not None and dom0.dtb is not None:
            raise ValueError("dom0 device tree is generated by Xen, dom0.dtb must be None")

        domains = [(img, self.domu_bootargs) for img in domus]
        if dom0 is not None:
            domains.insert(0, (dom0, self.dom0_bootargs))
        binaries = self._combine_binaries(domains)
        dom0_loaders = binaries.pop(0) if dom0 is not None else None
        domu_loaders = binaries

        with tempfile.TemporaryDirectory(prefix="uktest-xen-") as tmp:
            workdir = Path(tmp)
            dtb = self._make_dtb(workdir, dom0_loaders, domu_loaders)
            args = [
                "-kernel",
                str(self.xen),
                "-dtb",
                str(dtb),
            ]
            for loaders in [dom0_loaders or [], *domu_loaders]:
                for loader in loaders:
                    args += loader.qemu_args

            images = [img for img, _ in domains]
            with self._run_qemu(images, args, workdir, console_timeout) as machine:
                try:
                    machine.console.expect(
                        r"\(XEN\) Xen dom0less mode detected"
                        if dom0 is None
                        else r"\*\*\* LOADING DOMAIN 0 \*\*\*"
                    )

                    for _ in domus:
                        machine.console.expect(r"\*\*\* LOADING DOMU")

                except TIMEOUT as err:
                    raise BootFailed() from err

                yield machine
