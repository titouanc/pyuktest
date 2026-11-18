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
