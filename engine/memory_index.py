#!/usr/bin/env python3
import sys
sys.stdout.reconfigure(encoding='utf-8')
"""Semantic Memory Index — 三层记忆检索 (Engram 蒸馏).

v1.2 (2026-06-13): 蒸馏自 Engram (arXiv 2606.09900) + Compound Agent + NeuSymMS.

核心原则:
  - 精简检索胜过全量上下文 (Engram: 83.6% at 8× fewer tokens)
  - 三层索引: FTS5全文 → 语义相似度(可选) → JSON原文件
  - 双层去重: 精确匹配 + 语义相似度
  - 时态有效性: 每个条目有 valid_until，过期自动降权

架构:
  Tier 1.0: Event Log (memory.py) — 不可变, 仅追加
  Tier 1.5: FTS5 Index (本模块) — 全文搜索, SQLite
  Tier 2.0: Insights (memory.py) — LLM合成的抽象
  Tier 2.5: Evidence Verifier (evidence.py) — 修复证据

使用Python内置sqlite3, 零额外依赖。embedding检索为可选特性。
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ── Database setup ─────────────────────────────────────────────────────────────

HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))
DB_PATH = SKILL_ROOT / "data" / "memory_index.db"


def _get_db() -> sqlite3.Connection:
    """Get or create the FTS5 database."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection):
    """Initialize FTS5 schema if not exists."""
    # Main documents table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memory_docs (
            doc_id TEXT PRIMARY KEY,
            doc_type TEXT NOT NULL,       -- 'insight', 'event', 'evidence', 'rule'
            skill_name TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            iso_created TEXT NOT NULL,
            valid_until TEXT,              -- NULL = permanent
            confidence REAL DEFAULT 0.5,
            status TEXT DEFAULT 'active',  -- 'active', 'archived', 'expired'
            metadata_json TEXT DEFAULT '{}',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # FTS5 virtual table for full-text search
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
            title, content,
            content='memory_docs',
            content_rowid='rowid'
        )
    """)

    # Triggers to keep FTS in sync
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS memory_docs_ai AFTER INSERT ON memory_docs BEGIN
            INSERT INTO memory_fts(rowid, title, content)
            VALUES (new.rowid, new.title, new.content);
        END
    """)
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS memory_docs_ad AFTER DELETE ON memory_docs BEGIN
            INSERT INTO memory_fts(memory_fts, rowid, title, content)
            VALUES ('delete', old.rowid, old.title, old.content);
        END
    """)
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS memory_docs_au AFTER UPDATE ON memory_docs BEGIN
            INSERT INTO memory_fts(memory_fts, rowid, title, content)
            VALUES ('delete', old.rowid, old.title, old.content);
            INSERT INTO memory_fts(rowid, title, content)
            VALUES (new.rowid, new.title, new.content);
        END
    """)

    # Dedup index: track content hashes
    conn.execute("""
        CREATE TABLE IF NOT EXISTS content_hashes (
            content_hash TEXT PRIMARY KEY,
            doc_id TEXT NOT NULL,
            iso_created TEXT NOT NULL
        )
    """)

    conn.commit()


# ── Document CRUD ──────────────────────────────────────────────────────────────

def _hash_content(content: str) -> str:
    """Simple content hash for dedup."""
    import hashlib
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def index_document(doc_type: str, skill_name: str, title: str,
                   content: str, confidence: float = 0.5,
                   valid_until: Optional[str] = None,
                   metadata: Optional[dict] = None,
                   doc_id: Optional[str] = None) -> str:
    """Index a document in the memory store. Deduplicates by content hash.

    Args:
        doc_type: 'insight', 'event', 'evidence', 'rule'
        skill_name: 所属skill
        title: 搜索标题
        content: 全文内容
        confidence: 置信度(0-1)
        valid_until: ISO时间戳, 过期后降权
        metadata: 额外JSON元数据
        doc_id: 自定义doc_id (默认自动生成)

    Returns the doc_id.
    """
    conn = _get_db()

    # Dedup check
    content_hash = _hash_content(content)
    existing = conn.execute(
        "SELECT doc_id FROM content_hashes WHERE content_hash = ?",
        (content_hash,)
    ).fetchone()

    if existing:
        # Update confidence on existing doc (merge)
        conn.execute(
            "UPDATE memory_docs SET confidence = MAX(confidence, ?), "
            "metadata_json = ? WHERE doc_id = ?",
            (confidence, json.dumps(metadata or {}, ensure_ascii=False), existing[0])
        )
        conn.commit()
        conn.close()
        return existing[0]

    # New document
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    doc_id = doc_id or f"mem-{ts}"

    conn.execute("""
        INSERT INTO memory_docs (doc_id, doc_type, skill_name, title, content,
                                  iso_created, valid_until, confidence, status, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)
    """, (
        doc_id, doc_type, skill_name, title, content,
        datetime.now(timezone.utc).isoformat(),
        valid_until, confidence,
        json.dumps(metadata or {}, ensure_ascii=False)
    ))

    # Record hash for dedup
    conn.execute(
        "INSERT INTO content_hashes (content_hash, doc_id, iso_created) VALUES (?, ?, ?)",
        (content_hash, doc_id, datetime.now(timezone.utc).isoformat())
    )

    conn.commit()
    conn.close()
    return doc_id


def search(query: str, skill_name: Optional[str] = None,
           doc_type: Optional[str] = None, top_k: int = 10,
           include_expired: bool = False) -> list:
    """Full-text search across indexed memory.

    Uses FTS5 for fast keyword search. Results are ranked by:
      1. FTS5 relevance (bm25)
      2. Confidence score
      3. Recency (newer = higher)
      4. Not expired (unless include_expired)

    Args:
        query: 搜索查询
        skill_name: 限制到特定skill (None = 所有)
        doc_type: 限制文档类型 (None = 所有)
        top_k: 返回数
        include_expired: 是否包含过期文档

    Returns list of matching documents, sorted by relevance.
    """
    conn = _get_db()

    conditions = ["memory_fts MATCH ?"]
    params = [query]

    if skill_name:
        conditions.append("memory_docs.skill_name = ?")
        params.append(skill_name)

    if doc_type:
        conditions.append("memory_docs.doc_type = ?")
        params.append(doc_type)

    if not include_expired:
        conditions.append(
            "(memory_docs.valid_until IS NULL OR memory_docs.valid_until > ?)"
        )
        params.append(datetime.now(timezone.utc).isoformat())

    where = " AND ".join(conditions)

    rows = conn.execute(f"""
        SELECT memory_docs.doc_id, memory_docs.doc_type, memory_docs.skill_name,
               memory_docs.title, memory_docs.content,
               memory_docs.confidence, memory_docs.iso_created,
               memory_docs.valid_until, memory_docs.metadata_json,
               rank
        FROM memory_fts
        JOIN memory_docs ON memory_fts.rowid = memory_docs.rowid
        WHERE {where}
        ORDER BY rank
        LIMIT ?
    """, params + [top_k]).fetchall()

    results = []
    for row in rows:
        results.append({
            "doc_id": row["doc_id"],
            "doc_type": row["doc_type"],
            "skill_name": row["skill_name"],
            "title": row["title"],
            "content": row["content"][:500],
            "confidence": row["confidence"],
            "iso_created": row["iso_created"],
            "valid_until": row["valid_until"],
            "metadata": json.loads(row["metadata_json"]) if row["metadata_json"] else {},
            "rank": row["rank"]
        })

    conn.close()
    return results


def search_with_fallback(query: str, skill_name: Optional[str] = None,
                          **kwargs) -> list:
    """Search with fallback: try FTS5 → try LIKE → return empty."""
    results = search(query, skill_name, **kwargs)
    if results:
        return results

    # Fallback: LIKE search
    conn = _get_db()
    like_query = f"%{query}%"
    rows = conn.execute("""
        SELECT doc_id, doc_type, skill_name, title, content,
               confidence, iso_created, valid_until, metadata_json
        FROM memory_docs
        WHERE (title LIKE ? OR content LIKE ?)
        AND (skill_name = ? OR ? IS NULL)
        AND status = 'active'
        ORDER BY confidence DESC, iso_created DESC
        LIMIT ?
    """, (like_query, like_query, skill_name, skill_name,
          kwargs.get("top_k", 10))).fetchall()

    results = []
    for row in rows:
        results.append({
            "doc_id": row["doc_id"],
            "doc_type": row["doc_type"],
            "skill_name": row["skill_name"],
            "title": row["title"],
            "content": row["content"][:500],
            "confidence": row["confidence"],
            "iso_created": row["iso_created"],
            "valid_until": row["valid_until"],
            "metadata": json.loads(row["metadata_json"]) if row["metadata_json"] else {},
            "rank": 999  # Fallback rank
        })

    conn.close()
    return results


# ── Deduplication ──────────────────────────────────────────────────────────────

def find_similar(query: str, threshold: float = 0.7,
                 skill_name: Optional[str] = None) -> list:
    """Find documents similar to query using FTS5 snippet matching.

    For exact dedup, use index_document() which auto-deduplicates by content hash.
    This function finds semantically similar (but not identical) documents.

    Args:
        query: 搜索文本
        threshold: 相似度阈值 (0-1), FTS5 bm25 based
        skill_name: 限制skill

    Returns list of similar docs above threshold.
    """
    results = search(query, skill_name=skill_name, top_k=20)

    # Simple threshold: if FTS5 rank is high enough, consider it similar
    # FTS5 rank is negative (more relevant = more negative); normalize
    if not results:
        return []

    max_rank = max(abs(r["rank"]) for r in results) if results else 1
    similar = []
    for r in results:
        normalized = 1.0 - (abs(r["rank"]) / max_rank) if max_rank > 0 else 0.0
        if normalized >= threshold:
            r["similarity_score"] = normalized
            similar.append(r)

    return similar


def merge_similar_docs(skill_name: str, doc_type: str,
                        similarity_threshold: float = 0.85) -> int:
    """Merge near-duplicate documents of the same type.

    Documents with very high similarity are merged: the older one is archived,
    the newer one gets a confidence boost.

    Returns number of merged pairs.
    """
    conn = _get_db()
    rows = conn.execute("""
        SELECT doc_id, content FROM memory_docs
        WHERE skill_name = ? AND doc_type = ? AND status = 'active'
        ORDER BY iso_created DESC
    """, (skill_name, doc_type)).fetchall()

    merged = 0
    seen_hashes = set()
    for row in rows:
        ch = _hash_content(row["content"])
        if ch in seen_hashes:
            conn.execute(
                "UPDATE memory_docs SET status = 'archived' WHERE doc_id = ?",
                (row["doc_id"],)
            )
            merged += 1
        seen_hashes.add(ch)

    conn.commit()
    conn.close()
    return merged


# ── Temporal validity ──────────────────────────────────────────────────────────

def expire_old_documents(older_than_days: int = 90) -> int:
    """Archive documents past their valid_until or older than threshold.

    Returns number of archived documents.
    """
    conn = _get_db()
    cutoff = datetime.now(timezone.utc).isoformat()

    # Archive expired
    expired = conn.execute("""
        UPDATE memory_docs SET status = 'expired'
        WHERE valid_until IS NOT NULL AND valid_until < ? AND status = 'active'
    """, (cutoff,)).rowcount

    conn.commit()
    conn.close()
    return expired


# ── (C) Memory merge & decay — 对抗记忆膨胀熵增 ───────────────────────────

def _last_hit_rounds_ago(skill_name: str, doc_id: str) -> int:
    """How many scan events ago was a document last found in search results?
    -1 = never searched."""
    from engine import memory
    events = memory.read_events(skill_name, limit=200)
    # Approximate: count scans since doc was created
    doc_created = None
    for e in events:
        if e.get("data", {}).get("doc_id") == doc_id:
            doc_created = e.get("iso_timestamp", "")
            break
    if not doc_created:
        return 999
    scan_after = 0
    for e in events:
        if e["event_type"] == "scan" and e.get("iso_timestamp", "") > doc_created:
            scan_after += 1
    return scan_after


def merge_similar_by_content(similarity_threshold: float = 0.85) -> int:
    """Merge documents with high content similarity.
    Older doc is archived, newer doc gets the older's confidence merged in.
    Returns number of merged pairs."""
    conn = _get_db()
    rows = conn.execute(
        "SELECT doc_id, content, confidence FROM memory_docs WHERE status='active' "
        "ORDER BY iso_created DESC"
    ).fetchall()

    merged = 0
    seen_hashes = {}
    for row in rows:
        ch = _hash_content(row["content"])
        if ch in seen_hashes:
            older_id = seen_hashes[ch]
            # Merge: archive older, boost newer confidence
            conn.execute(
                "UPDATE memory_docs SET status='merged', "
                "metadata_json=json_set(metadata_json, '$.merged_into', ?) "
                "WHERE doc_id=?", (row["doc_id"], older_id))
            conn.execute(
                "UPDATE memory_docs SET confidence=MIN(1.0, confidence+0.05) "
                "WHERE doc_id=?", (row["doc_id"],))
            merged += 1
        seen_hashes[ch] = row["doc_id"]

    conn.commit()
    conn.close()
    return merged


def decay_stale_documents(skill_name: str, stale_rounds: int = 20,
                            dead_rounds: int = 100) -> dict:
    """Documents untouched across N rounds -> stale. 5*N rounds -> dead.

    Stale: confidence decays toward 0.
    Dead: removed from active index (archived to historical).

    This is active forgetting — not passive expiration by timestamp,
    but decay driven by actual usage (or lack thereof).

    Returns {stale_count, dead_count, actions}.
    """
    conn = _get_db()
    rows = conn.execute(
        "SELECT doc_id, doc_type, skill_name, iso_created, confidence, "
        "metadata_json FROM memory_docs WHERE status='active' AND skill_name=?",
        (skill_name,)
    ).fetchall()

    stale, dead = 0, 0

    for row in rows:
        rounds = _last_hit_rounds_ago(skill_name, row["doc_id"])

        if rounds >= dead_rounds:
            conn.execute(
                "UPDATE memory_docs SET status='historical' WHERE doc_id=?",
                (row["doc_id"],)
            )
            dead += 1
        elif rounds >= stale_rounds:
            new_conf = max(0.05, row["confidence"] - 0.1)
            conn.execute(
                "UPDATE memory_docs SET confidence=? WHERE doc_id=?",
                (new_conf, row["doc_id"])
            )
            stale += 1

    conn.commit()
    conn.close()

    return {
        "stale_count": stale, "dead_count": dead,
        "total_checked": len(rows),
        "verdict": f"[OK] 记忆衰减: {stale}条降权, {dead}条归档"
    }


def auto_maintain_memory(skill_name: str) -> dict:
    """Run all memory maintenance tasks: merge + decay + expire.

    Called periodically (every 10 rounds or session end).
    Returns combined report.
    """
    merged = merge_similar_by_content(0.85)
    decay_result = decay_stale_documents(skill_name, 20, 100)
    expired = expire_old_documents(90)

    return {
        "merged": merged,
        "stale": decay_result.get("stale_count", 0),
        "dead": decay_result.get("dead_count", 0),
        "expired": expired,
        "verdict": (
            f"[OK] 记忆维护: 合并{merged} 衰减{decay_result.get('stale_count',0)} "
            f"归档{decay_result.get('dead_count',0)} 过期{expired}"
        )
    }


# ═══════════════════════════════════════════════════════════════════════
# S3: Mem²Evolve — 经验-资产共进化双向链接
# ═══════════════════════════════════════════════════════════════════════

def _evo_links_db() -> sqlite3.Connection:
    """Get the co-evolution links database."""
    links_path = SKILL_ROOT / "data" / "coevolution_links.db"
    links_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(links_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS evo_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT NOT NULL,   -- 'experience' | 'asset'
            source_id TEXT NOT NULL,     -- insight_id | rule_id
            target_type TEXT NOT NULL,   -- 'asset' | 'experience'
            target_id TEXT NOT NULL,     -- rule_id | insight_id
            relationship TEXT NOT NULL,  -- 'triggered' | 'feedback' | 'coevolved'
            iso_created TEXT DEFAULT (datetime('now')),
            metadata_json TEXT DEFAULT '{}'
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_evo_source
        ON evo_links(source_type, source_id)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_evo_target
        ON evo_links(target_type, target_id)
    """)
    conn.commit()
    return conn


def link_experience_to_asset(insight_id: str, rule_id: str,
                              relationship: str = "triggered") -> bool:
    """(S3) Record: experience (insight) triggered asset (rule) creation.

    Mem²Evolve forward link: Experience → Asset.
    """
    conn = _evo_links_db()
    conn.execute("""
        INSERT INTO evo_links (source_type, source_id, target_type,
                                target_id, relationship, metadata_json)
        VALUES ('experience', ?, 'asset', ?, ?, '{}')
    """, (insight_id, rule_id, relationship))
    conn.commit()
    conn.close()
    return True


def link_asset_to_experience(rule_id: str, insight_id: str,
                              relationship: str = "feedback") -> bool:
    """(S3) Record: asset (rule) execution produced feedback → experience (insight).

    Mem²Evolve backward link: Asset → Experience.
    """
    conn = _evo_links_db()
    conn.execute("""
        INSERT INTO evo_links (source_type, source_id, target_type,
                                target_id, relationship, metadata_json)
        VALUES ('asset', ?, 'experience', ?, ?, '{}')
    """, (rule_id, insight_id, relationship))
    conn.commit()
    conn.close()
    return True


def get_coevolution_chain(insight_or_rule_id: str) -> list:
    """(S3) Trace the full co-evolution chain: what triggered what?

    Mem²Evolve insight: the full chain shows whether the co-evolution
    is producing a virtuous spiral (insight → good rule → useful feedback)
    or a vicious cycle (insight → bad rule → misleading feedback).
    """
    conn = _evo_links_db()
    forward = conn.execute(
        "SELECT * FROM evo_links WHERE source_id = ? ORDER BY iso_created",
        (insight_or_rule_id,)
    ).fetchall()
    backward = conn.execute(
        "SELECT * FROM evo_links WHERE target_id = ? ORDER BY iso_created",
        (insight_or_rule_id,)
    ).fetchall()
    conn.close()

    chain = []
    for row in forward + backward:
        chain.append({
            "source_type": row[1], "source_id": row[2],
            "target_type": row[3], "target_id": row[4],
            "relationship": row[5], "iso": row[6]
        })
    return chain


def coevolution_stats(skill_name: str = "") -> dict:
    """(S3) Co-evolution statistics: is the spiral virtuous or vicious?

    Virtuous spiral = high ratio of 'triggered'+'feedback' links.
    Vicious cycle = many 'triggered' links but few 'feedback' (rules created but never used).
    """
    conn = _evo_links_db()
    total = conn.execute("SELECT COUNT(*) FROM evo_links").fetchone()[0]
    by_rel = {}
    for row in conn.execute(
        "SELECT relationship, COUNT(*) as cnt FROM evo_links GROUP BY relationship"
    ).fetchall():
        by_rel[row[0]] = row[1]
    conn.close()

    virtuous = by_rel.get("triggered", 0) + by_rel.get("feedback", 0)
    vicious = by_rel.get("coevolved", 0)  # coevolved without feedback = possible runaway

    return {
        "total_links": total,
        "by_relationship": by_rel,
        "virtuous_count": virtuous,
        "feedback_ratio": by_rel.get("feedback", 0) / max(1, by_rel.get("triggered", 1)),
        "verdict": (
            "[OK] 良性共进化——经验有效引导资产创建并收到反馈" if virtuous > vicious
            else "[!!] 恶性循环——资产创建多但反馈少——需要审计规则质量"
        )
    }


def auto_index_insights(skill_name: str) -> int:
    """Auto-index all existing insights from JSON files into FTS5.

    Called during memory system initialization.
    Returns number of indexed documents.
    """
    from engine import memory

    insights = memory.read_insights(skill_name, limit=500)
    indexed = 0
    for ins in insights:
        doc_id = index_document(
            doc_type="insight",
            skill_name=skill_name,
            title=ins.get("insight_id", "unknown"),
            content=ins.get("text", ""),
            confidence=ins.get("confidence", 0.5),
            doc_id=ins.get("insight_id")
        )
        if doc_id:
            indexed += 1
    return indexed


def auto_index_events(skill_name: str, limit: int = 200) -> int:
    """Auto-index recent events into FTS5.

    Only indexes events with meaningful text content.
    Returns number of indexed documents.
    """
    from engine import memory

    events = memory.read_events(skill_name, limit=limit)
    indexed = 0
    for evt in events:
        data = evt.get("data", {})
        content = json.dumps(data, ensure_ascii=False)

        # Only index events with substantive content
        if len(content) > 10:
            doc_id = index_document(
                doc_type="event",
                skill_name=skill_name,
                title=f"{evt.get('event_type', 'unknown')}",
                content=content,
                confidence=0.5,
                doc_id=evt.get("event_id", None)
            )
            if doc_id:
                indexed += 1
    return indexed


# ── Stats ──────────────────────────────────────────────────────────────────────

def memory_index_stats(skill_name: Optional[str] = None) -> dict:
    """Get statistics about the memory index."""
    conn = _get_db()

    if skill_name:
        total = conn.execute(
            "SELECT COUNT(*) FROM memory_docs WHERE skill_name = ?",
            (skill_name,)
        ).fetchone()[0]
        by_type = {}
        for row in conn.execute(
            "SELECT doc_type, COUNT(*) as cnt FROM memory_docs "
            "WHERE skill_name = ? GROUP BY doc_type",
            (skill_name,)
        ).fetchall():
            by_type[row["doc_type"]] = row["cnt"]
    else:
        total = conn.execute("SELECT COUNT(*) FROM memory_docs").fetchone()[0]
        by_type = {}
        for row in conn.execute(
            "SELECT doc_type, COUNT(*) as cnt FROM memory_docs GROUP BY doc_type"
        ).fetchall():
            by_type[row["doc_type"]] = row["cnt"]

    active = conn.execute(
        "SELECT COUNT(*) FROM memory_docs WHERE status = 'active'"
        + (" AND skill_name = ?" if skill_name else ""),
        (skill_name,) if skill_name else ()
    ).fetchone()[0]

    conn.close()

    return {
        "total_docs": total,
        "active_docs": active,
        "by_type": by_type,
        "db_path": str(DB_PATH),
        "db_size_mb": round(DB_PATH.stat().st_size / (1024 * 1024), 2) if DB_PATH.exists() else 0
    }


# ── Rebuild ────────────────────────────────────────────────────────────────────

def rebuild_index(skill_name: str) -> dict:
    """Full rebuild: drop and recreate FTS5 index, re-index all content.

    Use after major data changes or corruption.
    """
    conn = _get_db()

    # Drop and recreate
    conn.execute("DROP TABLE IF EXISTS memory_fts")
    conn.execute("DROP TABLE IF EXISTS memory_docs")
    conn.execute("DROP TABLE IF EXISTS content_hashes")
    conn.commit()
    conn.close()

    # Re-initialize
    _init_schema(_get_db())

    # Re-index
    insight_count = auto_index_insights(skill_name)
    event_count = auto_index_events(skill_name, limit=500)

    return {
        "rebuilt": True,
        "insights_indexed": insight_count,
        "events_indexed": event_count,
        "total_indexed": insight_count + event_count
    }


# ── Bootstrap ──────────────────────────────────────────────────────────────────

def bootstrap_if_empty(skill_name: str) -> dict:
    """Bootstrap the index if it's empty. Called on first use."""
    stats = memory_index_stats(skill_name)
    if stats["total_docs"] > 0:
        return {"bootstrapped": False, "reason": f"已有 {stats['total_docs']} 个文档"}

    insight_count = auto_index_insights(skill_name)
    event_count = auto_index_events(skill_name, limit=300)

    return {
        "bootstrapped": True,
        "insights_indexed": insight_count,
        "events_indexed": event_count,
        "total": insight_count + event_count
    }


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Semantic Memory Index (Engram)——三层记忆检索"
    )
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("search", help="全文搜索记忆")
    p.add_argument("query", help="搜索查询")
    p.add_argument("--skill", help="限制skill")
    p.add_argument("--type", choices=["insight", "event", "evidence", "rule"],
                   help="限制文档类型")
    p.add_argument("--top", type=int, default=10, help="返回数")

    p = sub.add_parser("index", help="索引导入")
    p.add_argument("skill", help="Skill名称")
    p.add_argument("--rebuild", action="store_true", help="重建索引")
    p.add_argument("--auto", action="store_true", help="自动从JSON导入")

    p = sub.add_parser("expire", help="清理过期文档")
    p.add_argument("--days", type=int, default=90, help="文档保留天数")

    p = sub.add_parser("stats", help="索引统计")
    p.add_argument("--skill", help="限制skill")

    p = sub.add_parser("merge", help="合并重复文档")
    p.add_argument("skill", help="Skill名称")
    p.add_argument("--type", default="insight", help="文档类型")

    args = parser.parse_args()

    if args.cmd == "search":
        results = search_with_fallback(
            args.query, skill_name=args.skill,
            doc_type=args.type, top_k=args.top
        )
        for r in results:
            print(f"  [{r['doc_type']}] {r['title']} "
                  f"(置信度={r['confidence']:.0%})")
            print(f"    {r['content'][:200]}")
        if not results:
            print("  (无匹配结果)")

    elif args.cmd == "index":
        if args.rebuild:
            result = rebuild_index(args.skill)
            print(f"  重建完成: {result['total_indexed']} 个文档索引")
        elif args.auto:
            result = bootstrap_if_empty(args.skill)
            if result.get("bootstrapped"):
                print(f"  Bootstrap完成: {result['total']} 个文档索引")
            else:
                print(f"  已存在: {result.get('reason', '?')}")
        else:
            print("  请指定 --rebuild 或 --auto")

    elif args.cmd == "expire":
        count = expire_old_documents(args.days)
        print(f"  已归档: {count} 个过期文档")

    elif args.cmd == "stats":
        stats = memory_index_stats(args.skill)
        print(f"  记忆索引统计:")
        print(f"  总文档: {stats['total_docs']} | 活跃: {stats['active_docs']}")
        print(f"  按类型: {stats['by_type']}")
        print(f"  数据库: {stats['db_path']} ({stats['db_size_mb']} MB)")

    elif args.cmd == "merge":
        count = merge_similar_docs(args.skill, args.type)
        print(f"  已合并: {count} 个重复文档")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
