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
#define MUSE_TTS_KEY_MAX 511
#define MUSE_TTS_EVENT_MAX (128 * 1024)
bool muse_tts_key_valid(const char *key);
char *muse_tts_request_json(const char *text);
void muse_tts_protocol_free(void *p);
/* -1 invalid/error, 0 unrelated event, 1 PCM audio, 2 successful end. */
int muse_tts_parse_event(const char *json, size_t len, uint8_t *pcm, size_t cap, size_t *written);
#ifdef __cplusplus
}
#endif
