from __future__ import annotations

import base64
import json
import struct
from pathlib import Path
from typing import Any, Optional

SLIDE_WIDTH_IN = 10.0
SLIDE_HEIGHT_IN = 5.625


def _read_png_size(image_path: Path) -> Optional[tuple[int, int]]:
    with image_path.open("rb") as handle:
        header = handle.read(24)
    if len(header) >= 24 and header[:8] == b"\x89PNG\r\n\x1a\n":
        width, height = struct.unpack(">II", header[16:24])
        return int(width), int(height)
    return None


def read_image_size(image_path: Path) -> tuple[int, int]:
    png_size = _read_png_size(image_path)
    if png_size:
        return png_size

    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - depends on runtime
        raise RuntimeError(
            f"无法读取图片尺寸: {image_path}。PNG 可直接支持，其他格式需要 Pillow。"
        ) from exc

    with Image.open(image_path) as image:
        width, height = image.size
    return int(width), int(height)


def image_data_string(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".png":
        mime = "image/png"
    elif suffix in {".jpg", ".jpeg"}:
        mime = "image/jpeg"
    elif suffix == ".webp":
        mime = "image/webp"
    else:
        raise ValueError(f"Unsupported asset image format: {path}")
    encoded = base64.b64encode(path.read_bytes()).decode("utf-8")
    return f"{mime};base64,{encoded}"


def build_browser_asset_manifest(assets_json: Path, image_path: Path) -> list[dict[str, Any]]:
    rows = json.loads(assets_json.read_text(encoding="utf-8"))
    image_width, image_height = read_image_size(image_path)

    manifest: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        cutout_path = Path(str(row["cutout_path"]))
        if not cutout_path.is_absolute():
            cutout_path = (assets_json.parent / cutout_path).resolve()
        if not cutout_path.exists():
            raise FileNotFoundError(f"Cutout image missing: {cutout_path}")

        bbox = row.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            raise ValueError(f"Invalid bbox in {assets_json}: {bbox}")

        x1, y1, x2, y2 = [int(value) for value in bbox]
        box_width = max(x2 - x1, 1)
        box_height = max(y2 - y1, 1)
        placeholder_bbox_slide = row.get("placeholder_bbox_slide")
        placeholder_bbox_px = row.get("placeholder_bbox_px")
        source_backend = str(row.get("source_backend", "") or "").strip().lower()

        manifest.append(
            {
                "id": int(row.get("id", index)),
                "asset_kind": row.get("asset_kind", "icon"),
                "prompt": row.get("prompt", ""),
                "score": float(row.get("score", 0.0)),
                "bbox_px": [x1, y1, x2, y2],
                "bbox_slide": {
                    "x": x1 * SLIDE_WIDTH_IN / image_width,
                    "y": y1 * SLIDE_HEIGHT_IN / image_height,
                    "w": box_width * SLIDE_WIDTH_IN / image_width,
                    "h": box_height * SLIDE_HEIGHT_IN / image_height,
                },
                "cutout_size_px": {"w": box_width, "h": box_height},
                "cutout_path": str(cutout_path),
                "data": image_data_string(cutout_path),
                "placeholder_id": str(row.get("placeholder_id", "") or "").strip() or None,
                "placeholder_bbox_px": placeholder_bbox_px if isinstance(placeholder_bbox_px, list) else None,
                "placeholder_bbox_slide": placeholder_bbox_slide if isinstance(placeholder_bbox_slide, dict) else None,
                "source_backend": source_backend or None,
                "match_metrics": row.get("match_metrics"),
            }
        )

    manifest.sort(key=lambda item: (item["bbox_slide"]["y"], item["bbox_slide"]["x"], -item["score"]))
    return manifest


def build_asset_runtime_script(
    manifests_by_slide: dict[str, list[dict[str, Any]]],
    allow_asset_reuse: bool,
    drop_unmatched_placeholders: bool,
) -> str:
    manifest_json = json.dumps(manifests_by_slide, ensure_ascii=False)
    allow_reuse_js = "true" if allow_asset_reuse else "false"
    drop_unmatched_js = "true" if drop_unmatched_placeholders else "false"
    return f"""
<script>
(function () {{
    const AUTO_ASSETS_BY_SLIDE = {manifest_json};
    const ALLOW_REUSE = {allow_reuse_js};
    const DROP_UNMATCHED_PLACEHOLDERS = {drop_unmatched_js};
    const SLIDE_W = {SLIDE_WIDTH_IN};
    const SLIDE_H = {SLIDE_HEIGHT_IN};

    window.__AUTO_ASSET_MATCHES = {{}};
    window.__AUTO_ASSET_USED_IDS = {{}};

    function getSlideKey(targetSlide) {{
        if (!targetSlide) return '';
        return targetSlide.__slideKey || targetSlide._autoSlideKey || '';
    }}

    function getAssets(targetSlide) {{
        const key = getSlideKey(targetSlide);
        return AUTO_ASSETS_BY_SLIDE[key] || [];
    }}

    function getUsedSet(targetSlide) {{
        const key = getSlideKey(targetSlide);
        if (!window.__AUTO_ASSET_USED_IDS[key]) {{
            window.__AUTO_ASSET_USED_IDS[key] = new Set();
        }}
        return window.__AUTO_ASSET_USED_IDS[key];
    }}

    function pushMatch(targetSlide, payload) {{
        const key = getSlideKey(targetSlide);
        if (!window.__AUTO_ASSET_MATCHES[key]) {{
            window.__AUTO_ASSET_MATCHES[key] = [];
        }}
        window.__AUTO_ASSET_MATCHES[key].push(payload);
    }}

    function rectArea(box) {{
        return Math.max(0, box.w) * Math.max(0, box.h);
    }}

    function intersectionArea(a, b) {{
        const x1 = Math.max(a.x, b.x);
        const y1 = Math.max(a.y, b.y);
        const x2 = Math.min(a.x + a.w, b.x + b.w);
        const y2 = Math.min(a.y + a.h, b.y + b.h);
        return Math.max(0, x2 - x1) * Math.max(0, y2 - y1);
    }}

    function iou(a, b) {{
        const inter = intersectionArea(a, b);
        if (inter <= 0) return 0;
        const union = rectArea(a) + rectArea(b) - inter;
        return inter / Math.max(union, 1e-6);
    }}

    function centerDistanceNorm(a, b) {{
        const phCx = a.x + a.w / 2;
        const phCy = a.y + a.h / 2;
        const boxCx = b.x + b.w / 2;
        const boxCy = b.y + b.h / 2;
        return Math.hypot(phCx - boxCx, phCy - boxCy) / Math.hypot(SLIDE_W, SLIDE_H);
    }}

    function fitContain(ph, asset) {{
        const pad = Math.min(ph.w, ph.h) * 0.04;
        const safeW = Math.max(ph.w - pad * 2, 0.01);
        const safeH = Math.max(ph.h - pad * 2, 0.01);
        const assetW = Math.max(asset.cutout_size_px.w, 1);
        const assetH = Math.max(asset.cutout_size_px.h, 1);
        const scale = Math.min(safeW / assetW, safeH / assetH);
        const drawW = assetW * scale;
        const drawH = assetH * scale;
        return {{
            x: ph.x + (ph.w - drawW) / 2,
            y: ph.y + (ph.h - drawH) / 2,
            w: drawW,
            h: drawH
        }};
    }}

    function placeholderScore(ph, asset) {{
        const assetBox = asset.bbox_slide;
        const phArea = Math.max(rectArea(ph), 1e-6);
        const assetArea = Math.max(rectArea(assetBox), 1e-6);
        const contain = intersectionArea(ph, assetBox) / Math.max(Math.min(phArea, assetArea), 1e-6);
        const overlap = iou(ph, assetBox);

        const phCx = ph.x + ph.w / 2;
        const phCy = ph.y + ph.h / 2;
        const assetCx = assetBox.x + assetBox.w / 2;
        const assetCy = assetBox.y + assetBox.h / 2;
        const centerDist = Math.hypot(phCx - assetCx, phCy - assetCy);
        const centerScore = 1 - Math.min(centerDist / Math.hypot(SLIDE_W, SLIDE_H), 1);

        const phAspect = ph.w / Math.max(ph.h, 1e-6);
        const assetAspect = assetBox.w / Math.max(assetBox.h, 1e-6);
        const aspectScore = 1 - Math.min(Math.abs(Math.log(phAspect / assetAspect)) / 2.5, 1);

        const areaScore = 1 - Math.min(Math.abs(Math.log(phArea / assetArea)) / 3.0, 1);

        let kindBonus = 0;
        if (asset.asset_kind === 'illustration' && phArea > 0.25) kindBonus += 0.30;
        if (asset.asset_kind !== 'illustration' && phArea <= 0.25) kindBonus += 0.15;

        return overlap * 5.0 + contain * 3.0 + centerScore * 1.75 + aspectScore * 1.5 + areaScore * 1.25 + kindBonus;
    }}

    function pickPlaceholderBoundAsset(targetSlide, ph) {{
        const usedSet = getUsedSet(targetSlide);
        const boundAssets = getAssets(targetSlide)
            .filter((asset) => asset && asset.placeholder_bbox_slide && !usedSet.has(asset.id));
        if (!boundAssets.length) return null;

        const ranked = boundAssets
            .map((asset) => {{
                const distance = centerDistanceNorm(ph, asset.placeholder_bbox_slide);
                return {{ asset, distance, used: false }};
            }})
            .sort((a, b) => a.distance - b.distance);

        return ranked[0] || null;
    }}

    function pickAsset(targetSlide, ph) {{
        const assets = getAssets(targetSlide);
        if (!assets.length) return null;

        const boundAsset = pickPlaceholderBoundAsset(targetSlide, ph);
        if (boundAsset) {{
            return boundAsset;
        }}
        if (assets.some((asset) => asset && asset.placeholder_bbox_slide)) {{
            return null;
        }}

        const usedSet = getUsedSet(targetSlide);
        const ranked = assets
            .map((asset) => {{
                return {{
                    asset,
                    used: usedSet.has(asset.id),
                    score: placeholderScore(ph, asset)
                }};
            }})
            .sort((a, b) => b.score - a.score);

        const unused = ranked.filter((item) => !item.used);
        if (unused.length > 0) {{
            return unused[0];
        }}

        if (ALLOW_REUSE) {{
            return ranked[0] || null;
        }}

        return null;
    }}

    function drawFallback(targetSlide, x, y, w, h) {{
        targetSlide.addShape('rect', {{
            x: x, y: y, w: w, h: h,
            fill: {{ color: 'E0E0E0' }},
            line: {{ color: '999999', width: 1, dashType: 'solid' }}
        }});
        targetSlide.addText('PH', {{
            x: x, y: y, w: w, h: h,
            align: 'center',
            valign: 'mid',
            fontSize: 8,
            color: '333333',
            bold: true
        }});
    }}

    window.__AUTO_FILL_PH = function () {{
        let targetSlide = null;
        let x = 0;
        let y = 0;
        let w = 0;
        let h = 0;

        if (arguments.length >= 6 && arguments[0] && typeof arguments[0].addText === 'function') {{
            targetSlide = arguments[0];
            x = Number(arguments[2]);
            y = Number(arguments[3]);
            w = Number(arguments[4]);
            h = Number(arguments[5]);
        }} else {{
            targetSlide = (typeof slide !== 'undefined' && slide)
                ? slide
                : (window.__slideRef || window.slide || null);
            x = Number(arguments[0]);
            y = Number(arguments[1]);
            w = Number(arguments[2]);
            h = Number(arguments[3]);
        }}

        if (!targetSlide) return;

        const ph = {{ x, y, w, h }};
        const picked = pickAsset(targetSlide, ph);
        if (!picked || !picked.asset) {{
            pushMatch(targetSlide, {{
                placeholder: ph,
                asset_id: null,
                status: DROP_UNMATCHED_PLACEHOLDERS ? 'deleted_no_asset' : 'fallback_no_asset'
            }});
            if (!DROP_UNMATCHED_PLACEHOLDERS) {{
                drawFallback(targetSlide, x, y, w, h);
            }}
            return;
        }}

        const usedSet = getUsedSet(targetSlide);
        const asset = picked.asset;
        const fit = fitContain(ph, asset);
        try {{
            targetSlide.addImage({{
                data: asset.data,
                x: fit.x,
                y: fit.y,
                w: fit.w,
                h: fit.h
            }});
            usedSet.add(asset.id);
            pushMatch(targetSlide, {{
                placeholder: ph,
                asset_id: asset.id,
                asset_kind: asset.asset_kind,
                prompt: asset.prompt,
                score: typeof picked.score === 'number' ? picked.score : null,
                center_distance_norm: typeof picked.distance === 'number' ? picked.distance : null,
                reused: picked.used
            }});
        }} catch (error) {{
            pushMatch(targetSlide, {{
                placeholder: ph,
                asset_id: asset.id,
                status: DROP_UNMATCHED_PLACEHOLDERS ? 'deleted_addImage_error' : 'fallback_addImage_error',
                error: String(error)
            }});
            if (!DROP_UNMATCHED_PLACEHOLDERS) {{
                drawFallback(targetSlide, x, y, w, h);
            }}
        }}
    }};

    window.addPH = function () {{
        return window.__AUTO_FILL_PH.apply(window, arguments);
    }};
}})();
</script>
""".strip()


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
