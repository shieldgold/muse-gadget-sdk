# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import base64, ctypes, json, pathlib, subprocess, tempfile, unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class TtsProtocolTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = ROOT / "components/muse/muse_tts_protocol.c"
        assert source.is_file(), "Cloud TTS protocol implementation is missing"
        cls.tmp = tempfile.TemporaryDirectory()
        lib = pathlib.Path(cls.tmp.name) / "tts.dylib"
        cj = ROOT / "managed_components/espressif__cjson/cJSON"
        r = subprocess.run(
            [
                "cc",
                "-shared",
                "-fPIC",
                "-std=c11",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-I",
                str(cj),
                str(source),
                str(cj / "cJSON.c"),
                "-o",
                str(lib),
            ],
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0, r.stderr
        cls.lib = ctypes.CDLL(str(lib))
        cls.lib.muse_tts_key_valid.argtypes = [ctypes.c_char_p]
        cls.lib.muse_tts_key_valid.restype = ctypes.c_bool
        cls.lib.muse_tts_request_json.argtypes = [ctypes.c_char_p]
        cls.lib.muse_tts_request_json.restype = ctypes.c_void_p
        cls.lib.muse_tts_protocol_free.argtypes = [ctypes.c_void_p]
        cls.lib.muse_tts_parse_event.argtypes = [
            ctypes.c_char_p,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        cls.lib.muse_tts_parse_event.restype = ctypes.c_int

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def request(self, text):
        p = self.lib.muse_tts_request_json(text)
        self.assertTrue(p)
        try:
            return json.loads(ctypes.string_at(p))
        finally:
            self.lib.muse_tts_protocol_free(p)

    def event(self, obj, cap=4096):
        data = json.dumps(obj).encode() if not isinstance(obj, bytes) else obj
        out = ctypes.create_string_buffer(cap)
        n = ctypes.c_size_t(0)
        kind = self.lib.muse_tts_parse_event(data, len(data), out, cap, ctypes.byref(n))
        return kind, out.raw[: n.value]

    def test_key_cannot_inject_headers(self):
        self.assertTrue(self.lib.muse_tts_key_valid(b"sk-proj_" + b"A" * 100))
        for k in [
            b"",
            b"sk-short",
            b"sk-" + b"a" * 600,
            b"sk-" + b"a" * 30 + b"\r\nX:evil",
            b"sk-" + b"a" * 30 + b" ",
            b"other-" + b"a" * 40,
        ]:
            self.assertFalse(self.lib.muse_tts_key_valid(k))

    def test_chinese_and_json_are_preserved(self):
        text = '你好，"Muse"！\n请原样朗读。'
        r = self.request(text.encode())
        self.assertEqual(r["type"], "response.create")
        p = r["response"]
        self.assertEqual(p["conversation"], "none")
        self.assertEqual(p["output_modalities"], ["audio"])
        self.assertEqual(p["input"][0]["content"][0]["text"], text)
        self.assertEqual(
            p["audio"]["output"]["format"], {"type": "audio/pcm", "rate": 24000}
        )
        self.assertNotIn("tools", p)

    def test_truncated_utf8_is_not_sent(self):
        r = self.request("你好".encode() + b"\xe4\xb8")
        self.assertEqual(r["response"]["input"][0]["content"][0]["text"], "你好")

    def test_pcm_delta_is_decoded(self):
        pcm = bytes([0, 0, 255, 127, 0, 128, 42, 0])
        self.assertEqual(
            self.event(
                {
                    "type": "response.output_audio.delta",
                    "delta": base64.b64encode(pcm).decode(),
                }
            ),
            (1, pcm),
        )

    def test_invalid_audio_is_rejected(self):
        for value in ["!", "AAA", "A===", "AA==AAAA", "AB==", "AAAA", "A" * 20000]:
            self.assertEqual(
                self.event({"type": "response.output_audio.delta", "delta": value})[0],
                -1,
            )

    def test_completion_and_error(self):
        self.assertEqual(
            self.event({"type": "response.done", "response": {"status": "completed"}})[
                0
            ],
            2,
        )
        for obj in [
            {"type": "error"},
            {"type": "response.done", "response": {"status": "failed"}},
            {"type": "response.done"},
            b"{oops",
            b"[]",
            b"x" * 140000,
        ]:
            self.assertEqual(self.event(obj)[0], -1)
        self.assertEqual(self.event({"type": "session.created"})[0], 0)


class CredentialStoreTest(unittest.TestCase):
    def test_store_failure_and_busy_state_preserve_previous_key(self):
        source = (ROOT / "components/muse/muse_tts.cpp").read_text()
        start = source.index('extern "C" bool muse_tts_set_key(')
        brace = source.index("{", start)
        depth = 0
        for end in range(brace, len(source)):
            depth += (source[end] == "{") - (source[end] == "}")
            if not depth:
                function = source[start : end + 1]
                break
        with tempfile.TemporaryDirectory() as directory:
            directory = pathlib.Path(directory)
            harness = directory / "store.cpp"
            harness.write_text(
                r"""#include <atomic>
#include <cassert>
#include <cstdio>
#include <cstring>
#define ESP_LOGW(tag, ...) ((void)(tag), (void)sizeof(printf(__VA_ARGS__)))
#define portMAX_DELAY 0
#define ESP_OK 0
#define ESP_ERR_NVS_NOT_FOUND 1
using esp_err_t = int;
static const char *TAG = "test";
static void *s_lock = (void*)1, *s_audio = (void*)1;
static int s_nvs = 1, error = 0, writes = 0, commits = 0;
static std::atomic<bool> s_running{false}, s_configured{false};
static char s_key[512];
static void xSemaphoreTake(void*, int) {}
static void xSemaphoreGive(void*) {}
static bool muse_tts_key_valid(const char *key) { return !strcmp(key, "valid-key"); }
static int nvs_set_str(int, const char*, const char*) { writes++; return error; }
static int nvs_erase_key(int, const char*) { writes++; return error; }
static int nvs_commit(int) { commits++; return error; }
const char *esp_err_to_name(int) { return "test error"; }
"""
                + function
                + r"""
int main() {
    assert(muse_tts_set_key("valid-key"));
    assert(s_configured && !strcmp(s_key, "valid-key") && writes == 1 && commits == 1);
    s_running = true;
    assert(!muse_tts_set_key(""));
    assert(s_configured && !strcmp(s_key, "valid-key") && writes == 1);
    s_running = false; error = 9;
    assert(!muse_tts_set_key(""));
    assert(s_configured && !strcmp(s_key, "valid-key"));
    error = 0;
    assert(muse_tts_set_key(""));
    assert(!s_configured && !s_key[0]);
    assert(!muse_tts_set_key("invalid"));
    s_nvs = 0;
    assert(!muse_tts_set_key("valid-key"));
}
"""
            )
            binary = directory / "store"
            result = subprocess.run(
                [
                    "c++",
                    "-std=c++17",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    str(harness),
                    "-o",
                    str(binary),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)


class ProvisioningStartupTest(unittest.TestCase):
    def test_waits_for_initialized_status_and_network_before_test(self):
        import importlib.util
        import sys
        import types
        from unittest import mock

        spec = importlib.util.spec_from_file_location(
            "configure_tts", ROOT / "tools/muse/configure_tts.py"
        )
        module = importlib.util.module_from_spec(spec)
        with mock.patch.dict(sys.modules, {"serial": types.SimpleNamespace()}):
            spec.loader.exec_module(module)

        class Port:
            def __init__(self):
                self.lines = [
                    b'@tts_status {"ready":false,"configured":false}\n',
                    b"booting\n",
                    b'@tts_status {"ready":true,"configured":true,"wifi":false}\n',
                    b'@tts_status {"ready":true,"configured":true,"wifi":true}\n',
                ]
                self.writes = []

            def write(self, value):
                self.writes.append(value)

            def readline(self):
                return self.lines.pop(0)

        port = Port()
        status = module.wait_for_ready(port, require_network=True)
        self.assertTrue(status["configured"])
        self.assertEqual(port.lines, [])
        self.assertTrue(all(value == b"\n>tts.status\n" for value in port.writes))
