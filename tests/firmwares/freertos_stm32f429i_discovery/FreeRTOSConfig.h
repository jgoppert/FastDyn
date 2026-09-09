#ifndef FREERTOS_CONFIG_H
#define FREERTOS_CONFIG_H

/*
 * Reset_Handler deliberately uses the STM32F429's reset-default 16 MHz HSI
 * clock.  Keeping this accurate avoids requiring a board-specific PLL setup
 * before the first FreeRTOS tick and makes the 250/500 ms LED periods correct
 * on a freshly flashed STM32F429I-DISC1.
 */
#define configCPU_CLOCK_HZ                              ( 16000000UL )
#define configTICK_RATE_HZ                              ( 1000U )
#define configMAX_PRIORITIES                             5
#define configMINIMAL_STACK_SIZE                         128
#define configTOTAL_HEAP_SIZE                            ( 32 * 1024 )
#define configMAX_TASK_NAME_LEN                          16

#define configUSE_PREEMPTION                             1
#define configUSE_TIME_SLICING                           1
#define configUSE_PORT_OPTIMISED_TASK_SELECTION          1
#define configUSE_TICKLESS_IDLE                          0
#define configUSE_16_BIT_TICKS                           0
#define configSUPPORT_DYNAMIC_ALLOCATION                 1
#define configSUPPORT_STATIC_ALLOCATION                  0

#define configUSE_TIMERS                                 0
#define configUSE_MUTEXES                                1
#define configUSE_COUNTING_SEMAPHORES                    1
#define configUSE_QUEUE_SETS                             0
#define configUSE_TASK_NOTIFICATIONS                     1
#define configTASK_NOTIFICATION_ARRAY_ENTRIES            1
#define configUSE_TRACE_FACILITY                         1
#define configUSE_STATS_FORMATTING_FUNCTIONS             0
#define configUSE_APPLICATION_TASK_TAG                   0
#define configUSE_NEWLIB_REENTRANT                       0
#define configNUM_THREAD_LOCAL_STORAGE_POINTERS          0
#define configGENERATE_RUN_TIME_STATS                    0

#define configUSE_IDLE_HOOK                              0
#define configUSE_TICK_HOOK                              0
#define configCHECK_FOR_STACK_OVERFLOW                   0
#define configUSE_MALLOC_FAILED_HOOK                     1
#define configCHECK_HANDLER_INSTALLATION                 0

#define configPRIO_BITS                                  4
#define configLIBRARY_LOWEST_INTERRUPT_PRIORITY          15
#define configLIBRARY_MAX_SYSCALL_INTERRUPT_PRIORITY     5
#define configKERNEL_INTERRUPT_PRIORITY                  ( configLIBRARY_LOWEST_INTERRUPT_PRIORITY << 4 )
#define configMAX_SYSCALL_INTERRUPT_PRIORITY             ( configLIBRARY_MAX_SYSCALL_INTERRUPT_PRIORITY << 4 )

#define INCLUDE_vTaskDelay                               1
#define INCLUDE_vTaskDelete                              0
#define INCLUDE_vTaskSuspend                             0

void vAssertCalled(const char *file, int line);
#define configASSERT(expression) \
    do { if ((expression) == 0) vAssertCalled(__FILE__, __LINE__); } while (0)

#endif
