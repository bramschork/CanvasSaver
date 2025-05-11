// server.js
import express from "express";
import fetch from "node-fetch";
import archiver from "archiver";
import dotenv from "dotenv";
import path from "path";
import { fileURLToPath } from "url";

dotenv.config();

const DEBUG = true;
const THRESHOLD_COURSES = 50;

// ────── CONFIG ────────────────────────────────────────────────────────────────
const CANVAS_URL = process.env.CANVAS_URL;
const CANVAS_TOKEN = process.env.CANVAS_TOKEN || process.env.CANVAS_API_TOKEN;
if (!CANVAS_URL || !CANVAS_TOKEN) {
  console.error(
    "❌  Missing CANVAS_URL or CANVAS_TOKEN in your .env\n" +
      "    Make sure your .env contains:\n" +
      "    CANVAS_URL=https://caltech.instructure.com\n" +
      "    CANVAS_TOKEN=<your-personal-access-token>"
  );
  process.exit(1);
}

const app = express();
const PORT = process.env.PORT || 3000;
const __dirname = path.dirname(fileURLToPath(import.meta.url));

// ────── MIDDLEWARE ────────────────────────────────────────────────────────────
app.use(express.json());
app.use(express.static(path.join(__dirname, "public")));

// ────── SSE PROGRESS CHANNEL ──────────────────────────────────────────────────
const sseClients = new Map();
app.get("/download/events", (req, res) => {
  const { downloadId } = req.query;
  if (!downloadId) return res.status(400).end("Missing downloadId");
  res.writeHead(200, {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache, no-transform",
    Connection: "keep-alive",
  });
  res.write("\n");
  sseClients.set(downloadId, res);
  req.on("close", () => sseClients.delete(downloadId));
});

function sendProgress(id, done, total, message) {
  const client = sseClients.get(id);
  if (!client) return;
  client.write(`event: progress\n`);
  client.write(`data: ${JSON.stringify({ done, total, message })}\n\n`);
}

// ────── LINK-HEADER PAGINATION PARSER ─────────────────────────────────────────
function getNextPage(linkHeader) {
  if (!linkHeader) return null;
  for (const part of linkHeader.split(",")) {
    const [urlPart, relPart] = part.trim().split(";");
    if (/rel="next"/.test(relPart)) {
      return urlPart.trim().slice(1, -1);
    }
  }
  return null;
}

/**
 * Fetch all your courses (active + completed + inactive) by first listing enrollments,
 * then fetching each course’s metadata, de-duplicating, and returning all of them.
 */
async function fetchAllCourses() {
  if (DEBUG) console.log("=== fetchAllCourses starting ===");
  const states = ["active", "completed", "inactive"];
  if (DEBUG) console.log("🔍 Querying enrollment states:", states);

  // 1) List your enrollments
  const enrollUrl = new URL(`${CANVAS_URL}/api/v1/users/self/enrollments`);
  enrollUrl.searchParams.set("per_page", "100");
  states.forEach((state) => enrollUrl.searchParams.append("state[]", state));
  enrollUrl.searchParams.append("type[]", "StudentEnrollment");

  let next = enrollUrl.toString();
  const enrollments = [];
  while (next) {
    const resp = await fetch(next, {
      headers: { Authorization: `Bearer ${CANVAS_TOKEN}` },
    });
    if (!resp.ok) {
      throw new Error(`Enrollment fetch failed: ${resp.status}`);
    }
    const batch = await resp.json();
    if (DEBUG) {
      console.log(
        `📥 Got batch of ${batch.length} enrollments. Total so far: ${
          enrollments.length + batch.length
        }`
      );
    }
    enrollments.push(...batch);
    const link = resp.headers.get("Link") || resp.headers.get("link");
    next = getNextPage(link);
  }

  // 2) De-duplicate to unique course IDs
  const uniqueIds = [...new Set(enrollments.map((e) => e.course_id))];
  if (DEBUG)
    console.log(`🆔 Unique course IDs count: ${uniqueIds.length}`, uniqueIds);

  // 3) Fetch each course’s metadata (including term)
  const courses = [];
  for (const id of uniqueIds) {
    try {
      const cu = new URL(`${CANVAS_URL}/api/v1/courses/${id}`);
      cu.searchParams.append("include[]", "term");
      const cr = await fetch(cu.toString(), {
        headers: { Authorization: `Bearer ${CANVAS_TOKEN}` },
      });
      if (!cr.ok) {
        if (cr.status === 403 || cr.status === 404) continue;
        throw new Error(`Course ${id} metadata fetch failed: ${cr.status}`);
      }
      const c = await cr.json();
      courses.push({
        id: c.id,
        name: c.name,
        course_code: c.course_code,
        term: c.term || {},
        published: c.published,
      });
    } catch (e) {
      console.warn(e);
    }
  }
  if (DEBUG) console.log(`📚 Fetched metadata for ${courses.length} courses`);
  if (courses.length < THRESHOLD_COURSES) {
    console.warn(
      `⚠️ Only ${courses.length} courses found (<${THRESHOLD_COURSES}).`
    );
  }

  // 4) Return all fetched courses (including unpublished)
  if (DEBUG)
    console.log(
      `✅ Returning ${courses.length} courses (including unpublished)`
    );
  return courses;
}

// ────── LIST COURSES ENDPOINT ─────────────────────────────────────────────────
app.get("/api/courses", async (_, res) => {
  try {
    const all = await fetchAllCourses();
    res.json(all);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ────── DOWNLOAD ZIP ENDPOINT ─────────────────────────────────────────────────
app.post("/download", async (req, res) => {
  const { downloadId } = req.query;
  const { selections } = req.body;
  if (!downloadId || !sseClients.has(downloadId)) {
    return res.status(400).json({ error: "Invalid downloadId" });
  }

  // Set headers for zip download
  res.setHeader("Content-Type", "application/zip");
  res.setHeader(
    "Content-Disposition",
    'attachment; filename="canvas_backup.zip"'
  );
  const archive = archiver("zip");
  archive.on("error", (err) => res.destroy(err));
  archive.pipe(res);

  // total steps for progress indication
  const total =
    selections.modules.length +
    selections.syllabus.length +
    selections.pages.length +
    selections.submissions.length;
  let done = 0;

  // ─ Modules ────────────────────────────────────────────────────────────────
  for (const courseId of selections.modules) {
    const modsR = await fetch(
      `${CANVAS_URL}/api/v1/courses/${courseId}/modules?per_page=100&include[]=items`,
      { headers: { Authorization: `Bearer ${CANVAS_TOKEN}` } }
    );
    const modules = await modsR.json();
    for (const m of modules) {
      const safeMod = m.name.replace(/[\/\\]/g, "_");
      for (const item of m.items) {
        if (item.type === "File" && item.content_id) {
          const meta = await (
            await fetch(`${CANVAS_URL}/api/v1/files/${item.content_id}`, {
              headers: { Authorization: `Bearer ${CANVAS_TOKEN}` },
            })
          ).json();
          const stream = await fetch(meta.url).then((r) => r.body);
          archive.append(stream, {
            name: `${courseId}/Modules/${safeMod}/${meta.display_name}`,
          });
        }
      }
    }
    done++;
    sendProgress(downloadId, done, total, `Modules for ${courseId}`);
  }

  // ─ Syllabus ───────────────────────────────────────────────────────────────
  for (const courseId of selections.syllabus) {
    const attR = await fetch(
      `${CANVAS_URL}/api/v1/courses/${courseId}/pages/syllabus/attachments`,
      { headers: { Authorization: `Bearer ${CANVAS_TOKEN}` } }
    );
    const atts = await attR.json();
    for (const a of atts) {
      const st = await fetch(a.url).then((r) => r.body);
      archive.append(st, { name: `${courseId}/Syllabus/${a.filename}` });
    }
    done++;
    sendProgress(downloadId, done, total, `Syllabus for ${courseId}`);
  }

  // ─ Pages ────────────────────────────────────────────────────────────────
  for (const courseId of selections.pages) {
    const pagesR = await fetch(
      `${CANVAS_URL}/api/v1/courses/${courseId}/pages?per_page=100`,
      { headers: { Authorization: `Bearer ${CANVAS_TOKEN}` } }
    );
    const pages = await pagesR.json();
    for (const p of pages) {
      const safeTitle = p.title.replace(/[\/\\]/g, "_");
      const attR = await fetch(
        `${CANVAS_URL}/api/v1/courses/${courseId}/pages/${p.url}/attachments`,
        { headers: { Authorization: `Bearer ${CANVAS_TOKEN}` } }
      );
      const atts = await attR.json();
      for (const a of atts) {
        const st = await fetch(a.url).then((r) => r.body);
        archive.append(st, {
          name: `${courseId}/Pages/${safeTitle}/${a.filename}`,
        });
      }
    }
    done++;
    sendProgress(downloadId, done, total, `Pages for ${courseId}`);
  }

  // ─ Submissions ─────────────────────────────────────────────────────────
  for (const courseId of selections.submissions) {
    const asgR = await fetch(
      `${CANVAS_URL}/api/v1/courses/${courseId}/assignments?per_page=100`,
      { headers: { Authorization: `Bearer ${CANVAS_TOKEN}` } }
    );
    const asgs = await asgR.json();
    for (const a of asgs) {
      const subR = await fetch(
        `${CANVAS_URL}/api/v1/courses/${courseId}/assignments/${a.id}/submissions?student_ids[]=self&include[]=submission_comments`,
        { headers: { Authorization: `Bearer ${CANVAS_TOKEN}` } }
      );
      const subs = await subR.json();
      if (subs[0]?.attachments) {
        for (const att of subs[0].attachments) {
          const st = await fetch(att.url).then((r) => r.body);
          archive.append(st, {
            name: `${courseId}/Submissions/${a.name}/${att.filename}`,
          });
        }
      }
    }
    done++;
    sendProgress(downloadId, done, total, `Submissions for ${courseId}`);
  }

  // finalize & close streams
  archive.finalize();
  archive.on("end", () => {
    const client = sseClients.get(downloadId);
    if (client) {
      client.write(`event: done\ndata:{}\n\n`);
      client.end();
      sseClients.delete(downloadId);
    }
  });
});

// ────── START SERVER ─────────────────────────────────────────────────────────
app.listen(PORT, () => {
  console.log(`🚀 Server running on http://localhost:${PORT}`);
});
