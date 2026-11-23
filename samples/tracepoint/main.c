/* Enable tracing for this file, must be placed before includes */
#define UK_DEBUG_TRACE

#include <stdio.h>
#include <uk/trace.h>

/* Define a tracepoint with optional arguments to be formatted */
UK_TRACEPOINT(my_tracepoint, "%d", int)

int main(int argc, char **argv)
{
    for (int i=0; i<3; i++) {
        /* Emit a trace event at this point */
        my_tracepoint(i);
    }

    printf("Tracepoint application done !\n");

    return 0;
}
