#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""将 QQ/其他聊天文本转换为标准 chunks 文件

格式：
- 微信 WeFlow：优先使用 `tools/wechat_parser.py` 直接生成 chunks.jsonl
- QQ/其他来源：用本脚本先转为标准 chunks.jsonl，再执行 Milvus 入库

Usage:
    python3 build_chunks_generic.py \
        --input <path> \
        --output <chunks_jsonl_path> \
        [--source <qq/other>] \
        [--chat-id <id>] \
        [--format <auto/qq-txt/plain-text/jsonl>] \
        [--chunk-chars <n>] \
        [--overlap-chars <n>]
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

QQ_LINE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s+(.+?)(?:\((\d+)\))?\s*$"
)


def parse_ts(dt_str: str) -> int:
    try:
        return int(datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S").timestamp())
    except Exception:
        return 0


def sanitize_text(text: Any) -> str:
    if text is None:
        return ""
    return str(text).replace("\r\n", "\n").replace("\r", "\n").strip()


def detect_input_format(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".jsonl":
        return "jsonl"
    if ext in {".txt", ".log"}:
        return "qq-txt"
    return "plain-text"


def parse_qq_txt_messages(path: str) -> List[Dict[str, Any]]:
    messages: List[Dict[str, Any]] = []
    current_msg: Optional[Dict[str, Any]] = None

    with open(path, "r", encoding="utf-8", errors="ignore") as file_obj:
        for line in file_obj:
            line = line.rstrip("\n")
            match = QQ_LINE_RE.match(line)
            if match:
                if current_msg and current_msg.get("content"):
                    messages.append(current_msg)
                timestamp, sender, _ = match.groups()
                current_msg = {
                    "timestamp": parse_ts(timestamp),
                    "sender": sender.strip(),
                    "content": "",
                }
                continue

            if current_msg and line.strip() and not line.startswith("==="):
                if current_msg["content"]:
                    current_msg["content"] += "\n"
                current_msg["content"] += line

    if current_msg and current_msg.get("content"):
        messages.append(current_msg)
    return messages


def load_plain_text(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="ignore") as file_obj:
        return file_obj.read()


def load_jsonl_rows(path: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as file_obj:
        for line_no, line in enumerate(file_obj, start=1):
            raw = line.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"JSONL 第 {line_no} 行格式错误：{exc}") from exc
            if isinstance(obj, dict):
                rows.append(obj)
    return rows


def iter_text_windows(
    text: str, chunk_chars: int, overlap_chars: int
) -> Iterable[Tuple[int, str]]:
    clean = sanitize_text(text)
    if not clean:
        return

    step = max(chunk_chars - overlap_chars, 1)
    idx = 0
    start = 0
    while start < len(clean):
        end = min(start + chunk_chars, len(clean))
        window = clean[start:end].strip()
        if window:
            yield idx, window
            idx += 1
        start += step


def records_to_chunks(
    records: List[Dict[str, Any]],
    source: str,
    chat_id: str,
    chunk_chars: int,
    overlap_chars: int,
) -> List[Dict[str, Any]]:
    lines: List[str] = []
    ts_list: List[int] = []

    for row in records:
        sender = sanitize_text(row.get("sender", "unknown")) or "unknown"
        content = sanitize_text(row.get("content", row.get("text", "")))
        if not content:
            continue
        lines.append(f"[{sender}] {content}")
        ts = int(row.get("timestamp", 0) or 0)
        if ts > 0:
            ts_list.append(ts)

    text_blob = "\n".join(lines)
    return text_to_chunks(
        text_blob,
        source=source,
        chat_id=chat_id,
        chunk_chars=chunk_chars,
        overlap_chars=overlap_chars,
        start_ts=min(ts_list) if ts_list else 0,
        end_ts=max(ts_list) if ts_list else 0,
        dominant_speaker="unknown",
    )


def text_to_chunks(
    text: str,
    source: str,
    chat_id: str,
    chunk_chars: int,
    overlap_chars: int,
    start_ts: int = 0,
    end_ts: int = 0,
    dominant_speaker: str = "unknown",
) -> List[Dict[str, Any]]:
    chunks: List[Dict[str, Any]] = []
    for idx, window_text in iter_text_windows(text, chunk_chars, overlap_chars):
        chunks.append(
            {
                "chunk_id": f"{source}_{chat_id}_{idx:06d}",
                "source": source,
                "chat_id": chat_id,
                "session_id": f"{chat_id}_s0001",
                "start_ts": start_ts,
                "end_ts": end_ts,
                "dominant_speaker": dominant_speaker,
                "turn_count": 1,
                "message_count": 1,
                "has_image": False,
                "has_video": False,
                "has_sticker": False,
                "has_voice": False,
                "has_voice_asr": False,
                "text_for_embedding": window_text,
                "display_text": window_text,
            }
        )
    return chunks


def rows_to_chunks(
    rows: List[Dict[str, Any]], source: str, chat_id: str
) -> List[Dict[str, Any]]:
    chunks: List[Dict[str, Any]] = []
    for idx, row in enumerate(rows, start=1):
        text = sanitize_text(
            row.get("text_for_embedding")
            or row.get("display_text")
            or row.get("text")
            or row.get("content")
            or row.get("raw_text")
        )
        if not text:
            continue

        resolved_chat_id = str(
            row.get("chat_id") or row.get("conversation_id") or chat_id
        )
        resolved_source = str(row.get("source") or source)
        chunk_id = str(
            row.get("chunk_id")
            or row.get("id")
            or f"{resolved_source}_{resolved_chat_id}_{idx:06d}"
        )

        chunks.append(
            {
                "chunk_id": chunk_id,
                "source": resolved_source,
                "chat_id": resolved_chat_id,
                "session_id": str(row.get("session_id", f"{resolved_chat_id}_s0001")),
                "start_ts": int(row.get("start_ts", row.get("timestamp", 0)) or 0),
                "end_ts": int(row.get("end_ts", row.get("timestamp", 0)) or 0),
                "dominant_speaker": str(
                    row.get("dominant_speaker", row.get("sender", "unknown"))
                ),
                "turn_count": int(row.get("turn_count", 1) or 1),
                "message_count": int(row.get("message_count", 1) or 1),
                "has_image": bool(row.get("has_image", False)),
                "has_video": bool(row.get("has_video", False)),
                "has_sticker": bool(row.get("has_sticker", False)),
                "has_voice": bool(row.get("has_voice", False)),
                "has_voice_asr": bool(row.get("has_voice_asr", False)),
                "text_for_embedding": text,
                "display_text": sanitize_text(row.get("display_text") or text),
            }
        )
    return chunks


def write_jsonl(path: str, rows: List[Dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as file_obj:
        for row in rows:
            file_obj.write(json.dumps(row, ensure_ascii=False) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="将 QQ/其他文本转换为标准 chunks 文件")
    parser.add_argument("--input", required=True, help="输入文件路径")
    parser.add_argument("--output", required=True, help="输出 chunks.jsonl 路径")
    parser.add_argument(
        "--source", default="other", help="来源标记：qq / other / 自定义"
    )
    parser.add_argument("--chat-id", default="default_chat", help="聊天标识 chat_id")
    parser.add_argument(
        "--format",
        default="auto",
        choices=["auto", "qq-txt", "plain-text", "jsonl"],
        help="输入格式；默认 auto 自动判断",
    )
    parser.add_argument(
        "--chunk-chars", type=int, default=800, help="每块最大字符数，默认 800"
    )
    parser.add_argument(
        "--overlap-chars", type=int, default=80, help="相邻块重叠字符数，默认 80"
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not os.path.exists(args.input):
        raise FileNotFoundError(f"输入文件不存在：{args.input}")

    input_format = args.format
    if input_format == "auto":
        input_format = detect_input_format(args.input)

    if input_format == "qq-txt":
        messages = parse_qq_txt_messages(args.input)
        chunks = records_to_chunks(
            messages,
            source=args.source,
            chat_id=args.chat_id,
            chunk_chars=args.chunk_chars,
            overlap_chars=args.overlap_chars,
        )
    elif input_format == "jsonl":
        rows = load_jsonl_rows(args.input)
        chunks = rows_to_chunks(rows, source=args.source, chat_id=args.chat_id)
    else:
        text = load_plain_text(args.input)
        chunks = text_to_chunks(
            text,
            source=args.source,
            chat_id=args.chat_id,
            chunk_chars=args.chunk_chars,
            overlap_chars=args.overlap_chars,
        )

    if not chunks:
        raise RuntimeError("未生成任何 chunk，请检查输入文件内容")

    write_jsonl(args.output, chunks)
    print(f"[OK] 已生成 {len(chunks)} 条 chunks：{args.output}")


if __name__ == "__main__":
    main()
