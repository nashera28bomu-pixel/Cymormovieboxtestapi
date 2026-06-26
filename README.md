# Cymor MovieBox API Microservice

Python FastAPI wrapper around `moviebox-api` — powers streaming, downloading, and subtitles for **Cymor Movie Hub**.

---

## Stack
- Python + FastAPI
- moviebox-api v0.5.3 (wraps moviebox.ph / h5.aoneroom.com)
- Deployed on Render (free tier)

---

## Endpoints

### Health
| Method | Route | Description |
|--------|-------|-------------|
| GET | `/` | Service info |
| GET | `/health` | Health check |

### Search
| Method | Route | Query Params | Description |
|--------|-------|------|-------------|
| GET | `/search/movies` | `q`, `page`, `per_page` | Search movies |
| GET | `/search/series` | `q`, `page`, `per_page` | Search TV series |

### File Info (qualities + subtitles)
| Method | Route | Query Params | Description |
|--------|-------|------|-------------|
| GET | `/movies/files` | `title` | Available qualities + subtitles for a movie |
| GET | `/series/files` | `title`, `season`, `episode` | Available qualities + subtitles for a series episode |

### Downloads (async jobs)
| Method | Route | Query Params | Description |
|--------|-------|------|-------------|
| POST | `/download/movie` | `title`, `quality`, `subtitle`, `subtitle_language` | Start movie download |
| POST | `/download/series` | `title`, `season`, `episode`, `quality`, `subtitle`, `subtitle_language` | Start episode download |
| GET | `/download/progress/:job_id` | — | Poll progress |
| GET | `/download/jobs` | — | List all jobs |
| DELETE | `/download/job/:job_id` | — | Remove job |

### Serve Files
| Method | Route | Description |
|--------|-------|-------------|
| GET | `/files/:job_id/video` | Stream/download the video file |
| GET | `/files/:job_id/subtitle` | Download the subtitle file |

---

## Quality Options
`best` · `1080p` · `720p` · `480p` · `360p` · `worst`

---

## Node.js Integration (Express)

```javascript
const MOVIEBOX_API = process.env.MOVIEBOX_API_URL || 'https://cymor-moviebox-api.onrender.com';

// Search movies
app.get('/api/search', async (req, res) => {
  const { q, type = 'movies' } = req.query;
  const endpoint = type === 'series' ? 'series' : 'movies';
  const response = await fetch(`${MOVIEBOX_API}/search/${endpoint}?q=${encodeURIComponent(q)}`);
  const data = await response.json();
  res.json(data);
});

// Get available qualities for a movie
app.get('/api/movie/files', async (req, res) => {
  const { title } = req.query;
  const response = await fetch(`${MOVIEBOX_API}/movies/files?title=${encodeURIComponent(title)}`);
  const data = await response.json();
  res.json(data);
});

// Start a movie download
app.post('/api/download/movie', async (req, res) => {
  const { title, quality = 'best', subtitle = true } = req.body;
  const params = new URLSearchParams({ title, quality, subtitle });
  const response = await fetch(`${MOVIEBOX_API}/download/movie?${params}`, { method: 'POST' });
  const data = await response.json();
  // data.job_id — poll this for progress
  res.json(data);
});

// Poll progress
app.get('/api/download/progress/:jobId', async (req, res) => {
  const response = await fetch(`${MOVIEBOX_API}/download/progress/${req.params.jobId}`);
  const data = await response.json();
  res.json(data);
});
```

### Frontend Progress Polling (vanilla JS)

```javascript
async function startDownload(title, quality = 'best') {
  // Start job
  const res = await fetch('/api/download/movie', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title, quality })
  });
  const { job_id } = await res.json();

  // Poll every 2 seconds
  const interval = setInterval(async () => {
    const prog = await fetch(`/api/download/progress/${job_id}`).then(r => r.json());

    updateProgressBar(prog.percent);
    updateStatus(prog.status);

    if (prog.status === 'done') {
      clearInterval(interval);
      // Link to video
      document.getElementById('download-link').href = `/files/${job_id}/video`;
    }

    if (prog.status === 'error') {
      clearInterval(interval);
      showError(prog.error);
    }
  }, 2000);
}
```

---

## Job Status Flow

```
queued → searching → fetching_details → downloading → downloading_subtitle → done
                                                                          ↘ error
```

---

## Running Locally

```bash
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Then open: http://localhost:8000/docs (auto Swagger UI)

---

## Deploy to Render

1. Push this folder to a GitHub repo
2. Create new **Web Service** on Render
3. Set runtime: **Python**
4. Build command: `pip install -r requirements.txt`
5. Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
6. Add env var: `DOWNLOAD_DIR=/tmp/cymor_downloads`

> ⚠️ Note: On Render free tier, `/tmp` files are ephemeral and wiped on restart.
> Serve the file immediately after download completes.
