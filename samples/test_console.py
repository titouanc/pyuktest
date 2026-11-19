import pytest

from uktest.application import Application
from uktest.image import Image
from uktest.platform import QemuAarch64, XenHyperlaunch

HELLO_WORLD = Application.from_dir("samples/helloworld")
HELLO_WORLD_TEXT = "Hello Unikraft world!"


def test_hello_world_qemu():
    platform = QemuAarch64()
    # app = HELLO_WORLD.with_extra(defconfig=["CONFIG_LIBPL011=y"])

    with platform.run(image=platform.build(HELLO_WORLD)) as machine:
        machine.console.expect(HELLO_WORLD_TEXT)


def test_hello_world_xen_linuxdom0():
    platform = XenHyperlaunch()
    linux = Image.linux_from_dir("/home/titou/Code/unikraft-dev/unikraft/buildroot-arm64/")
    app = HELLO_WORLD.with_extra(defconfig=["CONFIG_LIBXENEMGCONS=y"])

    with platform.run(dom0=linux, domus=[platform.build(app)], console_timeout=15) as machine:
        boot_messages = [HELLO_WORLD_TEXT, r"Booting Linux on physical CPU"]
        while boot_messages:
            found = machine.console.expect(boot_messages)
            boot_messages.pop(found)


@pytest.mark.parametrize("console_type", ["PL011", "XENEMGCONS"])
def test_hello_world_xen_dom0less(console_type):
    platform = XenHyperlaunch()
    app = HELLO_WORLD.with_extra(defconfig=[f"CONFIG_LIB{console_type}=y"])
    image = platform.build(app)

    with platform.run(domus=[image]) as machine:
        machine.console.expect(HELLO_WORLD_TEXT)

    # Run the image twice in 2 domUs, we should see the message twice
    with platform.run(domus=[image, image]) as machine:
        machine.console.expect(HELLO_WORLD_TEXT)
        machine.console.expect(HELLO_WORLD_TEXT)


def test_hello_world_xen_only_one_pl011():
    platform = XenHyperlaunch()
    app = HELLO_WORLD.with_extra(defconfig=["CONFIG_LIBPL011=y", "CONFIG_LIBUKPRINT_KLVL_INFO=y"])
    image = platform.build(app, dts="""
    /dts-v1/;
    / {
        #address-cells = <2>;
        #size-cells = <2>;

        passthrough {
            compatible = "simple-bus";
            ranges;
            #address-cells = <0x02>;
            #size-cells = <0x02>;

            pl011@9040000 {
                compatible = "arm,pl011", "arm,primecell";
                reg = <0x00 0x9040000 0x00 0x1000>;
                interrupts = <0x00 0x08 0x04>;
                interrupt-parent = <65000>;
                xen,reg = /bits/ 64 <0x09040000 0x1000 0x09040000>;
                xen,path = "/pl011@9040000";
                xen,force-assign-without-iommu;
            };
        };
    };
    """)

    with platform.run(domus=[image]) as machine:
        machine.console.expect("con0: pl011")
        # As of now, only a single pl011 is supported on Xen, see
        # https://github.com/unikraft/unikraft/pull/1970
        # Since we have both the vpl011 and the passthrough uart, the second
        # one should fail to register
        machine.console.expect("Could not initialize pl011")
