/* Bare-metal dynamic ObjectSan fixture with a custom normalized allocator. */
#include <stdint.h>

static uint8_t pool[64];

__attribute__((noinline)) void *pool_alloc(uint32_t size) {
    (void)size;
    return pool;
}

__attribute__((noinline)) void pool_free(void *pointer) {
    (void)pointer;
}

void Reset_Handler(void) {
    void *base = pool_alloc(32);
    volatile uint8_t *object = base;
    object += 100;
    *object = 0x4d; /* dynamic Object #N must retain provenance */
    pool_free(base);
    *(volatile uint8_t *)base = 0x33; /* identity survives free for UAF checks */
    for (;;) {
        __asm__ volatile("wfi");
    }
}

__attribute__((section(".isr_vector"), used))
void (*const vectors[])(void) = {
    (void (*)(void))0x20010000,
    Reset_Handler,
};
