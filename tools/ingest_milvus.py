#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""聊天记录写入 Milvus 数据库

Usage:
    python3 ingest_milvus.py \
        --input <chunks_jsonl_path> \
        [--collection <name>] \
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
        raise FileNotFoundError(f"Input file not found: {input_path}")

    embedding_model = (
        args.embedding_model
        or os.getenv("EMBEDDING_MODEL", "").strip()
        or "text-embedding-3-large"
    )

    milvus_uri = args.milvus_uri or os.getenv("MILVUS_URI", "").strip()
    if not milvus_uri:
        raise RuntimeError("MILVUS_URI is required in .env or via --milvus-uri")

    milvus_token = args.milvus_token
    if milvus_token is None:
        milvus_token = os.getenv("MILVUS_TOKEN", "").strip() or None

    collection_name = (
        args.collection
        or os.getenv("MILVUS_COLLECTION", "").strip()
        or "wechat_chat_chunks"
    )

    batch_size = args.batch_size
    if batch_size is None:
        batch_size = int(os.getenv("BATCH_SIZE", "100"))

    openai_client = build_openai_client()
    milvus_client = build_milvus_client(uri=milvus_uri, token=milvus_token)

    chunks = list(load_jsonl(input_path))
    if args.limit is not None:
        chunks = chunks[: args.limit]

    if not chunks:
        raise RuntimeError("No chunk records found in input file.")

    print(f"[INFO] Loaded {len(chunks)} chunks from {input_path}")
    print(f"[INFO] Embedding model: {embedding_model}")
    print(f"[INFO] Milvus URI: {milvus_uri}")
    print(f"[INFO] Collection: {collection_name}")
    print(f"[INFO] Batch size: {batch_size}")

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

    print(f"[OK] Inserted {inserted} rows into Milvus collection: {collection_name}")


if __name__ == "__main__":
    main()
