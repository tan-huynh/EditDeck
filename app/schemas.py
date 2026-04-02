from typing import List, Optional

from pydantic import BaseModel, Field


class SlideOutline(BaseModel):
    page: int
    title: str
    key_points: List[str] = Field(default_factory=list)


class OutlineResult(BaseModel):
    deck_title: str
    slides: List[SlideOutline]


class SlideResult(BaseModel):
    page: int
    title: str
    prompt: str
    image_url: str
    image_path: str = ""


class EditableSlideResult(BaseModel):
    page: int
    image_path: str
    assets_json_path: str = ""
    asset_count: int = 0
    selected_attempt: int = 1
    attempt_dir: str = ""
    builder_path: str = ""
    preview_html_path: str = ""
    preview_pptx_path: str = ""
    remaining_ph_count: int = 0


class EditableDeckResult(BaseModel):
    run_id: str
    output_dir: str
    pptx_path: str
    pptx_url: str = ""
    total_remaining_ph_count: int = 0
    slides: List[EditableSlideResult] = Field(default_factory=list)


class GenerateResponse(BaseModel):
    run_id: str
    requirement: str
    deck_title: str
    style_prompt: str
    pptx_url: str
    pptx_path: str = ""
    output_dir: str
    log_dir: str = ""
    trace_path: str = ""
    progress_log_path: str = ""
    outline: List[SlideOutline]
    slides: List[SlideResult]
    editable_deck: Optional[EditableDeckResult] = None
