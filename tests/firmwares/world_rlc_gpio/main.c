/*
 * FastDyn world_model demo firmware.
 *
 * The firmware believes it owns one digital output pin and one analog input.
 * Neither is implemented here: both are naked stubs whose entry addresses are
 * bound to world virtuals by configs/world_rlc_gpio.toml. The pin drives the
 * RLC circuit's supply voltage, and the analog input samples the capacitor.
 *
 * The pin toggles continuously, so the firmware never exits.
 *
 * Build: tests/firmwares/world_rlc_gpio/build.sh
 */
#include <stdint.h>

/* ---- Semihosting -------------------------------------------------------- */

static void sh_write0(const char *text)
{
    register int r0 __asm__("r0") = 0x04; /* SYS_WRITE0 */
    register const char *r1 __asm__("r1") = text;
    /* Semihosting returns a value in r0, so r0 is an in/out operand. Marking
     * it input-only lets the compiler assume r0 still holds the opcode on a
     * later call, which it does not. */
    __asm__ volatile("bkpt 0xAB" : "+r"(r0) : "r"(r1) : "memory");
}

static char *u32_to_dec(char *out, uint32_t value, int width)
{
    char digits[12];
    int count = 0;
    do {
        digits[count++] = (char)('0' + (value % 10u));
        value /= 10u;
    } while (value);
    while (count < width) {
        digits[count++] = ' ';
    }
    while (count--) {
        *out++ = digits[count];
    }
    return out;
}

/* ---- The two world-backed pins ------------------------------------------ */

/*
 * Naked so the function body cannot disturb r0. The world virtual bound to
 * world_gpio_write reads the level from r0; the virtual bound to
 * world_adc_read writes millivolts into r0, and `bx lr` returns it.
 */
__attribute__((naked, noinline)) void world_gpio_write(int level)
{
    (void)level;
    __asm__ volatile("bx lr");
}

__attribute__((naked, noinline)) int world_adc_read(void)
{
    __asm__ volatile("bx lr");
}

/* ---- Application -------------------------------------------------------- */

/* Burn guest time so the world has an interval to integrate over. */
static void delay(volatile uint32_t iterations)
{
    while (iterations--) {
        __asm__ volatile("nop");
    }
}

#define SAMPLES_PER_LEVEL 8
#define DELAY_ITERATIONS 30000u

/*
 * Toggle the pin forever, sampling the capacitor between edges. A continuously
 * running firmware is the useful shape here: the RLC keeps charging and
 * discharging so there is always something to watch, and under a
 * co-simulation master the run lasts exactly as long as the master grants
 * rather than ending underneath it.
 *
 * Stop it with Ctrl-C, or bound it from the master by granting a fixed number
 * of slices.
 */
int main(void)
{
    char line[96];
    int level = 0;
    uint32_t sample_index = 0;

    sh_write0("world_rlc_gpio: driving an RLC circuit from a GPIO pin\r\n");
    sh_write0("  sample  pin      capacitor\r\n");

    /*
     * One flat loop, with the pin edge derived from the sample counter rather
     * than from a nested trip count. A nested loop bounded by a comparison
     * against a running counter can be wedged permanently by a single
     * miscount -- the counter steps past the bound and the inner loop never
     * exits, leaving the pin stuck and the physics frozen. Deriving the edge
     * from the counter cannot wedge: at worst an edge shifts by one sample.
     */
    for (;;) {
        if ((sample_index % SAMPLES_PER_LEVEL) == 0) {
            level = !level;
            world_gpio_write(level);
        }
        {
            delay(DELAY_ITERATIONS);
            int millivolts = world_adc_read();
            char *cursor = line;
            *cursor++ = ' ';
            *cursor++ = ' ';
            cursor = u32_to_dec(cursor, sample_index++, 4);
            *cursor++ = ' ';
            *cursor++ = ' ';
            *cursor++ = (char)('0' + level);
            *cursor++ = ' ';
            *cursor++ = ' ';
            cursor = u32_to_dec(cursor, (uint32_t)millivolts, 8);
            *cursor++ = ' ';
            *cursor++ = 'm';
            *cursor++ = 'V';
            *cursor++ = '\r';
            *cursor++ = '\n';
            *cursor = '\0';
            sh_write0(line);
        }
    }
}

/* ---- Startup ------------------------------------------------------------ */

extern uint32_t _sdata, _edata, _sidata, _sbss, _ebss, _estack;

void Reset_Handler(void)
{
    uint32_t *src = &_sidata;
    uint32_t *dst = &_sdata;
    while (dst < &_edata) {
        *dst++ = *src++;
    }
    for (dst = &_sbss; dst < &_ebss; dst++) {
        *dst = 0;
    }
    main();
    for (;;) {
    }
}

static void Default_Handler(void)
{
    for (;;) {
    }
}

__attribute__((section(".isr_vector"), used))
void (*const vector_table[])(void) = {
    (void (*)(void))&_estack,
    Reset_Handler,
    Default_Handler, /* NMI */
    Default_Handler, /* HardFault */
};
