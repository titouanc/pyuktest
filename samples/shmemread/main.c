#include <unistd.h>
#include <string.h>
#include <uk/print.h>
#include <xen-arm/shm.h>

#define SHMEM_NAME "shm"
#define SHMEM_SIZE 16384

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

    int ret = uk_plat_xen_shm_get(SHMEM_NAME, SHMEM_SIZE, false, 0, (void**) &shmem);
    if (ret != 0) {
        uk_pr_err("Unable to map shm '" SHMEM_NAME "'\n");
        return -1;
    }

    memset(shmem, 0, SHMEM_SIZE);
    uk_pr_info("Memory mapped '" SHMEM_NAME "' -> %p\n", shmem);

    if (! wait_for_change(10)) {
        uk_pr_err("No change detected in '%s'", SHMEM_NAME);
        return -1;
    }

    uk_pr_info("Memory updated: '%s'\n", shmem);
    return 0;
}
