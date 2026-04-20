#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""微信聊天记录解析器 - WeFlow Format

支持 WeFlow 导出的 JSON/JSONL 格式聊天记录。

Usage:
    python3 wechat_parser.py \
        --input <path> \
        --output-dir <output_path> \
        --chat-id <name> \
        [--input-format <auto/json/jsonl>] \
        [--my-sender <id>] \
        [--session-gap-minutes <minutes>] \
        [--merge-gap-seconds <seconds>] \
        [--chunk-turns <count>] \
        [--chunk-overlap <count>] \
        [--media-root <path>]
        
Output:
    - normalized_messages.jsonl
    - turns.jsonl
    - chunks.jsonl
    - media_items.jsonl
    - stats.json
    - analysis_report.json
    - analysis_report.md
"""

from __future__ import annotations

import os
import re
import json
import math
import argparse
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class NormalizedMessage:
    raw_index: int
    message_id: str
    local_id: int
    chat_id: str
    timestamp: int
    datetime_str: str
    sender_id: str
    sender_name: str
    role: str  # me / target / other
    is_send: bool
    type_name: str
    type_code: int
    kind: str  # text / voice_asr / image_placeholder / video_placeholder / sticker_placeholder / other
    content_raw: str
    content_for_embedding: str
    placeholder_text: Optional[str]
    has_media: bool
    media_id: Optional[str]
    media_type: Optional[str]  # image / video / sticker / voice / None
    source: str
    sender_avatar_key: str
    emoji_md5: Optional[str]
    emoji_cdn_url: Optional[str]
    is_short: bool


@dataclass
class MediaItem:
    media_id: str
    message_id: str
    local_id: int
    chat_id: str
    timestamp: int
    datetime_str: str
    sender_id: str
    sender_name: str
    role: str
    media_type: str  # image / video / sticker / voice
    type_name: str
    type_code: int
    placeholder_text: str
    file_path: Optional[str]
    cdn_url: Optional[str]
    emoji_md5: Optional[str]
    session_id: Optional[str]
    chunk_ids: List[str]
    vision_status: str  # pending / done / failed
    vision_caption: Optional[str]
    image_vector_status: str  # pending / done / failed


@dataclass
class Turn:
    turn_id: str
    chat_id: str
    session_id: str
    start_ts: int
    end_ts: int
    start_dt: str
    end_dt: str
    sender_id: str
    sender_name: str
    role: str
    kinds: List[str]
    message_count: int
    source_message_ids: List[str]
    media_ids: List[str]
    text: str


@dataclass
class Chunk:
    chunk_id: str
    chat_id: str
    session_id: str
    start_ts: int
    end_ts: int
    start_dt: str
    end_dt: str
    turn_count: int
    message_count: int
    speaker_set: List[str]
    dominant_speaker: str
    has_image: bool
    has_video: bool
    has_sticker: bool
    has_voice: bool
    has_voice_asr: bool
    media_ids: List[str]
    source_turn_ids: List[str]
    source_message_ids: List[str]
    text_for_embedding: str
    display_text: str


@dataclass
class MediaFileIndex:
    image_by_local_id: Dict[str, str]
    image_by_message_id: Dict[str, str]
    video_by_local_id: Dict[str, str]
    video_by_message_id: Dict[str, str]
    sticker_by_md5: Dict[str, str]
    voice_by_local_id: Dict[str, str]
    voice_by_message_id: Dict[str, str]


WHITESPACE_RE = re.compile(r"\s+")
MULTI_NEWLINE_RE = re.compile(r"\n{3,}")
PUNCT_ONLY_RE = re.compile(r"^[\?\？\!\！\.\。,，…~～]+$")
EMOJI_PATTERN = re.compile(
    r"[\U0001F600-\U0001F64F"
    r"\U0001F300-\U0001F5FF"
    r"\U0001F680-\U0001F6FF"
    r"\U0001F1E0-\U0001F1FF"
    r"\U00002702-\U000027B0"
    r"\U0000FE00-\U0000FE0F"
    r"\U0001F900-\U0001F9FF]+",
    re.UNICODE,
)


def extract_emoji_tokens(text: str) -> List[str]:
    if not text:
        return []
    return EMOJI_PATTERN.findall(text)


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def clean_text(text: Any) -> str:
    if text is None:
        return ""
    s = str(text).replace("\r\n", "\n").replace("\r", "\n").strip()
    s = WHITESPACE_RE.sub(" ", s)
    s = MULTI_NEWLINE_RE.sub("\n\n", s)
    return s.strip()


def safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def to_dt_str(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def is_short_message(text: str, short_char_threshold: int = 8) -> bool:
    s = (text or "").strip()
    if not s:
        return True
    if len(s) <= short_char_threshold:
        return True
    if PUNCT_ONLY_RE.match(s):
        return True
    return False


def count_cjk_chars(text: str) -> int:
    return sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")


def rough_token_count(text: str) -> int:
    if not text:
        return 0
    cjk = count_cjk_chars(text)
    total = len(text)
    non_cjk = max(total - cjk, 0)

    cjk_tokens = math.ceil(cjk / 1.2)
    non_cjk_tokens = math.ceil(non_cjk / 4.0)
    return cjk_tokens + non_cjk_tokens


def write_jsonl(path: str, rows: List[Dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def dedupe_keep_order(items: List[str]) -> List[str]:
    seen = set()
    out = []
    for x in items:
        if not x:
            continue
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def dominant_speaker(roles: List[str]) -> str:
    counter: Dict[str, int] = {}
    for r in roles:
        counter[r] = counter.get(r, 0) + 1
    return max(counter.items(), key=lambda x: x[1])[0] if counter else "unknown"


def build_session_id(chat_id: str, session_idx: int, ts: int) -> str:
    day = datetime.fromtimestamp(ts).strftime("%Y%m%d")
    return f"{chat_id}_{day}_s{session_idx:04d}"


def detect_kind_and_media(
    type_name: str,
    type_code: int,
    content_raw: str,
) -> Tuple[str, Optional[str], Optional[str]]:
    """
    Returns:
    - kind
    - placeholder_text
    - media_type
    """
    type_name = clean_text(type_name)
    content_raw = clean_text(content_raw)

    if type_name == "文本消息" or type_code == 1:
        return "text", None, None

    if type_name == "语音消息" or type_code == 34:
        if content_raw.startswith("[语音转文字]"):
            return "voice_asr", None, "voice"
        if "转文字失败" in content_raw:
            return "voice_failed", "[语音转文字失败]", "voice"
        return "voice_placeholder", "[语音]", "voice"

    if type_name == "图片消息" or type_code == 3:
        return "image_placeholder", "[图片]", "image"

    if type_name == "视频消息" or type_code == 43:
        return "video_placeholder", "[视频]", "video"

    if type_name == "动画表情" or type_code == 47:
        return "sticker_placeholder", "[表情包]", "sticker"

    return "other", None, None


def normalize_content_for_embedding(
    kind: str,
    content_raw: str,
    placeholder_text: Optional[str],
) -> str:
    content_raw = clean_text(content_raw)

    if kind == "voice_asr":
        s = content_raw.replace("[语音转文字]", "", 1).strip()
        return s if s else "[语音转文字]"

    if kind in {
        "image_placeholder",
        "video_placeholder",
        "sticker_placeholder",
        "voice_placeholder",
    }:
        return placeholder_text or content_raw or "[媒体]"

    return content_raw


def _load_jsonl(input_path: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with open(input_path, "r", encoding="utf-8") as f:
        for idx, line in enumerate(f):
            raw = line.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                print(f"[WARN] JSONL 第 {idx + 1} 行解析失败，已跳过")
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    return rows


def load_input_json(
    input_path: str, input_format: str = "auto"
) -> List[Dict[str, Any]]:
    normalized_format = (input_format or "auto").strip().lower()
    if normalized_format == "jsonl":
        return _load_jsonl(input_path)
    if normalized_format == "auto" and input_path.lower().endswith(".jsonl"):
        return _load_jsonl(input_path)

    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        for key in ("messages", "data", "records", "items"):
            value = data.get(key)
            if isinstance(value, list):
                return value

    raise ValueError(
        "Unsupported JSON structure. Expected a list or a dict containing a message list."
    )


def build_media_file_index(media_root: Optional[str]) -> MediaFileIndex:
    index = MediaFileIndex(
        image_by_local_id={},
        image_by_message_id={},
        video_by_local_id={},
        video_by_message_id={},
        sticker_by_md5={},
        voice_by_local_id={},
        voice_by_message_id={},
    )
    if not media_root:
        return index

    media_root = os.path.abspath(media_root)
    if not os.path.isdir(media_root):
        print(f"[WARN] media root not found: {media_root}")
        return index

    images_dir = os.path.join(media_root, "images")
    videos_dir = os.path.join(media_root, "videos")
    emojis_dir = os.path.join(media_root, "emojis")
    voices_dir = os.path.join(media_root, "voices")

    def safe_rel(path: str) -> str:
        return os.path.relpath(path, media_root).replace("\\", "/")

    if os.path.isdir(images_dir):
        for name in os.listdir(images_dir):
            path = os.path.join(images_dir, name)
            if not os.path.isfile(path):
                continue
            rel = safe_rel(path)
            stem, _ = os.path.splitext(name)
            local_match = re.match(r"^(\d+)_", stem)
            if local_match:
                local_id = local_match.group(1)
                if local_id not in index.image_by_local_id:
                    index.image_by_local_id[local_id] = rel
            long_digits = [x for x in re.findall(r"\d{16,}", stem) if x]
            for msg_id in long_digits:
                if msg_id not in index.image_by_message_id:
                    index.image_by_message_id[msg_id] = rel

    if os.path.isdir(videos_dir):
        for name in os.listdir(videos_dir):
            path = os.path.join(videos_dir, name)
            if not os.path.isfile(path):
                continue
            rel = safe_rel(path)
            stem, _ = os.path.splitext(name)
            local_match = re.match(r"^(\d+)_", stem)
            if local_match:
                local_id = local_match.group(1)
                if local_id not in index.video_by_local_id:
                    index.video_by_local_id[local_id] = rel
            long_digits = [x for x in re.findall(r"\d{16,}", stem) if x]
            for msg_id in long_digits:
                if msg_id not in index.video_by_message_id:
                    index.video_by_message_id[msg_id] = rel

    if os.path.isdir(emojis_dir):
        for name in os.listdir(emojis_dir):
            path = os.path.join(emojis_dir, name)
            if not os.path.isfile(path):
                continue
            rel = safe_rel(path)
            stem, _ = os.path.splitext(name)
            if stem and stem not in index.sticker_by_md5:
                index.sticker_by_md5[stem] = rel

    if os.path.isdir(voices_dir):
        for name in os.listdir(voices_dir):
            path = os.path.join(voices_dir, name)
            if not os.path.isfile(path):
                continue
            rel = safe_rel(path)
            stem, _ = os.path.splitext(name)
            voice_match = re.match(r"^voice_(.+)_(\d+)_(\d+)_(\d+)$", stem)
            if voice_match:
                local_id = voice_match.group(2)
                message_id = voice_match.group(4)
                if local_id not in index.voice_by_local_id:
                    index.voice_by_local_id[local_id] = rel
                if message_id not in index.voice_by_message_id:
                    index.voice_by_message_id[message_id] = rel

    return index


def resolve_media_file_path(
    media_type: Optional[str],
    local_id: int,
    message_id: str,
    emoji_md5: Optional[str],
    media_index: MediaFileIndex,
) -> Optional[str]:
    local_id_str = str(local_id)

    if media_type == "sticker":
        if emoji_md5:
            return media_index.sticker_by_md5.get(emoji_md5)
        return None

    if media_type == "image":
        return media_index.image_by_local_id.get(
            local_id_str
        ) or media_index.image_by_message_id.get(message_id)

    if media_type == "video":
        return media_index.video_by_local_id.get(
            local_id_str
        ) or media_index.video_by_message_id.get(message_id)

    if media_type == "voice":
        return media_index.voice_by_message_id.get(
            message_id
        ) or media_index.voice_by_local_id.get(local_id_str)

    return None


def load_and_normalize_messages(
    input_path: str,
    chat_id: str,
    my_sender: Optional[str] = None,
    input_format: str = "auto",
    media_root: Optional[str] = None,
) -> Tuple[List[NormalizedMessage], List[MediaItem]]:
    raw_messages = load_input_json(input_path, input_format=input_format)
    media_index = build_media_file_index(media_root)

    messages: List[NormalizedMessage] = []
    media_items: List[MediaItem] = []

    for idx, obj in enumerate(raw_messages):
        if not isinstance(obj, dict):
            continue

        timestamp = safe_int(obj.get("createTime", 0), 0)
        if not timestamp:
            continue

        message_id = str(obj.get("platformMessageId") or f"msg_{idx}")
        local_id = safe_int(obj.get("localId", idx), idx)
        datetime_str = clean_text(obj.get("formattedTime")) or to_dt_str(timestamp)

        sender_id = clean_text(obj.get("senderUsername"))
        sender_name = clean_text(obj.get("senderDisplayName")) or sender_id or "unknown"

        is_send = bool(safe_int(obj.get("isSend", 0), 0))
        if my_sender:
            role = "me" if sender_id == my_sender else "target"
        else:
            role = "me" if is_send else "target"

        type_name = clean_text(obj.get("type"))
        type_code = safe_int(obj.get("localType", -1), -1)
        content_raw = clean_text(obj.get("content"))

        source = clean_text(obj.get("source"))
        sender_avatar_key = clean_text(obj.get("senderAvatarKey"))
        emoji_md5 = clean_text(obj.get("emojiMd5")) or None
        emoji_cdn_url = clean_text(obj.get("emojiCdnUrl")) or None

        kind, placeholder_text, media_type = detect_kind_and_media(
            type_name=type_name,
            type_code=type_code,
            content_raw=content_raw,
        )

        content_for_embedding = normalize_content_for_embedding(
            kind=kind,
            content_raw=content_raw,
            placeholder_text=placeholder_text,
        )

        has_media = media_type is not None
        media_id = f"media_{message_id}" if has_media else None

        msg = NormalizedMessage(
            raw_index=idx,
            message_id=message_id,
            local_id=local_id,
            chat_id=chat_id,
            timestamp=timestamp,
            datetime_str=datetime_str,
            sender_id=sender_id,
            sender_name=sender_name,
            role=role,
            is_send=is_send,
            type_name=type_name,
            type_code=type_code,
            kind=kind,
            content_raw=content_raw,
            content_for_embedding=content_for_embedding,
            placeholder_text=placeholder_text,
            has_media=has_media,
            media_id=media_id,
            media_type=media_type,
            source=source,
            sender_avatar_key=sender_avatar_key,
            emoji_md5=emoji_md5,
            emoji_cdn_url=emoji_cdn_url,
            is_short=is_short_message(content_for_embedding),
        )
        messages.append(msg)

        if has_media and media_id:
            file_path = resolve_media_file_path(
                media_type=media_type,
                local_id=local_id,
                message_id=message_id,
                emoji_md5=emoji_md5,
                media_index=media_index,
            )
            media_items.append(
                MediaItem(
                    media_id=media_id,
                    message_id=message_id,
                    local_id=local_id,
                    chat_id=chat_id,
                    timestamp=timestamp,
                    datetime_str=datetime_str,
                    sender_id=sender_id,
                    sender_name=sender_name,
                    role=role,
                    media_type=media_type,
                    type_name=type_name,
                    type_code=type_code,
                    placeholder_text=placeholder_text
                    or ("[语音]" if media_type == "voice" else "[媒体]"),
                    file_path=file_path,
                    cdn_url=emoji_cdn_url if media_type == "sticker" else None,
                    emoji_md5=emoji_md5,
                    session_id=None,
                    chunk_ids=[],
                    vision_status="pending",
                    vision_caption=None,
                    image_vector_status="pending",
                )
            )

    messages.sort(key=lambda x: (x.timestamp, x.raw_index))
    media_items.sort(key=lambda x: (x.timestamp, x.local_id))
    return messages, media_items


def should_merge_into_current_turn(
    prev_turn_sender_id: str,
    prev_turn_end_ts: int,
    prev_turn_text_len: int,
    current_msg: NormalizedMessage,
    merge_gap_seconds: int,
    max_turn_chars: int,
) -> bool:
    if current_msg.sender_id != prev_turn_sender_id:
        return False

    gap = current_msg.timestamp - prev_turn_end_ts
    if gap < 0:
        return False
    if gap > merge_gap_seconds:
        return False
    if prev_turn_text_len >= max_turn_chars:
        return False

    return True


def merge_messages_to_turns(
    messages: List[NormalizedMessage],
    session_gap_minutes: int,
    merge_gap_seconds: int,
    max_turn_chars: int = 280,
) -> List[Turn]:
    turns: List[Turn] = []
    if not messages:
        return turns

    current_session_idx = 1
    current_session_id = build_session_id(
        messages[0].chat_id, current_session_idx, messages[0].timestamp
    )
    current_turn_msgs: List[NormalizedMessage] = []
    prev_msg: Optional[NormalizedMessage] = None

    def flush_turn() -> None:
        nonlocal current_turn_msgs, turns, current_session_id
        if not current_turn_msgs:
            return

        first = current_turn_msgs[0]
        last = current_turn_msgs[-1]
        turn_idx = len(turns) + 1

        text_lines = [
            m.content_for_embedding
            for m in current_turn_msgs
            if m.content_for_embedding.strip()
        ]
        text = "\n".join(text_lines).strip() or "[空消息]"

        turn = Turn(
            turn_id=f"{current_session_id}_t{turn_idx:05d}",
            chat_id=first.chat_id,
            session_id=current_session_id,
            start_ts=first.timestamp,
            end_ts=last.timestamp,
            start_dt=first.datetime_str,
            end_dt=last.datetime_str,
            sender_id=first.sender_id,
            sender_name=first.sender_name,
            role=first.role,
            kinds=sorted(list({m.kind for m in current_turn_msgs})),
            message_count=len(current_turn_msgs),
            source_message_ids=[m.message_id for m in current_turn_msgs],
            media_ids=dedupe_keep_order(
                [m.media_id for m in current_turn_msgs if m.media_id]
            ),
            text=text,
        )
        turns.append(turn)
        current_turn_msgs = []

    for msg in messages:
        if prev_msg is not None:
            gap_seconds = msg.timestamp - prev_msg.timestamp
            if gap_seconds > session_gap_minutes * 60:
                flush_turn()
                current_session_idx += 1
                current_session_id = build_session_id(
                    msg.chat_id, current_session_idx, msg.timestamp
                )

        if not current_turn_msgs:
            current_turn_msgs = [msg]
        else:
            prev_turn_sender_id = current_turn_msgs[-1].sender_id
            prev_turn_end_ts = current_turn_msgs[-1].timestamp
            prev_turn_text_len = sum(
                len(m.content_for_embedding) for m in current_turn_msgs
            )

            if should_merge_into_current_turn(
                prev_turn_sender_id=prev_turn_sender_id,
                prev_turn_end_ts=prev_turn_end_ts,
                prev_turn_text_len=prev_turn_text_len,
                current_msg=msg,
                merge_gap_seconds=merge_gap_seconds,
                max_turn_chars=max_turn_chars,
            ):
                current_turn_msgs.append(msg)
            else:
                flush_turn()
                current_turn_msgs = [msg]

        prev_msg = msg

    flush_turn()
    return turns


def group_turns_by_session(turns: List[Turn]) -> Dict[str, List[Turn]]:
    grouped: Dict[str, List[Turn]] = {}
    for t in turns:
        grouped.setdefault(t.session_id, []).append(t)
    return grouped


def build_chunk_texts(turns: List[Turn]) -> Tuple[str, str]:
    embed_lines: List[str] = []
    display_lines: List[str] = []

    for t in turns:
        speaker = t.sender_name or t.role
        embed_lines.append(f"{speaker}：{t.text}")
        display_lines.append(f"[{t.start_dt}] {speaker}：{t.text}")

    return "\n".join(embed_lines).strip(), "\n".join(display_lines).strip()


def split_session_into_chunks(
    session_id: str,
    turns: List[Turn],
    chunk_turns: int,
    chunk_overlap: int,
    max_chunk_tokens: int,
    min_chunk_turns: int = 4,
) -> List[Chunk]:
    if not turns:
        return []

    if chunk_overlap >= chunk_turns:
        raise ValueError("chunk_overlap must be smaller than chunk_turns")

    chunk_windows: List[List[Turn]] = []
    start = 0
    step = chunk_turns - chunk_overlap

    while start < len(turns):
        end = min(start + chunk_turns, len(turns))
        window = turns[start:end]

        while len(window) > min_chunk_turns:
            text_for_embedding, _ = build_chunk_texts(window)
            token_est = rough_token_count(text_for_embedding)
            if token_est <= max_chunk_tokens:
                break
            window = window[:-1]
            end -= 1

        if len(window) < min_chunk_turns and chunk_windows:
            chunk_windows[-1].extend(window)
            break

        chunk_windows.append(window)

        if end >= len(turns):
            break
        start += step

    chunks: List[Chunk] = []

    for chunk_idx, window in enumerate(chunk_windows, start=1):
        first = window[0]
        last = window[-1]
        text_for_embedding, display_text = build_chunk_texts(window)

        roles = [t.role for t in window]
        kinds = set()
        source_message_ids: List[str] = []
        media_ids: List[str] = []

        for t in window:
            kinds.update(t.kinds)
            source_message_ids.extend(t.source_message_ids)
            media_ids.extend(t.media_ids)

        chunk = Chunk(
            chunk_id=f"{session_id}_c{chunk_idx:04d}",
            chat_id=first.chat_id,
            session_id=session_id,
            start_ts=first.start_ts,
            end_ts=last.end_ts,
            start_dt=first.start_dt,
            end_dt=last.end_dt,
            turn_count=len(window),
            message_count=sum(t.message_count for t in window),
            speaker_set=sorted(list(set(roles))),
            dominant_speaker=dominant_speaker(roles),
            has_image=("image_placeholder" in kinds),
            has_video=("video_placeholder" in kinds),
            has_sticker=("sticker_placeholder" in kinds),
            has_voice_asr=("voice_asr" in kinds),
            has_voice=(
                "voice_asr" in kinds
                or "voice_placeholder" in kinds
                or "voice_failed" in kinds
            ),
            media_ids=dedupe_keep_order(media_ids),
            source_turn_ids=[t.turn_id for t in window],
            source_message_ids=dedupe_keep_order(source_message_ids),
            text_for_embedding=text_for_embedding,
            display_text=display_text,
        )
        chunks.append(chunk)

    return chunks


def build_all_chunks(
    turns: List[Turn],
    chunk_turns: int,
    chunk_overlap: int,
    max_chunk_tokens: int,
) -> List[Chunk]:
    grouped = group_turns_by_session(turns)
    all_chunks: List[Chunk] = []

    for session_id, session_turns in grouped.items():
        session_turns.sort(key=lambda x: (x.start_ts, x.turn_id))
        chunks = split_session_into_chunks(
            session_id=session_id,
            turns=session_turns,
            chunk_turns=chunk_turns,
            chunk_overlap=chunk_overlap,
            max_chunk_tokens=max_chunk_tokens,
        )
        all_chunks.extend(chunks)

    all_chunks.sort(key=lambda x: (x.start_ts, x.chunk_id))
    return all_chunks


def enrich_media_items_with_refs(
    media_items: List[MediaItem],
    messages: List[NormalizedMessage],
    chunks: List[Chunk],
) -> List[MediaItem]:
    media_by_id: Dict[str, MediaItem] = {m.media_id: m for m in media_items}

    # message -> session
    message_to_session: Dict[str, str] = {}
    for c in chunks:
        for msg_id in c.source_message_ids:
            if msg_id not in message_to_session:
                message_to_session[msg_id] = c.session_id

    # message -> chunk ids
    message_to_chunks: Dict[str, List[str]] = {}
    for c in chunks:
        for msg_id in c.source_message_ids:
            message_to_chunks.setdefault(msg_id, []).append(c.chunk_id)

    for msg in messages:
        if msg.media_id and msg.media_id in media_by_id:
            item = media_by_id[msg.media_id]
            item.session_id = message_to_session.get(msg.message_id)
            item.chunk_ids = dedupe_keep_order(
                message_to_chunks.get(msg.message_id, [])
            )

    return list(media_by_id.values())


def build_analysis_report(
    messages: List[NormalizedMessage],
    turns: List[Turn],
    target_role: str = "target",
) -> Dict[str, Any]:
    target_msgs = [m for m in messages if m.role == target_role]
    if not target_msgs:
        return {
            "target_role": target_role,
            "message_count": 0,
            "analysis": {},
            "sample_messages": [],
        }

    analyzable_msgs = [
        m
        for m in target_msgs
        if m.kind in {"text", "voice_asr"} and m.content_for_embedding.strip()
    ]

    all_text = "\n".join(m.content_for_embedding for m in analyzable_msgs)

    particles = re.findall(r"[哈嗯哦噢嘿唉呜啊呀吧嘛呢吗么啦喔哇欸诶哎]+", all_text)
    particle_freq: Dict[str, int] = {}
    for p in particles:
        particle_freq[p] = particle_freq.get(p, 0) + 1
    top_particles = sorted(particle_freq.items(), key=lambda x: -x[1])[:10]

    emojis = extract_emoji_tokens(all_text)
    emoji_freq: Dict[str, int] = {}
    for e in emojis:
        emoji_freq[e] = emoji_freq.get(e, 0) + 1
    top_emojis = sorted(emoji_freq.items(), key=lambda x: -x[1])[:10]

    msg_lengths = [
        len(m.content_for_embedding) for m in analyzable_msgs if m.content_for_embedding
    ]
    avg_length = round(sum(msg_lengths) / len(msg_lengths), 1) if msg_lengths else 0.0

    punctuation_counts = {
        "句号": all_text.count("。"),
        "感叹号": all_text.count("！") + all_text.count("!"),
        "问号": all_text.count("？") + all_text.count("?"),
        "省略号": all_text.count("...") + all_text.count("…"),
        "波浪号": all_text.count("～") + all_text.count("~"),
    }

    target_turns = [t for t in turns if t.role == target_role]
    media_turn_count = sum(
        1
        for t in target_turns
        if any(
            k
            in {
                "image_placeholder",
                "video_placeholder",
                "sticker_placeholder",
                "voice_placeholder",
                "voice_asr",
            }
            for k in t.kinds
        )
    )

    kind_counter: Dict[str, int] = {}
    for m in target_msgs:
        kind_counter[m.kind] = kind_counter.get(m.kind, 0) + 1

    message_style = "short_burst" if avg_length < 20 else "long_form"

    sample_messages = [
        m.content_for_embedding
        for m in analyzable_msgs[:50]
        if m.content_for_embedding.strip()
    ]

    return {
        "target_role": target_role,
        "message_count": len(target_msgs),
        "analyzable_message_count": len(analyzable_msgs),
        "analysis": {
            "top_particles": top_particles,
            "top_emojis": top_emojis,
            "avg_message_length": avg_length,
            "punctuation_habits": punctuation_counts,
            "message_style": message_style,
            "kind_distribution": kind_counter,
            "media_turn_count": media_turn_count,
        },
        "sample_messages": sample_messages,
    }


def write_analysis_report_markdown(
    path: str,
    report: Dict[str, Any],
    chat_id: str,
    input_path: str,
) -> None:
    analysis = report.get("analysis", {})
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# 微信聊天记录分析报告\n\n")
        f.write(f"- chat_id: {chat_id}\n")
        f.write(f"- source: {input_path}\n")
        f.write(f"- target_role: {report.get('target_role', 'target')}\n")
        f.write(f"- target_message_count: {report.get('message_count', 0)}\n")
        f.write(
            f"- analyzable_message_count: {report.get('analyzable_message_count', 0)}\n\n"
        )

        if analysis.get("top_particles"):
            f.write("## 高频语气词\n")
            for word, count in analysis["top_particles"]:
                f.write(f"- {word}: {count}次\n")
            f.write("\n")

        if analysis.get("top_emojis"):
            f.write("## 高频 Emoji\n")
            for emoji, count in analysis["top_emojis"]:
                f.write(f"- {emoji}: {count}次\n")
            f.write("\n")

        if analysis.get("punctuation_habits"):
            f.write("## 标点习惯\n")
            for punct, count in analysis["punctuation_habits"].items():
                f.write(f"- {punct}: {count}次\n")
            f.write("\n")

        f.write("## 消息风格\n")
        f.write(f"- 平均消息长度：{analysis.get('avg_message_length', 0)} 字\n")
        f.write(
            f"- 风格：{'短句连发型' if analysis.get('message_style') == 'short_burst' else '长段落型'}\n"
        )
        f.write(f"- 媒体相关 turn 数：{analysis.get('media_turn_count', 0)}\n\n")

        if analysis.get("kind_distribution"):
            f.write("## 消息类型分布\n")
            for kind, count in sorted(
                analysis["kind_distribution"].items(), key=lambda x: -x[1]
            ):
                f.write(f"- {kind}: {count}\n")
            f.write("\n")

        samples = report.get("sample_messages", [])
        if samples:
            f.write("## 消息样本（前 50 条）\n")
            for i, msg in enumerate(samples[:50], 1):
                f.write(f"{i}. {msg}\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="预处理微信 JSON 聊天记录")

    parser.add_argument(
        "--input",
        required=True,
        help="输入文件路径（JSON 或 JSONL）",
    )
    parser.add_argument(
        "--input-format",
        default="auto",
        choices=["auto", "json", "jsonl"],
        help="输入格式：auto/json/jsonl，默认 auto",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="输出目录",
    )
    parser.add_argument(
        "--chat-id",
        required=True,
        help="自定义聊天 ID，例如 chat_xiaoguang",
    )
    parser.add_argument(
        "--my-sender",
        default=None,
        help="你的发送方 ID（wxid），可选；若不填则根据 isSend 推断角色",
    )
    parser.add_argument(
        "--session-gap-minutes",
        type=int,
        default=30,
        help="session 切分时间阈值，默认 30 分钟",
    )
    parser.add_argument(
        "--merge-gap-seconds",
        type=int,
        default=90,
        help="同一发送者连续消息合并为 turn 的时间阈值，默认 90 秒",
    )
    parser.add_argument(
        "--max-turn-chars",
        type=int,
        default=280,
        help="单个 turn 最大字符数，默认 280",
    )
    parser.add_argument(
        "--chunk-turns",
        type=int,
        default=12,
        help="每个 chunk 的 turn 数，默认 12",
    )
    parser.add_argument(
        "--chunk-overlap",
        type=int,
        default=3,
        help="相邻 chunk 之间重叠的 turn 数量，默认 3",
    )
    parser.add_argument(
        "--max-chunk-tokens",
        type=int,
        default=800,
        help="chunk 粗略 token 上限，默认 800",
    )
    parser.add_argument(
        "--media-root",
        default=None,
        help="媒体根目录，可选",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    ensure_dir(args.output_dir)

    messages, media_items = load_and_normalize_messages(
        input_path=args.input,
        chat_id=args.chat_id,
        my_sender=args.my_sender,
        input_format=args.input_format,
        media_root=args.media_root,
    )

    if not messages:
        raise RuntimeError("No valid messages found in input file.")

    turns = merge_messages_to_turns(
        messages=messages,
        session_gap_minutes=args.session_gap_minutes,
        merge_gap_seconds=args.merge_gap_seconds,
        max_turn_chars=args.max_turn_chars,
    )

    chunks = build_all_chunks(
        turns=turns,
        chunk_turns=args.chunk_turns,
        chunk_overlap=args.chunk_overlap,
        max_chunk_tokens=args.max_chunk_tokens,
    )

    media_items = enrich_media_items_with_refs(
        media_items=media_items,
        messages=messages,
        chunks=chunks,
    )
    media_items.sort(key=lambda x: (x.timestamp, x.local_id))

    normalized_path = os.path.join(args.output_dir, "normalized_messages.jsonl")
    turns_path = os.path.join(args.output_dir, "turns.jsonl")
    chunks_path = os.path.join(args.output_dir, "chunks.jsonl")
    media_path = os.path.join(args.output_dir, "media_items.jsonl")
    stats_path = os.path.join(args.output_dir, "stats.json")

    write_jsonl(normalized_path, [asdict(m) for m in messages])
    write_jsonl(turns_path, [asdict(t) for t in turns])
    write_jsonl(chunks_path, [asdict(c) for c in chunks])
    write_jsonl(media_path, [asdict(m) for m in media_items])

    analysis_report = build_analysis_report(
        messages=messages,
        turns=turns,
        target_role="target",
    )

    analysis_json_path = os.path.join(args.output_dir, "analysis_report.json")
    analysis_md_path = os.path.join(args.output_dir, "analysis_report.md")

    with open(analysis_json_path, "w", encoding="utf-8") as f:
        json.dump(analysis_report, f, ensure_ascii=False, indent=2)

    write_analysis_report_markdown(
        path=analysis_md_path,
        report=analysis_report,
        chat_id=args.chat_id,
        input_path=args.input,
    )

    stats = {
        "input": args.input,
        "input_format": args.input_format,
        "chat_id": args.chat_id,
        "message_count": len(messages),
        "turn_count": len(turns),
        "chunk_count": len(chunks),
        "media_item_count": len(media_items),
        "media_file_resolved_count": sum(1 for m in media_items if m.file_path),
        "session_count": len({t.session_id for t in turns}),
        "params": {
            "session_gap_minutes": args.session_gap_minutes,
            "merge_gap_seconds": args.merge_gap_seconds,
            "max_turn_chars": args.max_turn_chars,
            "chunk_turns": args.chunk_turns,
            "chunk_overlap": args.chunk_overlap,
            "max_chunk_tokens": args.max_chunk_tokens,
        },
    }

    with open(stats_path, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

    print("[OK] preprocessing finished")
    print(f"[OK] normalized messages: {normalized_path}")
    print(f"[OK] turns: {turns_path}")
    print(f"[OK] chunks: {chunks_path}")
    print(f"[OK] media items: {media_path}")
    print(f"[OK] stats: {stats_path}")
    print(f"[OK] analysis report json: {analysis_json_path}")
    print(f"[OK] analysis report md: {analysis_md_path}")
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
