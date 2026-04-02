from __future__ import annotations

import json
import random
import re
from pathlib import Path
from typing import Any, Optional

from openai import OpenAI

from .assets import ensure_dir, write_json
from .codegen import encode_image, image_mime_from_path, normalize_content

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "editable_mask_builder.md"
ALLOWED_ASSET_KINDS = {"icon", "illustration", "logo", "photo", "screenshot", "graphic"}


def load_prompt_text(prompt_file: Optional[Path] = None) -> str:
    path = prompt_file or PROMPT_PATH
    if not path.exists():
        raise FileNotFoundError(f"Gemini mask prompt file not found: {path}")
    return path.read_text(encoding="utf-8")


def _extract_json_text(raw_text: str) -> str:
    stripped = raw_text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped

    fenced = re.search(r"```json\s*(.*?)```", raw_text, re.IGNORECASE | re.DOTALL)
    if fenced:
        return fenced.group(1).strip()

    start = raw_text.find("{")
    end = raw_text.rfind("}")
    if start >= 0 and end > start:
        return raw_text[start : end + 1].strip()
    raise ValueError("Gemini mask response did not contain JSON.")


def _load_pillow():
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:  # pragma: no cover - depends on runtime
        raise RuntimeError("Gemini mask extraction requires Pillow.") from exc
    return Image, ImageDraw


def _clip_int(value: Any, lower: int, upper: int) -> int:
    try:
        parsed = int(round(float(value)))
    except (TypeError, ValueError):
        parsed = lower
    return max(lower, min(parsed, upper))


def _normalize_bbox(raw_bbox: Any, width: int, height: int) -> Optional[list[int]]:
    if not isinstance(raw_bbox, list) or len(raw_bbox) != 4:
        return None
    x1 = _clip_int(raw_bbox[0], 0, max(width - 1, 0))
    y1 = _clip_int(raw_bbox[1], 0, max(height - 1, 0))
    x2 = _clip_int(raw_bbox[2], x1 + 1, width)
    y2 = _clip_int(raw_bbox[3], y1 + 1, height)
    if x2 <= x1 or y2 <= y1:
        return None
    return [x1, y1, x2, y2]


def _normalize_polygon(raw_polygon: Any, width: int, height: int) -> list[list[int]]:
    if not isinstance(raw_polygon, list):
        return []
    points: list[list[int]] = []
    for item in raw_polygon:
        if not isinstance(item, list) or len(item) != 2:
            continue
        x = _clip_int(item[0], 0, max(width - 1, 0))
        y = _clip_int(item[1], 0, max(height - 1, 0))
        points.append([x, y])
    return points


def _bbox_from_polygon(points: list[list[int]]) -> Optional[list[int]]:
    if len(points) < 3:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    x1 = min(xs)
    y1 = min(ys)
    x2 = max(xs) + 1
    y2 = max(ys) + 1
    if x2 <= x1 or y2 <= y1:
        return None
    return [x1, y1, x2, y2]


def _bbox_polygon(bbox: list[int]) -> list[list[int]]:
    x1, y1, x2, y2 = bbox
    return [[x1, y1], [x2 - 1, y1], [x2 - 1, y2 - 1], [x1, y2 - 1]]


def _placeholder_lookup(placeholders: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(item["placeholder_id"]): item for item in placeholders}


def _normalize_assets_payload(
    payload: dict[str, Any],
    placeholders: list[dict[str, Any]],
    image_width: int,
    image_height: int,
) -> list[dict[str, Any]]:
    placeholder_map = _placeholder_lookup(placeholders)
    rows = payload.get("assets", [])
    if not isinstance(rows, list):
        raise ValueError("Gemini mask JSON must contain an `assets` array.")

    by_placeholder: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        placeholder_id = str(row.get("placeholder_id", "")).strip()
        if not placeholder_id or placeholder_id not in placeholder_map:
            continue

        polygon = _normalize_polygon(row.get("polygon_px"), image_width, image_height)
        bbox = _normalize_bbox(row.get("bbox_px"), image_width, image_height)
        if not bbox and polygon:
            bbox = _bbox_from_polygon(polygon)
        if not polygon and bbox:
            polygon = _bbox_polygon(bbox)
        if not bbox or len(polygon) < 3:
            continue

        asset_kind = str(row.get("asset_kind", "graphic") or "graphic").strip().lower()
        if asset_kind not in ALLOWED_ASSET_KINDS:
            asset_kind = "graphic"

        prompt = str(row.get("prompt", "") or "").strip() or f"{asset_kind} asset"
        try:
            confidence = float(row.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0

        normalized = {
            "placeholder_id": placeholder_id,
            "asset_kind": asset_kind,
            "prompt": prompt[:80],
            "score": max(0.0, min(confidence, 1.0)),
            "bbox": bbox,
            "polygon": polygon,
            "placeholder_bbox_px": placeholder_map[placeholder_id]["bbox_px"],
            "placeholder_bbox_slide": placeholder_map[placeholder_id]["bbox_slide"],
        }

        prev = by_placeholder.get(placeholder_id)
        if prev is None or normalized["score"] >= prev["score"]:
            by_placeholder[placeholder_id] = normalized

    ordered: list[dict[str, Any]] = []
    for placeholder in placeholders:
        placeholder_id = str(placeholder["placeholder_id"])
        asset = by_placeholder.get(placeholder_id)
        if asset is not None:
            ordered.append(asset)
    return ordered


def _render_assets_to_disk(
    *,
    image_path: Path,
    assets_dir: Path,
    assets: list[dict[str, Any]],
) -> Path:
    Image, ImageDraw = _load_pillow()
    ensure_dir(assets_dir)
    masks_dir = assets_dir / "masks"
    cutouts_dir = assets_dir / "cutouts"
    ensure_dir(masks_dir)
    ensure_dir(cutouts_dir)

    with Image.open(image_path).convert("RGBA") as source_rgba:
        image_width, image_height = source_rgba.size
        overlay = source_rgba.copy()
        overlay_draw = ImageDraw.Draw(overlay, "RGBA")
        rng = random.Random(2026)
        export_rows: list[dict[str, Any]] = []

        for index, asset in enumerate(assets):
            mask = Image.new("L", (image_width, image_height), 0)
            mask_draw = ImageDraw.Draw(mask)
            polygon = [tuple(point) for point in asset["polygon"]]
            if len(polygon) >= 3:
                mask_draw.polygon(polygon, fill=255)
            else:
                x1, y1, x2, y2 = asset["bbox"]
                mask_draw.rectangle((x1, y1, x2, y2), fill=255)

            bbox = mask.getbbox()
            if bbox is None:
                continue
            x1, y1, x2, y2 = [int(value) for value in bbox]
            if x2 <= x1 or y2 <= y1:
                continue

            color = (
                rng.randint(0, 255),
                rng.randint(0, 255),
                rng.randint(0, 255),
                120,
            )
            overlay_draw.polygon(polygon, fill=color, outline=color[:3] + (255,))

            name = f"{index:03d}_{asset['asset_kind']}_{asset['placeholder_id']}"
            mask_path = masks_dir / f"{name}.png"
            cutout_path = cutouts_dir / f"{name}.png"
            mask.save(mask_path)

            cutout = source_rgba.crop((x1, y1, x2, y2))
            cutout_mask = mask.crop((x1, y1, x2, y2))
            cutout.putalpha(cutout_mask)
            cutout.save(cutout_path)

            export_rows.append(
                {
                    "id": len(export_rows),
                    "placeholder_id": asset["placeholder_id"],
                    "asset_kind": asset["asset_kind"],
                    "prompt": asset["prompt"],
                    "score": asset["score"],
                    "bbox": [x1, y1, x2, y2],
                    "polygon": asset["polygon"],
                    "placeholder_bbox_px": asset["placeholder_bbox_px"],
                    "placeholder_bbox_slide": asset["placeholder_bbox_slide"],
                    "mask_path": str(mask_path),
                    "cutout_path": str(cutout_path),
                    "source_backend": "gemini",
                }
            )

        overlay.save(assets_dir / "overlay.png")
        assets_json = assets_dir / "assets.json"
        write_json(assets_json, export_rows)
        return assets_json


def resolve_gemini_assets_json(
    *,
    image_path: Path,
    builder_text: str,
    placeholders: list[dict[str, Any]],
    assets_dir: Path,
    base_url: str,
    api_key: str,
    model: str,
    max_tokens: int,
    force_reextract_assets: bool,
) -> Path:
    assets_dir = assets_dir.resolve()
    ensure_dir(assets_dir)
    assets_json = assets_dir / "assets.json"
    if assets_json.exists() and not force_reextract_assets:
        return assets_json

    Image, _ = _load_pillow()
    with Image.open(image_path) as image:
        image_width, image_height = image.size

    write_json(assets_dir / "placeholders.json", placeholders)
    prompt_text = load_prompt_text()
    client = OpenAI(base_url=base_url, api_key=api_key)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            prompt_text
                            + "\n\n## Placeholder List\n"
                            + json.dumps(placeholders, ensure_ascii=False, indent=2)
                            + "\n\n## Editable PPT JavaScript\n```javascript\n"
                            + builder_text[:24000]
                            + "\n```"
                            + "\n\n## Image Size\n"
                            + json.dumps({"width": image_width, "height": image_height})
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{image_mime_from_path(image_path)};base64,{encode_image(image_path)}"
                        },
                    },
                ],
            }
        ],
        max_tokens=max_tokens,
    )

    raw_text = normalize_content(response.choices[0].message.content)
    (assets_dir / "mask_response.txt").write_text(raw_text, encoding="utf-8")
    payload = json.loads(_extract_json_text(raw_text))
    normalized_assets = _normalize_assets_payload(payload, placeholders, image_width, image_height)
    if not normalized_assets:
        raise RuntimeError(f"Gemini did not return any usable mask assets for slide: {image_path}")

    return _render_assets_to_disk(image_path=image_path, assets_dir=assets_dir, assets=normalized_assets)
