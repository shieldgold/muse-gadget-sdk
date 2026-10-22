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

#include "muse_tts.h"
#include "muse_tts_protocol.h"
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include "esp_attr.h"
#include "esp_crt_bundle.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_transport.h"
#include "esp_transport_ssl.h"
#include "esp_transport_ws.h"
#include "freertos/FreeRTOS.h"
#include "freertos/idf_additions.h"
#include "freertos/semphr.h"
#include "freertos/stream_buffer.h"
#include "freertos/task.h"
#include "nvs.h"

static const char *TAG = "muse_tts";
static StreamBufferHandle_t s_audio;
static SemaphoreHandle_t s_lock;
static nvs_handle_t s_nvs;
static std::atomic<bool> s_configured{false}, s_running{false}, s_cancelled{false}, s_done{false}, s_failed{false};
static std::atomic<uint32_t> s_generation{0};
static std::atomic<bool> s_ready{false};
EXT_RAM_BSS_ATTR static char s_key[MUSE_TTS_KEY_MAX + 1];
static char *s_request;
#define AUDIO_BYTES (64 * 1024)
#define TTS_LIMIT_US (120LL * 1000000)

static void *external(size_t size)
{
    return heap_caps_malloc(size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
}

static bool active(int64_t deadline)
{
    return !s_cancelled.load() && esp_timer_get_time() < deadline;
}

static void worker(void *)
{
    bool ok = false;
    size_t bytes = 0;
    char *rx = (char *)external(MUSE_TTS_EVENT_MAX + 1);
    uint8_t *pcm = (uint8_t *)external(MUSE_TTS_EVENT_MAX);
    char auth[MUSE_TTS_KEY_MAX + 16];
    snprintf(auth, sizeof(auth), "Bearer %s", s_key);
    auto tls = esp_transport_ssl_init();
    auto ws = tls ? esp_transport_ws_init(tls) : nullptr;
    int64_t deadline = esp_timer_get_time() + TTS_LIMIT_US;
    if (rx && pcm && ws) {
        esp_transport_ssl_crt_bundle_attach(tls, esp_crt_bundle_attach);
        esp_transport_ws_config_t config = {};
        config.ws_path = "/v1/realtime?model=gpt-realtime-2.1-mini";
        config.propagate_control_frames = false;  /* transport answers ping/pong */
        if (esp_transport_ws_set_config(ws, &config) == ESP_OK &&
            esp_transport_ws_set_auth(ws, auth) == ESP_OK && active(deadline) &&
            esp_transport_connect(ws, "api.openai.com", 443, 15000) == 0) {
            const int size = (int)strlen(s_request);
            if (esp_transport_ws_send_raw(ws, (ws_transport_opcodes_t)(WS_TRANSPORT_OPCODES_TEXT | WS_TRANSPORT_OPCODES_FIN),
                                          s_request, size, 10000) == size) {
                size_t used = 0;
                int remaining = 0;
                while (active(deadline)) {
                    if (used == MUSE_TTS_EVENT_MAX) break;
                    int n = esp_transport_read(ws, rx + used, MUSE_TTS_EVENT_MAX - used, 200);
                    if (n < 0) break;
                    if (!n) continue;
                    if (!remaining) {
                        remaining = esp_transport_ws_get_read_payload_len(ws);
                        const int op = esp_transport_ws_get_read_opcode(ws);
                        if ((op != WS_TRANSPORT_OPCODES_TEXT && op != WS_TRANSPORT_OPCODES_CONT) ||
                            remaining < n || (size_t)remaining > MUSE_TTS_EVENT_MAX - used) break;
                    }
                    used += n;
                    remaining -= n;
                    if (remaining || !esp_transport_ws_get_fin_flag(ws)) continue;
                    rx[used] = 0;
                    size_t count = 0;
                    int event = muse_tts_parse_event(rx, used, pcm, MUSE_TTS_EVENT_MAX, &count);
                    used = 0;
                    if (event < 0) { ESP_LOGW(TAG, "service returned invalid audio or an error"); break; }
                    if (event == 2) { ok = bytes > 0; break; }
                    if (event == 1) {
                        size_t offset = 0;
                        while (offset < count && active(deadline)) {
                            offset += xStreamBufferSend(s_audio, pcm + offset, count - offset, pdMS_TO_TICKS(50));
                        }
                        bytes += offset;
                    }
                }
            }
        } else {
            ESP_LOGW(TAG, "connection failed (HTTP %d)", esp_transport_ws_get_upgrade_request_status(ws));
        }
    }
    if (ws) { esp_transport_close(ws); esp_transport_destroy(ws); }
    if (tls) esp_transport_destroy(tls);
    memset(auth, 0, sizeof(auth));
    free(rx); free(pcm);
    muse_tts_protocol_free(s_request); s_request = nullptr;
    ESP_LOGI(TAG, "%s: %u PCM bytes", s_cancelled.load() ? "cancelled" : ok ? "complete" : "failed", (unsigned)bytes);
    s_failed.store(!ok);
    s_done.store(true);
    s_running.store(false);   /* no shared job access after this store */
    vTaskDeleteWithCaps(nullptr);
}

extern "C" void muse_tts_init(void)
{
    s_lock = xSemaphoreCreateMutex();
    s_audio = xStreamBufferCreateWithCaps(AUDIO_BYTES, 2, MALLOC_CAP_SPIRAM);
    if (!s_lock || !s_audio || nvs_open("muse_tts", NVS_READWRITE, &s_nvs) != ESP_OK) {
        ESP_LOGW(TAG, "not available");
        return;
    }
    size_t n = sizeof(s_key);
    if (nvs_get_str(s_nvs, "api_key", s_key, &n) == ESP_OK && muse_tts_key_valid(s_key)) s_configured.store(true);
    s_ready.store(true);
    ESP_LOGI(TAG, "AI-generated Chinese speech: %s", s_configured.load() ? "configured" : "USB key needed");
}

extern "C" bool muse_tts_configured(void) { return s_configured.load(); }
extern "C" bool muse_tts_ready(void) { return s_ready.load(); }
extern "C" bool muse_tts_busy(void) { return s_running.load(); }

extern "C" bool muse_tts_set_key(const char *key)
{
    if (!key || !s_lock || !s_audio || !s_nvs) {
        ESP_LOGW(TAG, "credential store unavailable: lock=%d audio=%d nvs=%d",
                 s_lock != nullptr, s_audio != nullptr, s_nvs != 0);
        return false;
    }
    if (key[0] && !muse_tts_key_valid(key)) {
        ESP_LOGW(TAG, "credential format rejected");
        return false;
    }
    xSemaphoreTake(s_lock, portMAX_DELAY);
    bool ok = false;
    if (!s_running.load()) {
        esp_err_t err = key[0] ? nvs_set_str(s_nvs, "api_key", key) : nvs_erase_key(s_nvs, "api_key");
        if (err == ESP_ERR_NVS_NOT_FOUND && !key[0]) err = ESP_OK;
        if (err == ESP_OK) err = nvs_commit(s_nvs);
        if (err == ESP_OK) {
            memset(s_key, 0, sizeof(s_key));
            memcpy(s_key, key, strlen(key));
            s_configured.store(key[0] != 0);
            ok = true;
        } else ESP_LOGW(TAG, "credential store failed: %s", esp_err_to_name(err));
    } else {
        ESP_LOGW(TAG, "credential store busy");
    }
    xSemaphoreGive(s_lock);
    return ok;
}

extern "C" bool muse_tts_start(const char *text, uint32_t generation)
{
    if (!s_lock || !s_audio || !s_configured.load()) return false;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    bool started = false;
    if (!s_running.load()) {
        s_request = muse_tts_request_json(text);
        if (s_request) {
            xStreamBufferReset(s_audio);
            s_generation.store(generation);
            s_cancelled.store(false); s_done.store(false); s_failed.store(false); s_running.store(true);
            if (xTaskCreatePinnedToCoreWithCaps(worker, "muse_tts", 16 * 1024, nullptr, 4, nullptr, 0,
                                               MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT) == pdPASS) started = true;
            else {
                muse_tts_protocol_free(s_request); s_request = nullptr;
                s_running.store(false); s_done.store(true); s_failed.store(true);
            }
        }
    }
    xSemaphoreGive(s_lock);
    return started;
}

extern "C" void muse_tts_cancel(uint32_t generation)
{
    if (s_generation.load() == generation) s_cancelled.store(true);
}

extern "C" size_t muse_tts_read(uint32_t generation, void *pcm, size_t cap, bool *done, bool *failed)
{
    *done = false; *failed = false;
    if (!s_audio || generation != s_generation.load()) { *done = true; *failed = true; return 0; }
    size_t n = xStreamBufferBytesAvailable(s_audio);
    n = (n < cap ? n : cap) & ~(size_t)1;
    if (n) n = xStreamBufferReceive(s_audio, pcm, n, 0);
    /* A finished worker has published all its bytes before this acquire. */
    if (!s_running.load() && s_done.load() && xStreamBufferBytesAvailable(s_audio) == 0) { *done = true; *failed = s_failed.load(); }
    return n;
}
