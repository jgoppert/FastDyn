#pragma once
#include <stdint.h>

/*
 * ADC input abstraction.
 *
 * An ADC device model does not manufacture analog samples on its own. Instead
 * it asks the framework for the value that would appear at the front-end for
 * a given (adc_id, channel). The framework returns:
 *   - a bound host-side source, if one has been registered via
 *     api_adc_bind_input(), or
 *   - a sensible default:
 *       * channel 17 (VREFINT):     ~1489 counts (12-bit, 3.3 V reference)
 *       * channel 18 (Temp sensor): ~1000 counts
 *       * every other channel:      0
 *
 * Samples are 12-bit values right-aligned in a uint16_t so they can be handed
 * straight to api_dma_request_data() with len = 2 for DMA transport.
 */

typedef uint16_t (*adc_sample_fn)(void *opaque);

/*
 * Bind a host-side sample source for (adc_id, channel). Passing cb == NULL
 * clears any previous binding for that pair and restores the default.
 */
void api_adc_bind_input(int adc_id, int channel, adc_sample_fn cb, void *opaque);

/*
 * Return the current sample for (adc_id, channel). Resolves a bound source
 * if present, otherwise falls back to the framework default. Never blocks.
 */
uint16_t api_adc_get_sample(int adc_id, int channel);

/*
 * Return only the framework default for (adc_id, channel), ignoring any
 * bound source. Useful for tests and diagnostics.
 */
uint16_t api_adc_default_sample(int adc_id, int channel);
