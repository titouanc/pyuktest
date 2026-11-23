import errno

from uktest.application import Application
from uktest.platform import XenHyperlaunch

KiB = 1024
MiB = 1024 * KiB

PADDR = 0x4000_0000
GADDR = 0x8000_0000
SIZE = 2 * MiB

platform = XenHyperlaunch(
    dts_fragment=(
        f"""
        &domU1 {{
            shared-mem@{PADDR:08x} {{
                compatible = "xen,domain-shared-memory-v1";
                xen,shm-id = "shm";
                xen,shared-mem = /bits/ 64 <{PADDR:#x} {GADDR:#x} {SIZE:#x}>;
                role = "owner";
            }};
        }};
    """
    )
)
app = Application.from_dir("samples/shmemread").with_extra(
    defconfig=[
        "CONFIG_LIBXENEMGCONS=y",
    ],
)


def test_static_shared_memory_noseal():
    image = platform.build(app.with_extra(defconfig=["# CONFIG_XEN_SHM_SEAL is not set"]))

    with platform.run(domus=[image]) as machine:
        # Wait for application to be ready
        machine.console.expect("Got memory region 'shm'")
        # Write text into shared memory
        machine.memory.strcpy(PADDR, "Hello from pytest!")
        # The application should see the change
        machine.console.expect("Memory updated: 'Hello from pytest!'")


def test_static_shared_memory_sealed():
    with platform.run(domus=[platform.build(app)]) as machine:
        machine.console.expect(f"Unable to configure shm 'shm': {-errno.EPERM}")
