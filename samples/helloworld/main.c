#include <stdio.h>

int main(int argc, char **argv)
{
    printf("argc=%d argv=[", argc);
    for (int i=0; i<argc; i++) {
        if (i) {
            printf(", ");
        }
        printf("%s", argv[i]);
    }
    printf("]\n");

    printf("Hello Unikraft world!\n");

	return 0;
}
