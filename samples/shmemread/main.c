#include <unistd.h>
#include <string.h>
#include <uk/print.h>
#include <xen-arm/shm.h>

#define SHMEM_NAME "shm"

static char *shmem;

static inline int wait_for_change(int timeout)
{
    if (timeout <= 0) {
        return 0;
    }

    while (timeout--) {
        if (shmem[0]) {
            return 1;
        }

        sleep(1);
    }

    return 0;
}

int main(int argc, const char **argv)
{
    (void) argc;
    (void) argv;

    int ret;
    __sz size;

    ret = uk_plat_xen_shm_configure(SHMEM_NAME, true);
    if (ret != 0) {
        uk_pr_err("Unable to configure shm '" SHMEM_NAME "': %d\n", ret);
        return -1;
    }

    ret = uk_plat_xen_shm_get(SHMEM_NAME, (void**) &shmem, &size);
    if (ret != 0) {
        uk_pr_err("Unable to get shm '" SHMEM_NAME "': %d\n", ret);
        return -1;
    }

    uk_pr_info("Got memory region '" SHMEM_NAME "': %p+%lx\n", shmem, size);

    memset(shmem, 0, size);
    uk_pr_info("Waiting for change on '" SHMEM_NAME "'\n");

    if (! wait_for_change(10)) {
        uk_pr_err("No change detected in '%s'", SHMEM_NAME);
        return -1;
    }

    uk_pr_info("Memory updated: '%s'\n", shmem);
    return 0;
}
