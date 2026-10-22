#!/usr/bin/env python3
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

"""Provision cloud speech without exposing the API key in output or argv."""

import argparse
import json
import pathlib
import re
import time
import serial


def key_from_file(path):
    for line in pathlib.Path(path).read_text().splitlines():
        m = re.fullmatch(r"\s*(?:export\s+)?OPENAI_API_KEY\s*=\s*(.*?)\s*", line)
        if m:
            key = m.group(1).strip().strip("\"'")
            if 24 <= len(key) <= 511 and re.fullmatch(r"sk-[A-Za-z0-9_-]+", key):
                return key
    raise ValueError("No valid OPENAI_API_KEY in the selected file")


def wait_for_ready(port, timeout=20, require_network=False):
    """Opening USB can restart the board before TTS initialization finishes."""
    end = time.monotonic() + timeout
    next_query = 0
    while time.monotonic() < end:
        if time.monotonic() >= next_query:
            port.write(b"\n>tts.status\n")
            next_query = time.monotonic() + 0.5
        line = port.readline().decode("utf-8", "replace").strip()
        if line.startswith("@tts_status "):
            try:
                status = json.loads(line[len("@tts_status ") :])
            except ValueError:
                continue
            if status.get("ready") and (not require_network or status.get("wifi")):
                return status
    raise TimeoutError("Device cloud speech module did not become ready")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", required=True)
    ap.add_argument("--env-file")
    ap.add_argument("--test", action="store_true")
    args = ap.parse_args()
    key = key_from_file(args.env_file) if args.env_file else None
    with serial.Serial(args.port, 115200, timeout=0.1) as s:
        s.dtr = False
        s.rts = False
        s.reset_input_buffer()
        wait_for_ready(s)
        if key:
            s.write(("\n>tts_key=" + key + "\n").encode())
            key = None
            end = time.monotonic() + 10
            while time.monotonic() < end:
                line = s.readline().decode("utf-8", "replace").strip()
                if line == "@tts_key ok":
                    print("Cloud TTS credential saved on device")
                    break
                if line == "@tts_key error":
                    raise RuntimeError(
                        "Device refused the credential; check it is idle"
                    )
            else:
                raise TimeoutError("No credential acknowledgement from device")
        s.write(b"\n>tts.status\n")
        end = time.monotonic() + 5
        while time.monotonic() < end:
            line = s.readline().decode("utf-8", "replace").strip()
            if line.startswith("@tts_status "):
                print(line)
                break
        else:
            raise TimeoutError("No TTS status from device")
        if args.test:
            wait_for_ready(s, require_network=True)
            s.write(b"\n>tts.test\n")
            end = time.monotonic() + 50
            while time.monotonic() < end:
                line = s.readline().decode("utf-8", "replace").strip()
                if "cloud speech self-test:" in line:
                    print(line)
                    if "ready" not in line:
                        raise RuntimeError("Device cloud speech test failed")
                if "cloud speech playback:" in line:
                    print(line)
                    if "complete" not in line:
                        raise RuntimeError(
                            "Device speaker playback failed or speaker is muted"
                        )
                    return
            raise TimeoutError("No cloud speech test result")


if __name__ == "__main__":
    main()
