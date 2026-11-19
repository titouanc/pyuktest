import json

from uktest.application import Application
from uktest.platform import XenHyperlaunch

KiB = 1024
MiB = 1024 * KiB


def shm_dts(paddr: int, size: int, gaddr: int, id: str, role: str = "owner"):
    return f"""
        shared-mem@{paddr:08x} {{
            compatible = "xen,domain-shared-memory-v1";
            xen,shm-id = {json.dumps(id)};
            xen,shared-mem = /bits/ 64 <{paddr:#x} {gaddr:#x} {size:#x}>;
            role = {json.dumps(role)};
        }};
    """


def test_static_shared_memory():
    paddr = 0x4000_0000
    size = 2 * MiB

    dts = f"""
        &domU1 {{
            {shm_dts(paddr, size, 0x8000_0000, id="shm")}
        }};
        &domU2 {{
            {shm_dts(paddr, size, 0x9900_0000, id="shm", role="borrower")}
        }};
    """

    platform = XenHyperlaunch(dts_fragment=dts)

    app = Application.from_dir("samples/shmemread").with_extra(defconfig=["CONFIG_LIBXENEMGCONS=y"])
    image = platform.build(app)

    with platform.run(domus=[image, image]) as machine:
        for _ in range(2):
            machine.console.expect("Memory mapped 'shm'")

        machine.memory.strcpy(paddr, "Hello from pytest!")

        for _ in range(2):
            machine.console.expect("Memory updated: 'Hello from pytest!'")
