from uktest.application import Application
from uktest.platform import XenHyperlaunch

KiB = 1024
MiB = 1024 * KiB

def test_static_shared_memory():
    paddr = 0x4000_0000
    size = 2 * MiB

    dts = f"""
        &domU1 {{
            shared-mem@{paddr:08x} {{
                compatible = "xen,domain-shared-memory-v1";
                xen,shm-id = "shm";
                xen,shared-mem = /bits/ 64 <{paddr} {paddr + 0x8000_0000} {size}>;
                role = "owner";
            }};
        }};

        &domU2 {{
            shared-mem@{paddr:08x} {{
                compatible = "xen,domain-shared-memory-v1";
                xen,shm-id = "shm";
                xen,shared-mem = /bits/ 64 <{paddr} {paddr + 0x9000_0000} {size}>;
                role = "borrower";
            }};
        }};
    """

    platform = XenHyperlaunch(dts_fragment=dts)

    app = Application.from_dir("samples/shmemread").with_extra(
        defconfig=["CONFIG_LIBXENEMGCONS=y"]
    )
    image = platform.build(app)

    with platform.run(domus=[image, image]) as machine:
        for _ in range(2):
            machine.console.expect("Memory mapped 'shm'")

        machine.memory.strcpy(paddr, "Hello from pytest !")

        for _ in range(2):
            machine.console.expect("Memory updated: 'Hello from pytest'")
