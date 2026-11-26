#include <unistd.h>
#include <uk/hrtimer.h>
#include <uk/print.h>
#include <uk/plat/time.h>

/* nolibc does not declare usleep(); it is provided by posix-time */
int usleep(useconds_t usec);

static struct uk_hrtimer timer;

#define NSEC (1)
#define USEC (1000 * NSEC)
#define MSEC (1000 * USEC)
#define SEC  (1000 * MSEC)

static inline void resched(unsigned long delay)
{
    timer.expires += delay;

    if (uk_hrtimer_start(&timer)) {
        uk_pr_err("Unable to start timer");
    }
}

int callback(void *priv)
{
    (void) priv;
    static int count;

    count++;
    uk_pr_info(">>> CALLBACK INVOKED (%d) <<<\n", count);

    if (count <= 18) {
        resched(50 * MSEC);
    }

    return 0;
}

int main(int argc, char **argv)
{
    (void) argc;
    (void) argv;

    uk_hrtimer_queue_init();

    unsigned long start = ukplat_monotonic_clock();
    uk_hrtimer_init(&timer, callback, NULL);
    timer.expires = start;

    resched(100 * MSEC);

    do {
        if (uk_hrtimer_is_pending()) {
            uk_hrtimer_run_functions();
        } else {
            usleep(1000);
        }
    } while (ukplat_monotonic_clock() - start < 2*SEC);

	return 0;
}
