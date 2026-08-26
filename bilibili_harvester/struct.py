from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, List, Optional


def _safe_get(d: Dict[str, Any], path: List[str], default=None):
    cur = d
    for k in path:
        if isinstance(cur, dict) and k in cur:
            cur = cur[k]
        else:
            return default
    return cur


def build_core(info: Dict[str, Any]) -> Dict[str, Any]:
    pages = []
    if "entries" in info and isinstance(info["entries"], list):
        for e in info["entries"]:
            pages.append({
                "cid": e.get("cid"),
                "title": e.get("title") or e.get("alt_title"),
                "duration": e.get("duration"),
            })
    core = {
        "bvid": info.get("id") if str(info.get("id", "")).startswith("BV") else info.get("bvid") or info.get("id"),
        "aid": info.get("aid"),
        "title": info.get("title"),
        "description": info.get("description") or info.get("desc"),
        "uploader": {
            "id": _safe_get(info, ["uploader_id"]) or _safe_get(info, ["creator"]) or _safe_get(info, ["owner", "id"]) or _safe_get(info, ["owner", "mid"]),
            "name": info.get("uploader") or _safe_get(info, ["owner", "name"]),
        },
        "duration": info.get("duration"),
        "like": _safe_get(info, ["like_count"]) or _safe_get(info, ["stat", "like"]),
        "view": _safe_get(info, ["view_count"]) or _safe_get(info, ["stat", "view"]),
        "danmaku": _safe_get(info, ["stat", "danmaku"]),
        "publish_date": info.get("upload_date") or info.get("release_timestamp"),
        "tags": info.get("tags") or [],
        "pages": pages,
        "thumbnails": info.get("thumbnails") or ([{"url": info.get("thumbnail")}] if info.get("thumbnail") else []),
    }
    return core


def build_streams(info: Dict[str, Any]) -> Dict[str, Any]:
    fmts: List[Dict[str, Any]] = []
    for f in info.get("formats", []) or []:
        fmts.append({
            "format_id": f.get("format_id"),
            "ext": f.get("ext"),
            "filesize": f.get("filesize") or f.get("filesize_approx"),
            "tbr": f.get("tbr"),
            "vcodec": f.get("vcodec"),
            "acodec": f.get("acodec"),
            "width": f.get("width"),
            "height": f.get("height"),
            "fps": f.get("fps"),
            "url": f.get("url"),
        })
    return {"formats": fmts}


def file_manifest(root: Path) -> Dict[str, Any]:
    files = []
    for p in root.rglob("*"):
        if p.is_file():
            try:
                h = sha256()
                with p.open("rb") as f:
                    while True:
                        b = f.read(1024 * 1024)
                        if not b:
                            break
                        h.update(b)
                files.append({
                    "path": str(p.relative_to(root)),
                    "size": p.stat().st_size,
                    "sha256": h.hexdigest(),
                })
            except Exception:
                files.append({
                    "path": str(p.relative_to(root)),
                    "size": p.stat().st_size,
                    "sha256": None,
                })
    return {"files": files}


def _optional_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_comment_node(
    node: Dict[str, Any],
    *,
    page: int,
    depth: int,
    root_rpid: Optional[int],
    parent_rpid: Optional[int],
    out: List[Dict[str, Any]],
) -> None:
    rpid = _optional_int(node.get("rpid"))
    root = rpid if root_rpid is None else root_rpid
    member = node.get("member") if isinstance(node.get("member"), dict) else {}
    content = node.get("content") if isinstance(node.get("content"), dict) else {}
    message = content.get("message")

    out.append(
        {
            "rpid": rpid,
            "root_rpid": root,
            "parent_rpid": parent_rpid,
            "depth": depth,
            "page": page,
            "author": {
                "mid": _optional_int(member.get("mid")),
                "name": str(member.get("uname") or "").strip(),
            },
            "message": str(message or "").strip(),
            "ctime": _optional_int(node.get("ctime")),
            "like": _optional_int(node.get("like")),
            "reply_count": _optional_int(node.get("rcount")),
        }
    )

    children = node.get("replies")
    if not isinstance(children, list):
        return
    for child in children:
        if isinstance(child, dict):
            _normalize_comment_node(
                child,
                page=page,
                depth=depth + 1,
                root_rpid=root,
                parent_rpid=rpid,
                out=out,
            )


def normalize_comments(comments: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten Bilibili's nested reply tree without discarding parentage or source pages."""
    items: List[Dict[str, Any]] = []
    pages = comments.get("pages") if isinstance(comments.get("pages"), list) else []
    for page_number, page in enumerate(pages, start=1):
        if not isinstance(page, dict):
            continue
        data = page.get("data") if isinstance(page.get("data"), dict) else {}
        replies = data.get("replies")
        if not isinstance(replies, list):
            continue
        for reply in replies:
            if isinstance(reply, dict):
                _normalize_comment_node(
                    reply,
                    page=page_number,
                    depth=0,
                    root_rpid=None,
                    parent_rpid=None,
                    out=items,
                )
    return {"source_pages": len(pages), "comments": items}


def _extract_comment_lines(normalized_comments: Dict[str, Any]) -> List[str]:
    lines: List[str] = []
    comments = normalized_comments.get("comments")
    if not isinstance(comments, list):
        return lines
    for item in comments:
        if not isinstance(item, dict):
            continue
        message = str(item.get("message") or "").strip()
        if not message:
            continue
        author = item.get("author") if isinstance(item.get("author"), dict) else {}
        name = str(author.get("name") or "").strip()
        depth = item.get("depth")
        indent = "  " * depth if isinstance(depth, int) and depth > 0 else ""
        prefix = f"{name}: " if name else ""
        lines.append(f"{indent}{prefix}{message}")
    return lines


def write_structured(info: Dict[str, Any], bvid_dir: Path, comments: Dict[str, Any]):
    """写入结构化产物：所有 JSON 归档至 bvid_dir/json/，并额外输出精简版评论文本 comments.txt。"""
    bvid_dir.mkdir(parents=True, exist_ok=True)
    json_dir = bvid_dir / "json"
    json_dir.mkdir(parents=True, exist_ok=True)

    (json_dir / "core.json").write_text(
        json.dumps(build_core(info), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (json_dir / "streams.json").write_text(
        json.dumps(build_streams(info), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (json_dir / "comments_structured.json").write_text(
        json.dumps(comments, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    normalized_comments = normalize_comments(comments)
    (json_dir / "comments_normalized.json").write_text(
        json.dumps(normalized_comments, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 纯文本评论来自规范化树，保留任意层级的缩进关系。
    txt_lines = _extract_comment_lines(normalized_comments)
    (bvid_dir / "comments.txt").write_text("\n".join(txt_lines), encoding="utf-8")

    # 目录清单仍覆盖整个 bvid 目录，便于统一校验
    (bvid_dir / "manifest.json").write_text(
        json.dumps(file_manifest(bvid_dir), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
