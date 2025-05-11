const urlInput = document.getElementById("canvas-url");
const tokenInput = document.getElementById("api-token");
const filterRadios = document.querySelectorAll('input[name="filter"]');
const termSelect = document.getElementById("term-select");
const loadBtn = document.getElementById("load-btn");
const downloadBtn = document.getElementById("download-btn");
const cancelBtn = document.getElementById("cancel-btn");
const statusP = document.getElementById("status");
const progressP = document.getElementById("progress");
const table = document.getElementById("courses-table");
const tbody = table.querySelector("tbody");

let allCourses = [];
let controller, evtSource, downloadId;

// show/hide term-select based on filter choice
filterRadios.forEach((r) => {
  r.addEventListener("change", () => {
    termSelect.hidden = r.value !== "specific";
  });
});

loadBtn.onclick = async () => {
  const canvasURL = urlInput.value.trim();
  const apiToken = tokenInput.value.trim();
  if (!canvasURL || !apiToken) {
    alert("Please fill in both Canvas URL and API token.");
    return;
  }

  statusP.textContent = "Fetching courses…";
  progressP.textContent = "";
  tbody.innerHTML = "";
  table.hidden = true;
  downloadBtn.disabled = true;
  cancelBtn.disabled = true;

  try {
    const u = new URL("/api/courses", location.origin);
    u.searchParams.set("canvasURL", canvasURL);
    u.searchParams.set("apiToken", apiToken);
    const res = await fetch(u);
    if (!res.ok) throw new Error(await res.text());
    allCourses = await res.json();

    // build term options
    const terms = Array.from(
      new Set(allCourses.map((c) => c.term.name).filter((n) => n))
    ).sort();
    termSelect.innerHTML = "";
    terms.forEach((t) => {
      const opt = document.createElement("option");
      opt.value = t;
      opt.textContent = t;
      termSelect.appendChild(opt);
    });

    renderCourses();
    table.hidden = false;
    downloadBtn.disabled = false;
    statusP.textContent = `Found ${allCourses.length} courses.`;
  } catch (err) {
    statusP.textContent = "Error: " + err.message;
  }
};

function renderCourses() {
  const filter = document.querySelector('input[name="filter"]:checked').value;
  const now = new Date();
  let filtered = allCourses.slice();

  if (filter === "current") {
    filtered = filtered.filter((c) => {
      if (!c.term.start_at || !c.term.end_at) return false;
      const start = new Date(c.term.start_at);
      const end = new Date(c.term.end_at);
      return start <= now && now <= end;
    });
  } else if (filter === "specific") {
    const chosen = Array.from(termSelect.selectedOptions).map((o) => o.value);
    filtered = filtered.filter((c) => chosen.includes(c.term.name));
  }

  tbody.innerHTML = "";
  for (const c of filtered) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${c.name} (${c.course_code})<br><small>${c.term.name}</small></td>
      <td><input type="checkbox" data-type="modules"    data-id="${c.id}"></td>
      <td><input type="checkbox" data-type="syllabus"   data-id="${c.id}"></td>
      <td><input type="checkbox" data-type="pages"      data-id="${c.id}"></td>
      <td><input type="checkbox" data-type="submissions"data-id="${c.id}"></td>
    `;
    tbody.appendChild(tr);
  }
}

downloadBtn.onclick = () => {
  // gather selections
  const selections = { modules: [], syllabus: [], pages: [], submissions: [] };
  tbody.querySelectorAll("input[type=checkbox]").forEach((cb) => {
    if (cb.checked) selections[cb.dataset.type].push(cb.dataset.id);
  });
  if (
    !selections.modules.length &&
    !selections.syllabus.length &&
    !selections.pages.length &&
    !selections.submissions.length
  ) {
    alert("Select at least one checkbox.");
    return;
  }

  downloadBtn.disabled = true;
  cancelBtn.disabled = false;
  statusP.textContent = "Preparing download…";
  progressP.textContent = "";

  controller = new AbortController();
  downloadId = Math.random().toString(36).slice(2);

  // SSE progress
  evtSource = new EventSource(`/download/events?downloadId=${downloadId}`);
  evtSource.addEventListener("progress", (e) => {
    const { done, total, message } = JSON.parse(e.data);
    progressP.textContent = `${message} (${done}/${total})`;
  });
  evtSource.addEventListener("done", () => {
    progressP.textContent = "All done!";
    evtSource.close();
  });

  // POST to /download with query params
  const canvasURL = urlInput.value.trim();
  const apiToken = tokenInput.value.trim();
  fetch(
    `/download?downloadId=${downloadId}&canvasURL=${encodeURIComponent(
      canvasURL
    )}&apiToken=${encodeURIComponent(apiToken)}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ selections }),
      signal: controller.signal,
    }
  )
    .then((r) => {
      if (!r.ok) throw new Error(r.statusText);
      return r.blob();
    })
    .then((blob) => {
      const href = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = href;
      a.download = "canvas_backup.zip";
      a.click();
      URL.revokeObjectURL(href);
      statusP.textContent = "Download ready!";
    })
    .catch((err) => {
      if (err.name === "AbortError") {
        statusP.textContent = "Download canceled.";
      } else {
        statusP.textContent = "Error: " + err.message;
      }
    })
    .finally(() => {
      downloadBtn.disabled = false;
      cancelBtn.disabled = true;
      evtSource?.close();
    });
};

cancelBtn.onclick = () => {
  controller?.abort();
};
