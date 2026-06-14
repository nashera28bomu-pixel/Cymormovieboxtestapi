import express from "express";
import { MovieboxSession, searchMovies, getMovieDetail, downloadMovie } from "moviebox-js-sdk";

const app = express();
const PORT = process.env.PORT || 3000;

// Create a session pointing at the aoneroom mirror.
// MOVIEBOX_API_HOST can be overridden via Render env vars if this host gets blocked.
const session = new MovieboxSession({
  host: process.env.MOVIEBOX_API_HOST || "h5.aoneroom.com",
  mirrorHosts: ["h5.aoneroom.com", "movieboxapp.in"],
  proxyUrl: process.env.MOVIEBOX_API_PROXY || undefined
});

app.get("/", (req, res) => {
  res.json({
    status: "ok",
    message: "Cymor MovieBox test API is running",
    routes: ["/health", "/search?q=avatar", "/detail?path=<detailPath>"]
  });
});

app.get("/health", (req, res) => {
  res.json({ status: "healthy", time: new Date().toISOString() });
});

// Test 1: search for a movie
app.get("/search", async (req, res) => {
  const query = req.query.q || "Avatar";
  try {
    const results = await searchMovies(session, { keyword: query, page: 1 });
    res.json({ ok: true, query, results });
  } catch (err) {
    res.status(500).json({
      ok: false,
      step: "search",
      error: err.message,
      stack: err.stack
    });
  }
});

// Test 2: get details + stream links for a known item
// detailPath comes from a /search result, e.g. "titanic-m7a9yt0abq6"
app.get("/detail", async (req, res) => {
  const detailPath = req.query.path;
  if (!detailPath) {
    return res.status(400).json({ ok: false, error: "Pass ?path=<detailPath> from a /search result" });
  }
  try {
    const detail = await getMovieDetail(session, { detailPath });
    res.json({ ok: true, detailPath, detail });
  } catch (err) {
    res.status(500).json({
      ok: false,
      step: "detail",
      error: err.message,
      stack: err.stack
    });
  }
});

app.listen(PORT, () => {
  console.log(`Cymor MovieBox test API listening on port ${PORT}`);
});
