const form = document.getElementById("generate-form");
const statusBox = document.getElementById("status");
const resultBox = document.getElementById("result");
const submitBtn = document.getElementById("submit-btn");
const deckTitle = document.getElementById("deck-title");
const stylePrompt = document.getElementById("style-prompt");
const outlineList = document.getElementById("outline-list");
const slidesGrid = document.getElementById("slides-grid");
const pptxWrap = document.getElementById("pptx-wrap");
const pptxLink = document.getElementById("pptx-link");

const progressCard = document.getElementById("progress-card");
const progressStep = document.getElementById("progress-step");
const progressPercent = document.getElementById("progress-percent");
const progressFill = document.getElementById("progress-fill");
const progressDetail = document.getElementById("progress-detail");
const progressSlide = document.getElementById("progress-slide");

const STEP_LABELS = {
  queued: "排队中",
  prepare: "初始化模型",
  slide_count: "确定页数",
  outline: "生成大纲",
  style: "生成风格",
  image_generation: "生成图片",
  packaging: "打包PPT",
  completed: "已完成",
  failed: "失败",
};

function setStatus(message, isError = false) {
  statusBox.textContent = message;
  statusBox.classList.remove("hidden");
  statusBox.classList.toggle("error", isError);
}

function clearResult() {
  deckTitle.textContent = "";
  stylePrompt.textContent = "";
  pptxWrap.classList.add("hidden");
  pptxLink.removeAttribute("href");
  outlineList.innerHTML = "";
  slidesGrid.innerHTML = "";
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
  const p = Number(job.progress || 0);
  const step = job.step || "queued";
  const label = STEP_LABELS[step] || step;

  progressStep.textContent = label;
  progressPercent.textContent = `${p}%`;
  progressFill.style.width = `${Math.max(0, Math.min(100, p))}%`;
  progressDetail.textContent = job.message || "处理中...";

  const current = Number(job.current_slide || 0);
  const total = Number(job.total_slides || 0);
  if (total > 0) {
    progressSlide.classList.remove("hidden");
    progressSlide.textContent = `图片进度：${current}/${total}`;
  } else {
    progressSlide.classList.add("hidden");
    progressSlide.textContent = "";
  }
}

function renderResult(data) {
  deckTitle.textContent = `文稿标题：${data.deck_title}（Run ID: ${data.run_id}）`;
  stylePrompt.textContent = data.style_prompt || "无";
  if (data.pptx_url) {
    pptxLink.href = data.pptx_url;
    pptxWrap.classList.remove("hidden");
  } else {
    pptxWrap.classList.add("hidden");
  }

  data.outline.forEach((slide) => {
    const li = document.createElement("li");
    li.textContent = `${slide.page}. ${slide.title} | 要点：${slide.key_points.join("；")}`;
    outlineList.appendChild(li);
  });

  data.slides.forEach((slide) => {
    const card = document.createElement("article");
    card.className = "slide-card";

    const img = document.createElement("img");
    img.src = slide.image_url;
    img.alt = `slide-${slide.page}`;
    img.loading = "lazy";

    const meta = document.createElement("div");
    meta.className = "meta";

    const h3 = document.createElement("h3");
    h3.textContent = `第 ${slide.page} 页：${slide.title}`;

    const details = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = "查看本页生成 Prompt";
    const pre = document.createElement("pre");
    pre.textContent = slide.prompt;

    details.appendChild(summary);
    details.appendChild(pre);
    meta.appendChild(h3);
    meta.appendChild(details);

    card.appendChild(img);
    card.appendChild(meta);
    slidesGrid.appendChild(card);
  });

  resultBox.classList.remove("hidden");
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function pollJob(jobId) {
  while (true) {
    const resp = await fetch(`/api/generate/status/${jobId}`);
    if (!resp.ok) {
      throw new Error(`查询进度失败：HTTP ${resp.status}`);
    }

    const job = await resp.json();
    updateProgress(job);

    if (job.state === "done") {
      return job.result;
    }
    if (job.state === "failed") {
      throw new Error(job.error || job.message || "生成失败");
    }

    await sleep(1000);
  }
}

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
  const hasStyleDesc = !!styleDescRaw;
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
  ];
  for (const key of optionalKeys) {
    const value = formData.get(key);
    if (typeof value === "string" && !value.trim()) {
      formData.delete(key);
    }
  }

  try {
    const startResp = await fetch("/api/generate/start", {
      method: "POST",
      body: formData,
    });
    if (!startResp.ok) {
      const errJson = await startResp.json().catch(() => ({}));
      throw new Error(errJson.detail || `任务启动失败：HTTP ${startResp.status}`);
    }

    const startData = await startResp.json();
    const jobId = startData.job_id;
    if (!jobId) {
      throw new Error("任务启动失败：缺少 job_id");
    }

    setStatus(`任务已启动（job: ${jobId}），正在执行...`);
    const result = await pollJob(jobId);

    renderResult(result);
    setStatus(`生成完成，共 ${result.slides.length} 页。`);
  } catch (err) {
    setStatus(`生成失败：${err.message}`, true);
  } finally {
    submitBtn.disabled = false;
  }
});
