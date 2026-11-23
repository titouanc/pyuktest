import pytest

from uktest.application import Application
from uktest.platform import QemuAarch64, XenHyperlaunch


@pytest.mark.parametrize(
    "platform",
    [QemuAarch64(), XenHyperlaunch(defconfig=["CONFIG_LIBXENEMGCONS=y"])],
    ids=["qemu", "xen"],
)
def test_get_tracepoint(platform):
    app = Application.from_dir("samples/tracepoint")
    image = platform.build(app)

    with platform.run(image=image) as machine:
        # Wait for application to complete
        machine.console.expect(r"Tracepoint application done \!")
        tracepoints = [(t.tp.name, tuple(t.args)) for t in machine.tracepoints[image]]
        assert tracepoints == [("my_tracepoint", (i,)) for i in range(3)]
