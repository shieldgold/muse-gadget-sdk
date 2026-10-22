/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
void muse_tts_init(void);
bool muse_tts_ready(void);
bool muse_tts_configured(void);
bool muse_tts_busy(void);
bool muse_tts_set_key(const char *key);
bool muse_tts_start(const char *text, uint32_t generation);
void muse_tts_cancel(uint32_t generation);
/* Nonblocking PCM16 mono at 24 kHz. Status refers only to this generation. */
size_t muse_tts_read(uint32_t generation, void *pcm, size_t cap, bool *done, bool *failed);
#ifdef __cplusplus
}
#endif
