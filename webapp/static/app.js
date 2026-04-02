const form = document.getElementById("generate-form");
const statusBox = document.getElementById("status");
const resultBox = document.getElementById("result");
const submitBtn = document.getElementById("submit-btn");
const convertBtn = document.getElementById("convert-editable-btn");

const deckTitle = document.getElementById("deck-title");
const stylePrompt = document.getElementById("style-prompt");
const outlineList = document.getElementById("outline-list");
const slidesGrid = document.getElementById("slides-grid");
const pptxWrap = document.getElementById("pptx-wrap");
const pptxLink = document.getElementById("pptx-link");
const editableWrap = document.getElementById("editable-wrap");
const editableLink = document.getElementById("editable-link");
const editableMeta = document.getElementById("editable-meta");

const progressCard = document.getElementById("progress-card");
const progressStep = document.getElementById("progress-step");
const progressPercent = document.getElementById("progress-percent");
const progressFill = document.getElementById("progress-fill");
const progressDetail = document.getElementById("progress-detail");
const progressSlide = document.getElementById("progress-slide");

let currentRunId = "";

const STEP_LABELS = {
  queued: "排队中",
  prepare: "初始化",
  slide_count: "确定页数",
  outline: "生成大纲",
  style: "生成风格",
  image_generation: "生成图片",
  packaging: "打包普通 PPT",
  editable_prepare: "准备可编辑流程",
  editable_assets: "提取素材",
  editable_codegen: "生成可编辑代码",
  editable_render: "验证单页预览",
  editable_packaging: "合成可编辑 PPT",
  completed: "已完成",
  failed: "失败",
};

const EDITABLE_FIELD_NAMES = [
  "editable_base_url",
  "editable_api_key",
  "editable_model",
  "editable_prompt_file",
  "editable_browser_path",
  "editable_download_timeout_ms",
  "editable_max_tokens",
  "editable_max_attempts",
  "editable_sleep_seconds",
  "assets_dir",
  "asset_backend",
  "mineru_base_url",
  "mineru_api_key",
  "mineru_model_version",
  "mineru_language",
  "mineru_enable_formula",
  "mineru_enable_table",
  "mineru_is_ocr",
  "mineru_poll_interval_seconds",
  "mineru_timeout_seconds",
  "mineru_max_refine_depth",
  "force_reextract_assets",
  "disable_asset_reuse",
];

function setStatus(message, isError = false) {
  statusBox.textContent = message;
  statusBox.classList.remove("hidden");
  statusBox.classList.toggle("error", isError);
}

function clearResult() {
  currentRunId = "";
  deckTitle.textContent = "";
  stylePrompt.textContent = "";
  outlineList.innerHTML = "";
  slidesGrid.innerHTML = "";
  pptxWrap.classList.add("hidden");
  editableWrap.classList.add("hidden");
  editableMeta.textContent = "";
  convertBtn.classList.add("hidden");
  convertBtn.disabled = false;
  resultBox.classList.add("hidden");
}

function resetProgress() {
  progressCard.classList.remove("hidden");
  progressStep.textContent = "准备中...";
  progressPercent.textContent = "0%";
  progressFill.style.width = "0%";
  progressDetail.textContent = "等待开始...";
  progressSlide.textContent = "";
  progressSlide.classList.add("hidden");
}

function updateProgress(job) {
  const progress = Number(job.progress || 0);
  const step = job.step || "queued";
  progressStep.textContent = STEP_LABELS[step] || step;
  progressPercent.textContent = `${progress}%`;
  progressFill.style.width = `${Math.max(0, Math.min(100, progress))}%`;
  progressDetail.textContent = job.message || "处理中...";

  const current = Number(job.current_slide || 0);
  const total = Number(job.total_slides || 0);
  if (total > 0) {
    progressSlide.classList.remove("hidden");
    progressSlide.textContent = `页进度：${current}/${total}`;
  } else {
    progressSlide.classList.add("hidden");
    progressSlide.textContent = "";
  }
}

function renderEditableBlock(editableDeck) {
  if (!editableDeck || !editableDeck.pptx_url) {
    editableWrap.classList.add("hidden");
    editableMeta.textContent = "";
    return;
  }
  editableLink.href = editableDeck.pptx_url;
  editableMeta.textContent = `剩余 PH: ${editableDeck.total_remaining_ph_count}`;
  editableWrap.classList.remove("hidden");
}

function renderResult(data) {
  currentRunId = data.run_id || "";
  deckTitle.textContent = `文稿标题：${data.deck_title}（Run ID: ${data.run_id}）`;
  stylePrompt.textContent = data.style_prompt || "无";

  if (data.pptx_url) {
    pptxLink.href = data.pptx_url;
    pptxWrap.classList.remove("hidden");
  } else {
    pptxWrap.classList.add("hidden");
  }

  outlineList.innerHTML = "";
  data.outline.forEach((slide) => {
    const li = document.createElement("li");
    li.textContent = `${slide.page}. ${slide.title} | 要点：${slide.key_points.join("；")}`;
    outlineList.appendChild(li);
  });

  slidesGrid.innerHTML = "";
  data.slides.forEach((slide) => {
    const card = document.createElement("article");
    card.className = "slide-card";

    const img = document.createElement("img");
    img.src = slide.image_url;
    img.alt = `slide-${slide.page}`;
    img.loading = "lazy";

    const meta = document.createElement("div");
    meta.className = "meta";

    const title = document.createElement("h3");
    title.textContent = `第 ${slide.page} 页：${slide.title}`;

    const details = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = "查看本页 Prompt";
    const pre = document.createElement("pre");
    pre.textContent = slide.prompt;

    details.appendChild(summary);
    details.appendChild(pre);
    meta.appendChild(title);
    meta.appendChild(details);

    card.appendChild(img);
    card.appendChild(meta);
    slidesGrid.appendChild(card);
  });

  renderEditableBlock(data.editable_deck);
  if (!data.editable_deck && currentRunId) {
    convertBtn.classList.remove("hidden");
  } else {
    convertBtn.classList.add("hidden");
  }

  resultBox.classList.remove("hidden");
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function appendEditableOverrides(formData) {
  EDITABLE_FIELD_NAMES.forEach((name) => {
    const nodes = form.querySelectorAll(`[name="${name}"]`);
    if (!nodes.length) return;

    const first = nodes[0];
    if (first.type === "radio") {
      const checked = form.querySelector(`[name="${name}"]:checked`);
      if (checked && checked.value) {
        formData.set(name, checked.value);
      }
      return;
    }

    if (first.type === "checkbox") {
      const checked = Array.from(nodes).some((node) => node.checked);
      if (checked) {
        formData.set(name, "true");
      }
      return;
    }

    const value = (first.value || "").trim();
    if (value) {
      formData.set(name, value);
    }
  });
}

async function pollJob(jobId) {
  while (true) {
    const response = await fetch(`/api/generate/status/${jobId}`);
    if (!response.ok) {
      throw new Error(`查询进度失败：HTTP ${response.status}`);
    }
    const job = await response.json();
    updateProgress(job);

    if (job.state === "done") {
      return job.result;
    }
    if (job.state === "failed") {
      throw new Error(job.error || job.message || "任务失败");
    }

    await sleep(1000);
  }
}

async function startEditableConversion() {
  if (!currentRunId) {
    setStatus("当前没有可转换的 run_id。", true);
    return;
  }

  convertBtn.disabled = true;
  resetProgress();
  setStatus(`开始基于 ${currentRunId} 转换可编辑 PPT...`);

  const formData = new FormData();
  formData.set("run_id", currentRunId);
  appendEditableOverrides(formData);

  try {
    const response = await fetch("/api/editable/start", {
      method: "POST",
      body: formData,
    });
    if (!response.ok) {
      const errJson = await response.json().catch(() => ({}));
      throw new Error(errJson.detail || `任务启动失败：HTTP ${response.status}`);
    }

    const startData = await response.json();
    const result = await pollJob(startData.job_id);
    renderEditableBlock(result);
    convertBtn.classList.add("hidden");
    setStatus("可编辑 PPT 转换完成。");
  } catch (error) {
    setStatus(`可编辑 PPT 转换失败：${error.message}`, true);
  } finally {
    convertBtn.disabled = false;
  }
}

convertBtn.addEventListener("click", startEditableConversion);

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearResult();
  resetProgress();
  submitBtn.disabled = true;
  setStatus("任务已提交，正在排队执行...");

  const formData = new FormData(form);
  const slideCountRaw = (formData.get("slide_count") || "").toString().trim();
  formData.set("slide_count", slideCountRaw || "auto");

  const styleDescRaw = (formData.get("style_description") || "").toString().trim();
  const styleFile = formData.get("style_template");
  const hasStyleDesc = Boolean(styleDescRaw);
  const hasStyleFile = styleFile instanceof File && styleFile.size > 0;
  if (hasStyleDesc && hasStyleFile) {
    setStatus("风格描述与风格模板图互斥，请二选一。", true);
    submitBtn.disabled = false;
    return;
  }

  const optionalKeys = [
    "base_url",
    "image_api_url",
    "text_api_key",
    "image_api_key",
    "text_model",
    "image_model",
    "style_description",
    ...EDITABLE_FIELD_NAMES,
  ];
  optionalKeys.forEach((key) => {
    const value = formData.get(key);
    if (typeof value === "string" && !value.trim()) {
      formData.delete(key);
    }
  });

  try {
    const response = await fetch("/api/generate/start", {
      method: "POST",
      body: formData,
    });
    if (!response.ok) {
      const errJson = await response.json().catch(() => ({}));
      throw new Error(errJson.detail || `任务启动失败：HTTP ${response.status}`);
    }

    const startData = await response.json();
    if (!startData.job_id) {
      throw new Error("任务启动失败：缺少 job_id");
    }

    setStatus(`任务已启动（job: ${startData.job_id}），正在执行...`);
    const result = await pollJob(startData.job_id);
    renderResult(result);
    setStatus(`生成完成，共 ${result.slides.length} 页。`);
  } catch (error) {
    setStatus(`生成失败：${error.message}`, true);
  } finally {
    submitBtn.disabled = false;
  }
});
