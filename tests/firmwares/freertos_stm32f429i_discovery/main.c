/*
 * Real FreeRTOS STM32F429I-DISC1 firmware fixture.
 *
 * The board's two user LEDs are on GPIOG pins 13--14:
 *   PG13 green (LD3), PG14 red (LD4).
 *
 * Two equal-priority FreeRTOS tasks toggle one LED each at different periods.
 * The counters are intentionally global and non-static so a debugger, DWARF
 * tool, or FastDyn plugin can observe independent task activity.
 */

#include <stdint.h>

#include "FreeRTOS.h"
#include "task.h"

#define REG32(address) (*(volatile uint32_t *)(address))

#define RCC_AHB1ENR REG32(0x40023830UL)
#define GPIOG_MODER REG32(0x40021800UL)
#define GPIOG_ODR   REG32(0x40021814UL)

#define GPIOG_ENABLE_BIT (1U << 6)
#define LED_GREEN         (1U << 13)
#define LED_RED           (1U << 14)
#define ALL_LEDS          (LED_GREEN | LED_RED)

volatile uint32_t freertos_led_green_toggles;
volatile uint32_t freertos_led_red_toggles;

typedef struct {
    uint32_t pin;
    TickType_t period;
    volatile uint32_t *toggles;
} LedTask;

static const LedTask led_tasks[] = {
    { LED_GREEN, pdMS_TO_TICKS(250), &freertos_led_green_toggles },
    { LED_RED,   pdMS_TO_TICKS(500), &freertos_led_red_toggles },
};

extern uint32_t _sidata;
extern uint32_t _sdata;
extern uint32_t _edata;
extern uint32_t _sbss;
extern uint32_t _ebss;

extern void vPortSVCHandler(void);
extern void xPortPendSVHandler(void);
extern void xPortSysTickHandler(void);

static void fatal(void)
{
    taskDISABLE_INTERRUPTS();
    for (;;) __asm volatile("wfi");
}

void vAssertCalled(const char *file, int line)
{
    (void) file;
    (void) line;
    fatal();
}

void vApplicationMallocFailedHook(void)
{
    fatal();
}

/* FreeRTOS uses memset while allocating task control blocks. */
void *memset(void *destination, int value, unsigned long size)
{
    unsigned char *bytes = destination;
    while (size--) *bytes++ = (unsigned char) value;
    return destination;
}

static void initialise_data_and_bss(void)
{
    uint32_t *source = &_sidata;
    uint32_t *destination = &_sdata;
    while (destination < &_edata) *destination++ = *source++;
    for (destination = &_sbss; destination < &_ebss;) *destination++ = 0;
}

static void initialise_leds(void)
{
    RCC_AHB1ENR |= GPIOG_ENABLE_BIT;
    (void) RCC_AHB1ENR; /* Ensure the peripheral clock write reaches the bus. */

    /* Clear then select general-purpose output mode (01) for PG13--PG14. */
    GPIOG_MODER &= ~((3U << 26) | (3U << 28));
    GPIOG_MODER |=  (1U << 26) | (1U << 28);
    GPIOG_ODR &= ~ALL_LEDS;
}

static void led_task(void *argument)
{
    const LedTask *led = argument;
    for (;;) {
        GPIOG_ODR ^= led->pin;
        ++*led->toggles;
        vTaskDelay(led->period);
    }
}

void Reset_Handler(void)
{
    initialise_data_and_bss();
    initialise_leds();

    for (unsigned int i = 0; i < sizeof(led_tasks) / sizeof(led_tasks[0]); ++i) {
        if (xTaskCreate(led_task, "led", configMINIMAL_STACK_SIZE,
                        (void *) &led_tasks[i], 2, NULL) != pdPASS) {
            fatal();
        }
    }
    vTaskStartScheduler();
    fatal();
}

void Default_Handler(void)
{
    fatal();
}

__attribute__((section(".isr_vector"), used))
void (*const vectors[])(void) = {
    (void (*)(void)) 0x20030000,
    Reset_Handler,
    Default_Handler, Default_Handler, Default_Handler, Default_Handler,
    Default_Handler, Default_Handler, Default_Handler, Default_Handler,
    Default_Handler, vPortSVCHandler, Default_Handler, Default_Handler,
    xPortPendSVHandler, xPortSysTickHandler,
};
