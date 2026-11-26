from uktest.application import Application
from uktest.platform import XenHyperlaunch


def test_highres_timer():
    platform = XenHyperlaunch()
    image = platform.build(Application.from_dir("samples/hrtimer"))

    with platform.run(image=image) as machine:
        # Wait for application to complete
        for i in range(1, 20):
            machine.console.expect(rf">>> CALLBACK INVOKED \({i}\) <<<")
