# pyuktest

pyuktest is an experimental runtime-testing infrastructure for Unikraft and Xen.

It builds a Unikraft application for a given platform, boots it in QEMU and
gives you its console (a [pexpect](https://pexpect.readthedocs.io/) object) to
check its output. It is particularly useful with [pytest](https://pytest.org/).

## Platforms

| Platform         | What runs                                                                  |
|------------------|----------------------------------------------------------------------------|
| `QemuAarch64`    | One Unikraft image booted directly by QEMU on arm64 (KVM platform)         |
| `XenHyperlaunch` | Xen on arm64: Unikraft domUs started at boot (dom0less), optionally a dom0 |

## Configuration

Platforms take their settings as keyword arguments, falling back to these
environment variables:

| Variable           | Meaning                                                   |
|--------------------|-----------------------------------------------------------|
| `UK_ROOT`          | Unikraft source tree (required)                           |
| `XEN_IMAGE`        | Xen hypervisor binary (required for `XenHyperlaunch`)     |
| `UK_LIBS`          | Directory where external libraries are looked up by name  |
| `CROSS_COMPILE`    | Toolchain prefix, guessed when cross-compiling if not set |
| `UKTEST_BUILD_DIR` | Where builds are cached (default: `uktest_cache`)         |

## Image

An `Image` is one or several related binaries that correspond to a bootable system.
It consists of:

* A kernel (a bootable binary) **required**
* An *optional* rootfs
* An *optional* device tree blob
* An *optional* debug-enabled ELF file of the kernel (used for debugging purposes)

## Applications

An `Application` represents a configured Unikraft application.
`Application.from_dir(path)` reads the `defconfig` file in `path`, if there is one.
`with_extra()` returns a copy with more Kconfig lines or libraries, so one
application can be built in several configurations:

```python
from uktest.application import Application

HELLO_WORLD = Application.from_dir("helloworld")
app = HELLO_WORLD.with_extra(defconfig=["CONFIG_LIBPL011=y"])
```

`platform.build(app)` builds it and returns an `Image`. Each configuration
gets its own build directory, so rebuilding one is incremental.

## Example usage

### QEMU

```python
from uktest.application import Application
from uktest.platform import QemuAarch64

platform = QemuAarch64()
application = Application.from_dir("helloworld")
image = platform.build(application)

with platform.run(image=image) as machine:
    machine.console.expect("Hello Unikraft world!")
```

### Xen dom0less

Each image in `domus` is started by Xen as its own domain, with a vpl011
console whose output Xen prints on its own console:

```python
from uktest.platform import XenHyperlaunch

platform = XenHyperlaunch()
image = platform.build(application.with_extra(defconfig=["CONFIG_LIBPL011=y"]))

# Run the image twice in 2 domUs, we should see the message twice
with platform.run(domus=[image, image]) as machine:
    machine.console.expect("Hello Unikraft world!")
    machine.console.expect("Hello Unikraft world!")
```

### Xen with a Linux dom0

A dom0 can be booted alongside the domUs. `Image.linux_from_dir()` loads a
Linux kernel (`Image`) and initramfs (`rootfs.cpio.gz`) from a directory, such as
a Buildroot output. The domUs and dom0 boot in parallel, so their messages can
show up in any order:

```python
from uktest.image import Image

linux = Image.linux_from_dir("path/to/buildroot/images/")

with platform.run(dom0=linux, domus=[image], console_timeout=15) as machine:
    boot_messages = ["Hello Unikraft world!", r"Booting Linux on physical CPU"]
    while boot_messages:
        found = machine.console.expect(boot_messages)
        boot_messages.pop(found)
```

Xen generates dom0's device tree itself, so the dom0 image must not have a
`dtb`. The dom0 memory and kernel command line are set with the
`dom0_memory_mb` and `dom0_bootargs` arguments of `XenHyperlaunch`.

### Patching Xen's device tree

`XenHyperlaunch(dts_fragment=...)` takes extra device tree source, applied on
top of the generated one. The domU nodes are labelled `domU1`, `domU2`, ... in
the order of `domus`, so a fragment can extend them, e.g. to give the first
domU a static shared memory region:

```python
platform = XenHyperlaunch(
    dts_fragment="""
    &domU1 {
        shared-mem@60000000 {
            compatible = "xen,domain-shared-memory-v1";
            role = "owner";
            xen,shm-id = "shm";
            xen,shared-mem = /bits/ 64 <0x60000000 0x200000>;
        };
    };
"""
)
```

### Device passthrough

`platform.build(app, dts=...)` compiles the given device tree source into the
image `dtb`. For a Xen domU, it is the domain's passthrough device tree.

QEMU runs with a second PL011 at `0x9040000` (Xen keeps the first one), and
both are multiplexed on the console. A domU given this PL011 through its
passthrough device tree sees 2 UARTs: its vpl011 console and the real one. See
`test_hello_world_xen_only_one_pl011` in
[samples/test_console.py](samples/test_console.py).

See [example.py](example.py) for the complete tests (`pytest example.py`).
