#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""把 ex skill 对话归档后的 session summary 作为一个 chunk 写入 Milvus。

设计要点：
- session chunk 的 `source` 字段固定为 "session_summary"，`dominant_speaker` 固定为 "session"
- 这样 ex skill 的默认查询（--source wechat_weflow --dominant-speaker target）
  不会把它当成"ta 真实说过的话"，避免 AI 生成内容自循环污染语气参考
- 只有当 ex skill 明确想查"最近聊过什么"时，才查 source=session_summary

Usage:
    python3 persist_session.py \\
        --session <session_summary_md_path> \\
        --collection <name> \\
        --chat-id <slug>_session \\
        [--source session_summary]
"""

from __future__ import annotations

import os
import sys
import json
import time
import argparse
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from openai import OpenAI
from pymilvus import MilvusClient, DataType

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


SOURCE_TAG = "session_summary"
SPEAKER_TAG = "session"


def sanitize_text(t: Optional[str]) -> str:
    if t is None:
        return "[EMPTY]"
    t = str(t).strip()
    return t if t else "[EMPTY]"


def build_openai_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required")
    base_url = os.getenv("OPENAI_BASE_URL", "").strip() or None
    if base_url:
        return OpenAI(api_key=api_key, base_url=base_url)
    return OpenAI(api_key=api_key)


def embed_text(client: OpenAI, model: str, text: str) -> List[float]:
    resp = client.embeddings.create(model=model, input=[sanitize_text(text)])
    return resp.data[0].embedding


def ensure_collection_compatible(milvus: MilvusClient, collection: str) -> None:
    """Session chunks 写进的集合必须已经由 ingest_milvus.py 创建过 schema。
    本工具只追加，不负责建表——如果集合不存在直接报错，让用户先跑原始入库流程。
    """
    if not milvus.has_collection(collection_name=collection):
        raise RuntimeError(
            f"Milvus 集合 '{collection}' 不存在。请先用 ingest_milvus.py 基于真实聊天记录创建，"
            "再用本工具追加 session summary。"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="把 ex skill 的 session summary 写入 Milvus（与真实聊天记录混合但可按 source 过滤）"
    )
    parser.add_argument("--session", required=True, help="session summary 的 .md 路径")
    parser.add_argument(
        "--collection",
        default=None,
        help="目标 Milvus 集合；默认读取 .env 的 MILVUS_COLLECTION",
    )
    parser.add_argument(
        "--chat-id",
        required=True,
        help='session chunk 的 chat_id，一般用 "{slug}_session"',
    )
    parser.add_argument(
        "--source",
        default=SOURCE_TAG,
        help=f'source 标签，默认 "{SOURCE_TAG}"。建议保持默认以与原始聊天记录区分',
    )
    return parser.parse_args()


def main() -> None:
    load_dotenv()
    args = parse_args()

    session_path = Path(args.session)
    if not session_path.exists():
        raise FileNotFoundError(f"session 文件不存在：{session_path}")

    session_text = session_path.read_text(encoding="utf-8").strip()
    if not session_text:
        raise RuntimeError("session 文件是空的，不入库。")

    uri = os.getenv("MILVUS_URI", "").strip()
    token = os.getenv("MILVUS_TOKEN", "").strip() or None
    collection = (
        args.collection or os.getenv("MILVUS_COLLECTION", "").strip() or "chat_chunks"
    )
    model = os.getenv("EMBEDDING_MODEL", "").strip() or "text-embedding-3-large"

    if not uri:
        raise RuntimeError("请在 .env 中配置 MILVUS_URI")

    openai_client = build_openai_client()
    milvus = MilvusClient(uri=uri, token=token) if token else MilvusClient(uri=uri)

    ensure_collection_compatible(milvus, collection)

    ts = int(time.time())
    chunk_id = f"{args.chat_id}_{ts}"
    vector = embed_text(openai_client, model, session_text)

    row: Dict[str, Any] = {
        "id": chunk_id,
        "source": args.source,
        "chat_id": args.chat_id,
        "session_id": chunk_id,
        "start_ts": ts,
        "end_ts": ts,
        "dominant_speaker": SPEAKER_TAG,
        "turn_count": 1,
        "message_count": 1,
        "has_image": False,
        "has_video": False,
        "has_sticker": False,
        "has_voice": False,
        "has_voice_asr": False,
        "text_for_embedding": session_text,
        "display_text": session_text,
        "vector": vector,
    }

    milvus.insert(collection_name=collection, data=[row])

    print(
        json.dumps(
            {
                "ok": True,
                "id": chunk_id,
                "collection": collection,
                "source": args.source,
                "chat_id": args.chat_id,
                "chars": len(session_text),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
