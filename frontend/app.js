// Studio: idea -> image -> approve -> 3D, upload -> 3D, gallery.
const STYLE_LABELS = { realistic: "Realistic", stylized: "Game asset", cartoon: "Cartoon", lowpoly: "Low-poly", clay: "Clay", figurine: "Figurine" };
const RES_LABEL = { "512": "Fast", "1024": "Balanced", "1536": "Highest" };
const S = { flow: "idea", imageGen: null, uploadFile: null, busy: false, galKind: "", galOffset: 0 };

function show(id) { for (const v of $$(".view")) v.hidden = v.id !== id; }
function currentRes() { return $('input[name="res"]:checked').value; }
function modelCost() { return C3D.config.cost_model[currentRes()]; }
function updateCosts() {
  const c = C3D.config;
  $("#cost-image").textContent = `${c.cost_image} credit`;
  $("#cost-retry").textContent = `${c.cost_image} credit`;
  $$(".cost-model").forEach((el) => (el.textContent = `${modelCost()} credits`));
}
function setStep(name, cls) {
  const order = ["describe", "approve", "build"], cur = order.indexOf(name);
  for (const li of $$("#flow-idea .step")) {
    const i = order.indexOf(li.dataset.step);
    li.classList.remove("active", "waiting", "done");
    if (i < cur) li.classList.add("done"); else if (i === cur) li.classList.add(cls || "active");
  }
}
function setBusy(b) {
  S.busy = b;
  $("#gen-image-btn").disabled = b; $("#approve-btn").disabled = b; $("#retry-btn").disabled = b;
  $("#upload-build-btn").disabled = b || !S.uploadFile;
}
function settings() {
  const seed = $("#seed").value;
  return { resolution: currentRes(), decimation_target: +$("#dec").value, texture_size: +$("#tex").value,
           seed: seed === "" ? null : +seed };
}
async function refreshCredits() { try { setCredits((await api("/api/auth/me")).credits); } catch {} }

function handleSpendError(e) {
  if (e.status === 402) {
    toast(e.message);
    setTimeout(() => { if (confirm(`${e.message}\n\nGo to pricing?`)) location.href = "/pricing"; }, 50);
  } else toast(e.message);
}

async function poll(gen, onTick) {
  while (gen.status === "queued" || gen.status === "running") {
    onTick?.(gen);
    await new Promise((r) => setTimeout(r, 1500));
    gen = await api(`/api/generations/${gen.id}`);
  }
  onTick?.(gen);
  refreshCredits();
  if (gen.status === "failed") { const e = new Error(gen.error || "Generation failed."); e.gen = gen; throw e; }
  return gen;
}
function tick(gen) {
  $("#build-stage").textContent = gen.stage;
  $("#build-bar").style.width = `${gen.progress}%`;
  const secs = Math.round((Date.now() - new Date(gen.created_at).getTime()) / 1000);
  $("#build-elapsed").textContent = secs > 0 ? `${secs} s` : "";
}

// ---------------- tabs
function selectFlow(flow) {
  S.flow = flow;
  $("#tab-idea").setAttribute("aria-selected", flow === "idea");
  $("#tab-upload").setAttribute("aria-selected", flow === "upload");
  $("#flow-idea").hidden = flow !== "idea"; $("#flow-upload").hidden = flow !== "upload";
}
$("#tab-idea").onclick = () => selectFlow("idea");
$("#tab-upload").onclick = () => selectFlow("upload");

// ---------------- idea -> image
async function generateImage() {
  const idea = $("#idea").value.trim();
  if (idea.length < 2) { $("#idea").focus(); return; }
  setBusy(true); setStep("describe"); $("#approve-actions").hidden = true; showCreate();
  show("view-build"); $("#build-img").removeAttribute("src"); $("#build-stage").textContent = "Starting"; $("#build-bar").style.width = "0";
  try {
    const gen = await api("/api/generations/image", { method: "POST", json: {
      idea, style: $('input[name="style"]:checked').value, optimize_for_3d: $("#opt3d").checked } });
    refreshCredits();
    showImageForApproval(await poll(gen, tick));
  } catch (e) {
    handleSpendError(e); show(S.imageGen ? "view-image" : "view-empty"); setStep(S.imageGen ? "approve" : "describe", S.imageGen ? "waiting" : undefined);
    if (S.imageGen) $("#approve-actions").hidden = false;
  } finally { setBusy(false); }
}
function showImageForApproval(gen) {
  S.imageGen = gen;
  $("#stage-img").src = gen.output.image_url;
  $("#img-caption").textContent = `“${gen.prompt}” · seed ${gen.output.seed ?? "random"}`;
  show("view-image"); selectFlow("idea");
  setStep("approve", "waiting");
  $("#approve-hint").textContent = "Happy with it? Approve to build the 3D model, or try another variation.";
  $("#approve-actions").hidden = false; $("#approve-btn").focus();
}
$("#idea-form").addEventListener("submit", (e) => { e.preventDefault(); generateImage(); });
$("#retry-btn").onclick = generateImage;
$("#approve-btn").onclick = () => {
  setStep("build"); $("#approve-actions").hidden = true;
  buildModel(() => api("/api/generations/model", { method: "POST", json: { image_generation_id: S.imageGen.id, ...settings() } }),
             $("#stage-img").src);
};

// ---------------- upload
const drop = $("#drop");
["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => e.dataTransfer.files[0] && pickFile(e.dataTransfer.files[0]));
$("#file").addEventListener("change", (e) => e.target.files[0] && pickFile(e.target.files[0]));
function pickFile(file) {
  if (!/^image\/(png|jpeg|webp)$/.test(file.type)) { toast("Choose a PNG, JPG or WEBP image."); return; }
  if (file.size > 15 * 1024 * 1024) { toast("Images must be under 15 MB."); return; }
  S.uploadFile = file;
  const url = URL.createObjectURL(file);
  drop.querySelector("img")?.remove();
  const img = document.createElement("img"); img.src = url; img.alt = ""; drop.prepend(img);
  $("strong", drop).textContent = file.name; $("span", drop).textContent = "Click to choose a different image.";
  $("#stage-img").src = url; $("#img-caption").textContent = file.name; showCreate(); show("view-image");
  setBusy(false);
}
$("#upload-build-btn").onclick = () => {
  const fd = new FormData(); fd.append("file", S.uploadFile);
  for (const [k, v] of Object.entries(settings())) if (v !== null) fd.append(k, v);
  buildModel(() => api("/api/generations/model/upload", { method: "POST", body: fd }), $("#stage-img").src);
};

// ---------------- image -> 3D
async function buildModel(start, previewSrc) {
  if (S.busy) return;
  setBusy(true); showCreate();
  $("#build-img").src = previewSrc; $("#build-stage").textContent = "Starting"; $("#build-bar").style.width = "0";
  show("view-build");
  try {
    const gen = await start();
    refreshCredits();
    showModel(await poll(gen, tick));
    if (S.flow === "idea") setStep("build", "done");
  } catch (e) {
    handleSpendError(e); show("view-image");
    if (S.flow === "idea" && S.imageGen) { setStep("approve", "waiting"); $("#approve-actions").hidden = false; }
  } finally { setBusy(false); }
}
function showModel(gen) {
  $("#viewer").src = gen.output.glb_url;
  $("#download").href = gen.output.download_url;
  const p = gen.params || {};
  $("#model-meta").textContent = `${gen.output.size_mb} MB · ${RES_LABEL[p.resolution] || p.resolution} detail · ${Math.round((p.decimation_target || 0) / 1000)}k polygons · ${gen.cost_credits} credits`;
  showCreate(); show("view-model");
}
$("#new-btn").onclick = () => {
  show("view-empty"); setStep("describe"); S.imageGen = null; $("#approve-actions").hidden = true;
  $("#approve-hint").textContent = "Your image appears on the stage. Approve it to build the 3D model, or try another.";
  (S.flow === "idea" ? $("#idea") : $("#drop")).focus();
};

// ---------------- gallery
function showCreate() { $("#view-create").setAttribute("aria-pressed", "true"); $("#view-gallery").setAttribute("aria-pressed", "false"); }
$("#view-create").onclick = () => { showCreate(); show(S.lastView || "view-empty"); };
$("#view-gallery").onclick = () => {
  S.lastView = $$(".view").find((v) => !v.hidden && v.id !== "view-gallery-panel")?.id || "view-empty";
  $("#view-create").setAttribute("aria-pressed", "false"); $("#view-gallery").setAttribute("aria-pressed", "true");
  show("view-gallery-panel"); loadGallery(true);
};
$$("#gal-filter button").forEach((b) => (b.onclick = () => {
  $$("#gal-filter button").forEach((x) => x.setAttribute("aria-pressed", x === b));
  S.galKind = b.dataset.k; loadGallery(true);
}));
$("#gal-more").onclick = () => loadGallery(false);

async function loadGallery(reset) {
  if (reset) { S.galOffset = 0; $("#gal-grid").innerHTML = ""; }
  const r = await api(`/api/generations?limit=24&offset=${S.galOffset}${S.galKind ? `&kind=${S.galKind}` : ""}`);
  S.galOffset += r.items.length;
  if (!r.total) $("#gal-grid").innerHTML = `<p class="empty-note">Nothing here yet. Your images and models will appear here.</p>`;
  for (const g of r.items) $("#gal-grid").append(tile(g));
  $("#gal-more").hidden = S.galOffset >= r.total;
}
function tile(g) {
  const b = document.createElement("button"); b.className = "tile"; b.type = "button";
  const thumb = g.kind === "image" ? g.output?.image_url : g.input_image_url;
  const status = g.status === "done" ? "" : g.status === "failed" ? '<span class="badge bad">Failed</span>' : '<span class="badge wait">Working</span>';
  const label = g.kind === "image" ? (g.prompt || "Image") : `3D · ${RES_LABEL[g.params?.resolution] || ""}`;
  b.innerHTML = `<div class="thumb" style="${thumb ? `background-image:url('${esc(thumb)}')` : ""}">
      <span class="kind badge ${g.kind === "model" ? "ok" : ""}">${g.kind === "model" ? "3D" : "Image"}</span></div>
    <div class="meta"><span>${esc(label)}</span><span>${status || new Date(g.created_at).toLocaleDateString()}</span></div>`;
  b.onclick = () => openGen(g);
  return b;
}
async function openGen(g) {
  g = await api(`/api/generations/${g.id}`);
  if (g.status === "failed") { toast(g.error || "This one failed; its credits were refunded."); return; }
  if (g.status !== "done") { resume(g); return; }
  if (g.kind === "model") showModel(g); else showImageForApproval(g);
  showCreate();
}
async function resume(g) {
  setBusy(true); showCreate(); show("view-build");
  $("#build-img").src = g.kind === "model" ? (g.input_image_url || "") : "";
  try {
    const done = await poll(g, tick);
    if (done.kind === "model") showModel(done); else showImageForApproval(done);
  } catch (e) { toast(e.message); show("view-empty"); } finally { setBusy(false); }
}

// ---------------- init
$("#dec").addEventListener("input", (e) => ($("#dec-out").textContent = `${Math.round(e.target.value / 1000)}k`));
$$('input[name="res"]').forEach((r) => r.addEventListener("change", updateCosts));
(async () => {
  try { await boot({ active: "/app", requireAuth: true }); } catch { return; }
  setStep("describe");
  $("#style-chips").innerHTML = C3D.config.styles.map((s, i) =>
    `<label><input type="radio" name="style" value="${s}" ${i === 0 ? "checked" : ""}><span>${STYLE_LABELS[s] || s}</span></label>`).join("");
  updateCosts();
  const open = new URLSearchParams(location.search).get("open");
  if (open) { openGen({ id: open }).catch(() => {}); return; }
  // Pick up anything still running from a previous visit.
  const running = (await api("/api/generations?limit=1&status=running")).items[0] || (await api("/api/generations?limit=1&status=queued")).items[0];
  if (running) resume(running);
})();
