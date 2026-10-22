/* Copyright (c) Meta Platforms, Inc. and affiliates.
 * SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
/* Speech-only gain, up to 3x (+9.5 dB), with 30000 peak headroom.
 * Initialize *gain to 3 at each new job. Keeps length and sample rate intact. */
void muse_tts_boost(int16_t *pcm, size_t frames, float *gain);
#ifdef __cplusplus
}
#endif
