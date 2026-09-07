/* Bare-metal ObjectSan end-to-end fixture: a deliberately derived OOB pointer. */
#include <stdint.h>

volatile uint8_t protected_buffer[32];
volatile uint32_t runtime_offset;

void Reset_Handler(void) {
    volatile uint8_t *derived = protected_buffer;
    runtime_offset = 100;
    derived += runtime_offset;
    *derived = 0x5a; /* Must retain protected_buffer provenance beyond its range. */
    for (;;) {
        __asm__ volatile("wfi");
    }
}

__attribute__((section(".isr_vector"), used))
void (*const vectors[])(void) = {
    (void (*)(void))0x20010000,
    Reset_Handler,
};
