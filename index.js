import express from "express";
import { MovieboxSession, search, getMovieDetails, getMovieStreamUrl } from "moviebox-js-sdk";

const app = express();
const PORT = process.env.PORT || 3000;

const EZVID_BASE = "https://ezvidapi.com";

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
    routes: [
      "/health",
      "/search?q=avatar",
      "/detail?path=<detailPath>",
      "/stream?path=<detailPath>",
      "/ezvid/list",
      "/ezvid/movie?tmdb=27205&provider=vidsrc",
      "/ezvid/tv?tmdb=1399&provider=vidsrc&season=1&episode=1"
    ]
  });
});

app.get("/health", (req, res) => {
  res.json({ status: "healthy", time: new Date().toISOString() });
});

// Test 1: search for a movie
app.get("/search", async (req, res) => {
  const query = req.query.q || "Avatar";
  try {
    const results = await search(session, { query });
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

// Test 2: get details for a known item
// detailPath comes from a /search result, e.g. "titanic-m7a9yt0abq6"
app.get("/detail", async (req, res) => {
  const detailPath = req.query.path;
  if (!detailPath) {
    return res.status(400).json({ ok: false, error: "Pass ?path=<detailPath> from a /search result" });
  }
  try {
    const detail = await getMovieDetails(session, { detailPath });
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

// Test 3: get direct stream URL for a known item
app.get("/stream", async (req, res) => {
  const detailPath = req.query.path;
  const quality = req.query.quality || "best";
  if (!detailPath) {
    return res.status(400).json({ ok: false, error: "Pass ?path=<detailPath> from a /search result" });
  }
  try {
    const stream = await getMovieStreamUrl(session, { detailPath, quality });
    res.json({ ok: true, detailPath, quality, stream });
  } catch (err) {
    res.status(500).json({
      ok: false,
      step: "stream",
      error: err.message,
      stack: err.stack
    });
  }
});

// --- ezvidapi.com integration ---
// List all available providers
app.get("/ezvid/list", async (req, res) => {
  try {
    const r = await fetch(`${EZVID_BASE}/list`);
    const data = await r.json();
    res.json({ ok: true, status: r.status, data });
  } catch (err) {
    res.status(500).json({ ok: false, step: "ezvid/list", error: err.message });
  }
});

// Resolve a movie stream URL via ezvidapi. Default TMDB id 27205 = Inception.
// Usage: /ezvid/movie?tmdb=27205&provider=vidsrc
app.get("/ezvid/movie", async (req, res) => {
  const tmdbId = req.query.tmdb || "27205";
  const provider = req.query.provider || "vidsrc";
  const url = `${EZVID_BASE}/movie/${provider}/${tmdbId}`;
  try {
    const r = await fetch(url);
    const text = await r.text();
    let data;
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
    res.json({ ok: true, url, status: r.status, data });
  } catch (err) {
    res.status(500).json({ ok: false, step: "ezvid/movie", url, error: err.message });
  }
});

// Resolve a TV episode stream URL via ezvidapi.
// Usage: /ezvid/tv?tmdb=1399&provider=vidsrc&season=1&episode=1
app.get("/ezvid/tv", async (req, res) => {
  const tmdbId = req.query.tmdb || "1399";
  const provider = req.query.provider || "vidsrc";
  const season = req.query.season || "1";
  const episode = req.query.episode || "1";
  const url = `${EZVID_BASE}/tv/${provider}/${tmdbId}?season=${season}&episode=${episode}`;
  try {
    const r = await fetch(url);
    const text = await r.text();
    let data;
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
    res.json({ ok: true, url, status: r.status, data });
  } catch (err) {
    res.status(500).json({ ok: false, step: "ezvid/tv", url, error: err.message });
  }
});

app.listen(PORT, () => {
  console.log(`Cymor MovieBox test API listening on port ${PORT}`);
});
