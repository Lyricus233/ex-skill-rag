#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""聊天记录向量化并写入 Milvus 数据库

Usage:
    python3 ingest_milvus.py \
        --input <chunks_jsonl_path> \
        [--collection <name>] \
        [--source <wechat_weflow/qq/other>] \
        [--milvus-uri <uri>] \
        [--milvus-token <token>] \
        [--embedding-model <model>] \
        [--batch-size <size>] \
        [--limit <count>] \
        [--drop-collection]
"""

from __future__ import annotations

import os
import json
import argparse
from typing import Any, Dict, Iterable, List, Optional

from dotenv import load_dotenv
from tqdm import tqdm
from openai import OpenAI
from pymilvus import MilvusClient, DataType


DEFAULT_SOURCE = "wechat_weflow"


def load_jsonl(path: str) -> Iterable[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            raw = line.strip()
            if not raw:
                continue
            try:
                yield json.loads(raw)
            except json.JSONDecodeError as e:
                raise ValueError(f"JSONL 第 {line_no} 行格式错误：{e}") from e


def batched(items: List[Any], batch_size: int) -> Iterable[List[Any]]:
    for i in range(0, len(items), batch_size):
        yield items[i : i + batch_size]


def sanitize_text(text: Optional[str]) -> str:
    if text is None:
        return "[EMPTY]"
    text = str(text).strip()
    return text if text else "[EMPTY]"


def safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def safe_bool(v: Any, default: bool = False) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return bool(v)
    s = str(v).strip().lower()
    if s in {"1", "true", "yes", "y", "on"}:
        return True
    if s in {"0", "false", "no", "n", "off"}:
        return False
    return default


def normalize_chunk(
    raw_chunk: Dict[str, Any],
    row_idx: int,
    source_override: Optional[str],
) -> Dict[str, Any]:
    source = (
        str(source_override).strip()
        if source_override
        else str(raw_chunk.get("source", "")).strip() or DEFAULT_SOURCE
    )

    chunk_id = (
        raw_chunk.get("chunk_id")
        or raw_chunk.get("id")
        or raw_chunk.get("uuid")
        or f"{source}_{row_idx:08d}"
    )

    chat_id = (
        raw_chunk.get("chat_id")
        or raw_chunk.get("conversation_id")
        or raw_chunk.get("target_name")
        or "unknown_chat"
    )

    timestamp = safe_int(raw_chunk.get("timestamp", raw_chunk.get("ts", 0)), 0)
    start_ts = safe_int(raw_chunk.get("start_ts", timestamp), timestamp)
    end_ts = safe_int(raw_chunk.get("end_ts", start_ts), start_ts)

    text_for_embedding = sanitize_text(
        raw_chunk.get("text_for_embedding")
        or raw_chunk.get("content_for_embedding")
        or raw_chunk.get("text")
        or raw_chunk.get("content")
        or raw_chunk.get("raw_text")
        or raw_chunk.get("display_text")
        or ""
    )
    display_text = sanitize_text(
        raw_chunk.get("display_text")
        or raw_chunk.get("text")
        or raw_chunk.get("content")
        or text_for_embedding
    )

    turn_count = safe_int(raw_chunk.get("turn_count", 1), 1)
    message_count = safe_int(raw_chunk.get("message_count", turn_count), turn_count)

    return {
        "chunk_id": str(chunk_id),
        "source": source,
        "chat_id": str(chat_id),
        "session_id": str(raw_chunk.get("session_id", chat_id)),
        "start_ts": start_ts,
        "end_ts": end_ts,
        "dominant_speaker": str(
            raw_chunk.get("dominant_speaker", raw_chunk.get("sender", "unknown"))
        ),
        "turn_count": turn_count,
        "message_count": message_count,
        "has_image": safe_bool(raw_chunk.get("has_image", False), False),
        "has_video": safe_bool(raw_chunk.get("has_video", False), False),
        "has_sticker": safe_bool(raw_chunk.get("has_sticker", False), False),
        "has_voice": safe_bool(raw_chunk.get("has_voice", False), False),
        "has_voice_asr": safe_bool(raw_chunk.get("has_voice_asr", False), False),
        "text_for_embedding": text_for_embedding,
        "display_text": display_text,
    }


def build_openai_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required in .env")

    base_url = os.getenv("OPENAI_BASE_URL", "").strip() or None

    if base_url:
        return OpenAI(api_key=api_key, base_url=base_url)

    return OpenAI(api_key=api_key)


def embed_texts(client: OpenAI, model: str, texts: List[str]) -> List[List[float]]:
    cleaned = [sanitize_text(t) for t in texts]
    resp = client.embeddings.create(
        model=model,
        input=cleaned,
    )
    return [item.embedding for item in resp.data]


def infer_vector_dim(client: OpenAI, model: str) -> int:
    vec = embed_texts(client, model, ["dimension probe"])[0]
    return len(vec)


def build_milvus_client(uri: str, token: Optional[str]) -> MilvusClient:
    if token:
        return MilvusClient(uri=uri, token=token)
    return MilvusClient(uri=uri)


def ensure_collection(
    milvus: MilvusClient,
    collection_name: str,
    vector_dim: int,
    drop_if_exists: bool = False,
) -> None:
    exists = milvus.has_collection(collection_name=collection_name)

    if exists and drop_if_exists:
        milvus.drop_collection(collection_name=collection_name)
        exists = False

    if exists:
        return

    schema = milvus.create_schema(auto_id=False, enable_dynamic_field=False)

    schema.add_field(
        field_name="id", datatype=DataType.VARCHAR, is_primary=True, max_length=128
    )
    schema.add_field(field_name="source", datatype=DataType.VARCHAR, max_length=32)
    schema.add_field(field_name="chat_id", datatype=DataType.VARCHAR, max_length=128)
    schema.add_field(field_name="session_id", datatype=DataType.VARCHAR, max_length=128)
    schema.add_field(field_name="start_ts", datatype=DataType.INT64)
    schema.add_field(field_name="end_ts", datatype=DataType.INT64)
    schema.add_field(
        field_name="dominant_speaker", datatype=DataType.VARCHAR, max_length=32
    )
    schema.add_field(field_name="turn_count", datatype=DataType.INT64)
    schema.add_field(field_name="message_count", datatype=DataType.INT64)
    schema.add_field(field_name="has_image", datatype=DataType.BOOL)
    schema.add_field(field_name="has_video", datatype=DataType.BOOL)
    schema.add_field(field_name="has_sticker", datatype=DataType.BOOL)
    schema.add_field(field_name="has_voice", datatype=DataType.BOOL)
    schema.add_field(field_name="has_voice_asr", datatype=DataType.BOOL)
    schema.add_field(
        field_name="text_for_embedding", datatype=DataType.VARCHAR, max_length=65535
    )
    schema.add_field(
        field_name="display_text", datatype=DataType.VARCHAR, max_length=65535
    )
    schema.add_field(
        field_name="vector", datatype=DataType.FLOAT_VECTOR, dim=vector_dim
    )

    index_params = milvus.prepare_index_params()
    index_params.add_index(
        field_name="vector",
        index_type="AUTOINDEX",
        metric_type="COSINE",
    )

    milvus.create_collection(
        collection_name=collection_name,
        schema=schema,
        index_params=index_params,
    )


def chunk_to_row(chunk: Dict[str, Any], vector: List[float]) -> Dict[str, Any]:
    return {
        "id": str(chunk["chunk_id"]),
        "source": str(chunk.get("source", DEFAULT_SOURCE)),
        "chat_id": str(chunk.get("chat_id", "")),
        "session_id": str(chunk.get("session_id", "")),
        "start_ts": int(chunk.get("start_ts", 0)),
        "end_ts": int(chunk.get("end_ts", 0)),
        "dominant_speaker": str(chunk.get("dominant_speaker", "")),
        "turn_count": int(chunk.get("turn_count", 0)),
        "message_count": int(chunk.get("message_count", 0)),
        "has_image": bool(chunk.get("has_image", False)),
        "has_video": bool(chunk.get("has_video", False)),
        "has_sticker": bool(chunk.get("has_sticker", False)),
        "has_voice": bool(chunk.get("has_voice", False)),
        "has_voice_asr": bool(chunk.get("has_voice_asr", False)),
        "text_for_embedding": sanitize_text(chunk.get("text_for_embedding", "")),
        "display_text": sanitize_text(chunk.get("display_text", "")),
        "vector": vector,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="数据向量化并写入 Milvus。")
    parser.add_argument(
        "--input",
        required=True,
        help="chunks.jsonl 文件路径",
    )
    parser.add_argument(
        "--collection",
        default=None,
        help="Milvus 集合名；默认读取 .env 的 MILVUS_COLLECTION",
    )
    parser.add_argument(
        "--source",
        default=None,
        help="覆盖输入记录中的 source（示例：wechat_weflow / qq / other）",
    )
    parser.add_argument(
        "--milvus-uri",
        default=None,
        help="Milvus 地址；默认读取 .env 的 MILVUS_URI",
    )
    parser.add_argument(
        "--milvus-token",
        default=None,
        help="Milvus 令牌；默认读取 .env 的 MILVUS_TOKEN",
    )
    parser.add_argument(
        "--embedding-model",
        default=None,
        help="Embedding 模型；默认读取 .env 的 EMBEDDING_MODEL",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="每批处理的 chunk 数量；默认 100 或读取 .env 的 BATCH_SIZE",
    )
    parser.add_argument(
        "--drop-collection",
        action="store_true",
        help="若目标集合已存在，则先删除后重新创建",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="仅处理前 N 条记录，测试使用",
    )
    return parser.parse_args()


def main() -> None:
    load_dotenv()
    args = parse_args()

    input_path = args.input
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"输入文件不存在：{input_path}")

    embedding_model = (
        args.embedding_model
        or os.getenv("EMBEDDING_MODEL", "").strip()
        or "text-embedding-3-large"
    )

    milvus_uri = args.milvus_uri or os.getenv("MILVUS_URI", "").strip()
    if not milvus_uri:
        raise RuntimeError("请在 .env 或参数中提供 MILVUS_URI")

    milvus_token = args.milvus_token
    if milvus_token is None:
        milvus_token = os.getenv("MILVUS_TOKEN", "").strip() or None

    collection_name = (
        args.collection or os.getenv("MILVUS_COLLECTION", "").strip() or "chat_chunks"
    )

    batch_size = args.batch_size
    if batch_size is None:
        batch_size = int(os.getenv("BATCH_SIZE", "100"))

    openai_client = build_openai_client()
    milvus_client = build_milvus_client(uri=milvus_uri, token=milvus_token)

    raw_chunks = list(load_jsonl(input_path))
    if args.limit is not None:
        raw_chunks = raw_chunks[: args.limit]

    chunks = [
        normalize_chunk(raw_chunk, idx, args.source)
        for idx, raw_chunk in enumerate(raw_chunks, start=1)
    ]

    if not chunks:
        raise RuntimeError("输入文件中未找到可入库的记录")

    print(f"[INFO] Loaded {len(chunks)} chunks from {input_path}")
    print(f"[INFO] Embedding model: {embedding_model}")
    print(f"[INFO] Milvus URI: {milvus_uri}")
    print(f"[INFO] Collection: {collection_name}")
    print(f"[INFO] Batch size: {batch_size}")
    if args.source:
        print(f"[INFO] source 覆盖：{args.source}")

    vector_dim = infer_vector_dim(openai_client, embedding_model)
    print(f"[INFO] Inferred vector dimension: {vector_dim}")

    ensure_collection(
        milvus=milvus_client,
        collection_name=collection_name,
        vector_dim=vector_dim,
        drop_if_exists=args.drop_collection,
    )
    print("[INFO] Collection is ready")

    inserted = 0
    for batch in tqdm(
        list(batched(chunks, batch_size)), desc="Embedding and inserting"
    ):
        texts = [sanitize_text(c.get("text_for_embedding", "")) for c in batch]
        vectors = embed_texts(openai_client, embedding_model, texts)
        rows = [chunk_to_row(chunk, vec) for chunk, vec in zip(batch, vectors)]

        milvus_client.insert(
            collection_name=collection_name,
            data=rows,
        )
        inserted += len(rows)

    print(f"[OK] 已写入 {inserted} 条到 Milvus 集合：{collection_name}")


if __name__ == "__main__":
    main()
