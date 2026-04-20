#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""微信语音聊天记录识别工具 - Tencent ASR

Usage:
    python3 retranscribe_tencent_asr.py \
        --input <path> \
        --voice-dir <voices_path> \
        --output <output_path> \
        [--log <log_path>] \
        [--input-format <auto/json/jsonl>] \
        [--limit <count>] \
        [--only-failed]
"""

from __future__ import annotations

import os
import re
import json
import glob
import base64
import argparse
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from dotenv import load_dotenv
from tqdm import tqdm

from tencentcloud.common import credential
from tencentcloud.common.exception.tencent_cloud_sdk_exception import (
    TencentCloudSDKException,
)
from tencentcloud.asr.v20190614 import asr_client, models


VOICE_FAIL_PATTERN = re.compile(r"^\[语音消息\s*-\s*转文字失败[:：].*?\]$")
VOICE_ASR_PATTERN = re.compile(r"^\[语音转文字\]\s*")
VOICE_TYPE_NAMES = {"语音消息"}
VOICE_TYPE_CODES = {34}


def load_records(
    input_path: str, input_format: str
) -> Tuple[List[Dict[str, Any]], str]:
    """
    Output:
    - records
    - actual_format: json / jsonl
    """
    if input_format == "auto":
        input_format = "jsonl" if input_path.lower().endswith(".jsonl") else "json"

    if input_format == "jsonl":
        rows = []
        with open(input_path, "r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                raw = line.strip()
                if not raw:
                    continue
                try:
                    rows.append(json.loads(raw))
                except json.JSONDecodeError as e:
                    raise ValueError(f"Invalid JSONL at line {line_no}: {e}") from e
        return rows, "jsonl"

    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        return data, "json"

    if isinstance(data, dict):
        for key in ("messages", "data", "records", "items"):
            if isinstance(data.get(key), list):
                return data[key], "json"

    raise ValueError("Unsupported JSON structure.")


def save_records(
    output_path: str,
    original_input_path: str,
    records: List[Dict[str, Any]],
    actual_format: str,
) -> None:
    if actual_format == "jsonl":
        with open(output_path, "w", encoding="utf-8") as f:
            for row in records:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return

    with open(original_input_path, "r", encoding="utf-8") as f:
        original = json.load(f)

    if isinstance(original, list):
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
        return

    if isinstance(original, dict):
        for key in ("messages", "data", "records", "items"):
            if isinstance(original.get(key), list):
                original[key] = records
                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(original, f, ensure_ascii=False, indent=2)
                return

    raise ValueError("Unsupported JSON structure when saving.")


def is_voice_message(msg: Dict[str, Any]) -> bool:
    type_name = str(msg.get("type", "")).strip()
    type_code = int(msg.get("localType", -1) or -1)
    return type_name in VOICE_TYPE_NAMES or type_code in VOICE_TYPE_CODES


def should_retranscribe(msg: Dict[str, Any], only_failed: bool) -> bool:
    if not is_voice_message(msg):
        return False

    content = str(msg.get("content", "") or "").strip()
    if only_failed:
        return bool(VOICE_FAIL_PATTERN.match(content))

    return True


def resolve_voice_file(
    input_json_path: str, msg: Dict[str, Any], voice_dir: Optional[str] = None
) -> Optional[str]:
    """
    Format: *_{createTime}_{platformMessageId}.wav
    """
    content = str(msg.get("content", "") or "").strip()

    if content.lower().endswith(".wav"):
        base_dir = os.path.dirname(os.path.abspath(input_json_path))
        candidate = os.path.normpath(os.path.join(base_dir, content))
        if os.path.isfile(candidate):
            return candidate
        candidate2 = os.path.normpath(content)
        if os.path.isfile(candidate2):
            return candidate2

    voice_dir = os.path.abspath(voice_dir)
    if not os.path.isdir(voice_dir):
        return None

    create_time = str(msg.get("createTime", "")).strip()
    platform_message_id = str(msg.get("platformMessageId", "")).strip()
    sender_username = str(msg.get("senderUsername", "")).strip()

    if not create_time or not platform_message_id:
        return None

    if sender_username:
        pattern = os.path.join(
            voice_dir,
            f"*{sender_username}*_{create_time}_{platform_message_id}.wav",
        )
        matches = glob.glob(pattern)
        if matches:
            return matches[0]

    pattern = os.path.join(
        voice_dir,
        f"*_{create_time}_{platform_message_id}.wav",
    )
    matches = glob.glob(pattern)
    if matches:
        return matches[0]

    return None


def detect_voice_format(file_path: str) -> str:
    ext = Path(file_path).suffix.lower()
    if ext == ".wav":
        return "wav"
    if ext == ".mp3":
        return "mp3"
    if ext == ".m4a":
        return "m4a"
    if ext == ".aac":
        return "aac"
    if ext == ".flac":
        return "flac"
    return "wav"


def build_asr_client() -> Tuple[asr_client.AsrClient, str]:
    secret_id = os.getenv("TENCENTCLOUD_SECRET_ID", "").strip()
    secret_key = os.getenv("TENCENTCLOUD_SECRET_KEY", "").strip()
    region = os.getenv("TENCENTCLOUD_REGION", "").strip() or "ap-shanghai"
    engine = os.getenv("TENCENT_ASR_ENGINE", "").strip() or "16k_zh"

    if not secret_id or not secret_key:
        raise RuntimeError(
            "TENCENTCLOUD_SECRET_ID and TENCENTCLOUD_SECRET_KEY are required."
        )

    cred = credential.Credential(secret_id, secret_key)
    client = asr_client.AsrClient(cred, region)
    return client, engine


def tencent_sentence_recognition(
    client: asr_client.AsrClient,
    engine: str,
    file_path: str,
) -> str:
    """
    Tencent ASR
    """
    req = models.SentenceRecognitionRequest()

    with open(file_path, "rb") as f:
        audio_bytes = f.read()

    req.ProjectId = 0
    req.SubServiceType = 2
    req.EngSerViceType = engine
    req.SourceType = 1
    req.VoiceFormat = detect_voice_format(file_path)
    req.UsrAudioKey = os.path.basename(file_path)
    req.Data = base64.b64encode(audio_bytes).decode("utf-8")
    req.DataLen = len(audio_bytes)

    resp = client.SentenceRecognition(req)
    text = getattr(resp, "Result", "") or ""
    return text.strip()


def normalize_asr_text(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"\s+", "", text)
    return text


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="腾讯云 ASR 识别语音并回填 JSON")
    parser.add_argument("--input", required=True, help="输入 JSON / JSONL 文件路径")
    parser.add_argument(
        "--input-format",
        default="auto",
        choices=["auto", "json", "jsonl"],
        help="输入格式，默认 auto",
    )
    parser.add_argument(
        "--voice-dir",
        required=True,
        help="语音文件目录",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="输出更新后的 JSON / JSONL 文件路径",
    )
    parser.add_argument(
        "--log",
        default="retranscribe_log.jsonl",
        help="输出修改日志 JSONL 路径，默认 retranscribe_log.jsonl",
    )
    parser.add_argument(
        "--only-failed",
        action="store_true",
        help="仅识别“转文字失败”的语音消息",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="仅处理前 N 条命中的语音消息，用于测试",
    )
    return parser.parse_args()


def main() -> None:
    load_dotenv()
    args = parse_args()

    records, actual_format = load_records(args.input, args.input_format)
    client, engine = build_asr_client()

    changed_logs: List[Dict[str, Any]] = []
    hit_count = 0
    change_count = 0
    skip_no_file = 0
    skip_empty_asr = 0
    fail_count = 0

    iterable = tqdm(records, desc="Re-transcribing voices")

    for idx, msg in enumerate(iterable):
        if not should_retranscribe(msg, only_failed=args.only_failed):
            continue

        if args.limit is not None and hit_count >= args.limit:
            break

        hit_count += 1

        voice_path = resolve_voice_file(args.input, msg, args.voice_dir)
        old_content = str(msg.get("content", "") or "")

        if not voice_path:
            changed_logs.append(
                {
                    "index": idx,
                    "platformMessageId": msg.get("platformMessageId"),
                    "createTime": msg.get("createTime"),
                    "status": "skip_no_file",
                    "before": old_content,
                    "after": old_content,
                    "voice_path": None,
                }
            )
            skip_no_file += 1
            continue

        try:
            raw_text = tencent_sentence_recognition(client, engine, voice_path)
            new_text = normalize_asr_text(raw_text)

            if not new_text:
                changed_logs.append(
                    {
                        "index": idx,
                        "platformMessageId": msg.get("platformMessageId"),
                        "createTime": msg.get("createTime"),
                        "status": "skip_empty_asr",
                        "before": old_content,
                        "after": old_content,
                        "voice_path": voice_path,
                    }
                )
                skip_empty_asr += 1
                continue

            final_content = f"[语音转文字] {new_text}"

            msg["content"] = final_content
            change_count += 1

            changed_logs.append(
                {
                    "index": idx,
                    "platformMessageId": msg.get("platformMessageId"),
                    "createTime": msg.get("createTime"),
                    "status": "updated",
                    "before": old_content,
                    "after": final_content,
                    "voice_path": voice_path,
                }
            )

        except TencentCloudSDKException as e:
            fail_count += 1
            changed_logs.append(
                {
                    "index": idx,
                    "platformMessageId": msg.get("platformMessageId"),
                    "createTime": msg.get("createTime"),
                    "status": "asr_error",
                    "error": str(e),
                    "before": old_content,
                    "after": old_content,
                    "voice_path": voice_path,
                }
            )

    save_records(args.output, args.input, records, actual_format)

    with open(args.log, "w", encoding="utf-8") as f:
        for row in changed_logs:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print("[OK] finished")
    print(f"[OK] output: {args.output}")
    print(f"[OK] log: {args.log}")
    print(
        json.dumps(
            {
                "matched_voice_messages": hit_count,
                "updated": change_count,
                "skip_no_file": skip_no_file,
                "skip_empty_asr": skip_empty_asr,
                "asr_error": fail_count,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
