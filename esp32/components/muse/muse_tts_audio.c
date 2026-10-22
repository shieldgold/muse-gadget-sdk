/* Copyright (c) Meta Platforms, Inc. and affiliates.
 * SPDX-License-Identifier: Apache-2.0 */
#include "muse_tts_audio.h"

void muse_tts_boost(int16_t *pcm, size_t frames, float *gain)
{
    if (!pcm || !gain || !frames) return;
    int32_t peak = 0;
    for (size_t i = 0; i < frames; i++) {
        int32_t value = pcm[i];
        if (value < 0) value = -value;  /* includes INT16_MIN */
        if (value > peak) peak = value;
    }
    float target = peak > 10000 ? 30000.0f / peak : 3.0f;
    if (*gain > target) *gain = target;  /* instant attack prevents clipping */
    else *gain += (target - *gain) * ((float)frames / (frames + 6000.0f));
    for (size_t i = 0; i < frames; i++) pcm[i] = (int16_t)(pcm[i] * *gain);
}
