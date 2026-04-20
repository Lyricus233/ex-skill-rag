#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""按文本查询 Milvus 聊天记录块

Usage:
    python3 search_milvus.py \
        --query <text> \
        [--top-k <count>] \
        [--collection <name>] \
        [--source <wechat_weflow/qq/other>] \
        [--chat-id <chat_id>] \
        [--json]
"""

from __future__ import annotations

import os
import json
import argparse
from typing import List, Dict, Any
from dotenv import load_dotenv
from openai import OpenAI
from pymilvus import MilvusClient


def sanitize_text(text: str) -> str:
    text = (text or "").strip()
    return text if text else "[EMPTY]"


def build_openai_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required")

    base_url = os.getenv("OPENAI_BASE_URL", "").strip() or None
    if base_url:
        return OpenAI(api_key=api_key, base_url=base_url)
    return OpenAI(api_key=api_key)


def embed_query(client: OpenAI, model: str, text: str) -> List[float]:
    resp = client.embeddings.create(
        model=model,
        input=[sanitize_text(text)],
    )
    return resp.data[0].embedding


def build_filter(chat_id: str = None, source: str = None) -> str:
    clauses: List[str] = []
    if source:
        clauses.append(f'source == "{source}"')
    if chat_id:
        clauses.append(f'chat_id == "{chat_id}"')
    return " and ".join(clauses)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="按文本查询 Milvus 聊天记录块")
    parser.add_argument("--query", required=True, help="查询文本")
    parser.add_argument("--top-k", type=int, default=5, help="返回前 K 条，默认 5")
    parser.add_argument(
        "--collection", default=None, help="Milvus 集合名；默认读取 .env"
    )
    parser.add_argument(
        "--source", default=None, help="可选，按来源过滤（wechat_weflow/qq/other）"
    )
    parser.add_argument("--chat-id", default=None, help="可选，指定 chat_id")
    parser.add_argument("--json", action="store_true", help="以 JSON 格式输出")
    return parser.parse_args()


def main() -> None:
    load_dotenv()
    args = parse_args()

    uri = os.getenv("MILVUS_URI", "").strip()
    token = os.getenv("MILVUS_TOKEN", "").strip() or None
    collection = (
        args.collection or os.getenv("MILVUS_COLLECTION", "").strip() or "chat_chunks"
    )
    model = os.getenv("EMBEDDING_MODEL", "").strip() or "text-embedding-3-large"

    if not uri:
        raise RuntimeError("请在 .env 中配置 MILVUS_URI")

    openai_client = build_openai_client()
    milvus_client = (
        MilvusClient(uri=uri, token=token) if token else MilvusClient(uri=uri)
    )

    query_vector = embed_query(openai_client, model, args.query)
    filter_expr = build_filter(args.chat_id, args.source)

    search_kwargs = {
        "collection_name": collection,
        "data": [query_vector],
        "limit": args.top_k,
        "output_fields": [
            "source",
            "chat_id",
            "session_id",
            "start_ts",
            "end_ts",
            "dominant_speaker",
            "turn_count",
            "message_count",
            "display_text",
            "text_for_embedding",
            "has_image",
            "has_video",
            "has_sticker",
            "has_voice",
            "has_voice_asr",
        ],
    }

    if filter_expr:
        search_kwargs["filter"] = filter_expr

    results = milvus_client.search(**search_kwargs)
    hits = results[0]

    rows = []
    for item in hits:
        entity = item["entity"]
        rows.append(
            {
                "score": item["distance"],
                "source": entity.get("source"),
                "chat_id": entity.get("chat_id"),
                "session_id": entity.get("session_id"),
                "dominant_speaker": entity.get("dominant_speaker"),
                "turn_count": entity.get("turn_count"),
                "message_count": entity.get("message_count"),
                "has_image": entity.get("has_image"),
                "has_video": entity.get("has_video"),
                "has_sticker": entity.get("has_sticker"),
                "has_voice": entity.get("has_voice"),
                "has_voice_asr": entity.get("has_voice_asr"),
                "display_text": entity.get("display_text"),
            }
        )

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        for i, row in enumerate(rows, start=1):
            print("=" * 80)
            print(f"[{i}] score={row['score']}")
            print(f"source: {row['source']}")
            print(f"chat_id: {row['chat_id']}")
            print(f"session_id: {row['session_id']}")
            print(f"dominant_speaker: {row['dominant_speaker']}")
            print(f"turns={row['turn_count']} messages={row['message_count']}")
            print(row["display_text"] or "")


if __name__ == "__main__":
    main()
