/* Cortex-M VariableWatch end-to-end fixture. */
#include <stdint.h>

struct MotorState {
    uint32_t rpm;
    uint8_t enabled;
    uint8_t padding[3];
    uint32_t temperature;
};

volatile struct MotorState motor_state;

__attribute__((noinline)) static void set_temperature(volatile struct MotorState *state,
                                                      uint32_t value) {
    state->temperature = value;
}

void Reset_Handler(void) {
    motor_state.rpm = 1000;             /* Must not trigger a temperature watch. */
    motor_state.temperature = 35;
    set_temperature(&motor_state, 37);  /* Indirect field access must trigger. */
    (void)motor_state.temperature;
    for (;;) {
        __asm__ volatile("wfi");
    }
}

__attribute__((section(".isr_vector"), used))
void (*const vectors[])(void) = {
    (void (*)(void))0x20010000,
    Reset_Handler,
};
