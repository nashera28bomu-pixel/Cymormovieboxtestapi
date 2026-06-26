"""
Cymor MovieBox Microservice - v3
Python FastAPI wrapper around moviebox-api v3
Exposes REST endpoints for Cymor Movie Hub Node.js backend
"""

import os
import uuid
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

# ── V3 imports ────────────────────────────────────────────────────────────────
from moviebox_api.v3 import (
    MovieAuto,
    TVSeriesAuto,
    Search,
    Session,
    SubjectType,
    MovieDetails,
    TVSeriesDetails,
    DownloadableMovieFilesDetail,
    DownloadableTVSeriesFilesDetail,
)
from moviebox_api.v3.download import MediaFileDownloader, CaptionFileDownloader

# ─── Job tracker ──────────────────────────────────────────────────────────────
download_jobs: dict = {}

DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", "/tmp/cymor_downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ─── App ──────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🎬 Cymor MovieBox API v3 starting...")
    yield
    print("🛑 Cymor MovieBox API shutting down...")

app = FastAPI(
    title="Cymor MovieBox API",
    description="v3 Python microservice for Cymor Movie Hub",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Serializers ──────────────────────────────────────────────────────────────

def s_item(item):
    return {
        "id": getattr(item, "id", None),
        "title": getattr(item, "name", None) or getattr(item, "title", None),
        "year": getattr(item, "year", None) or getattr(item, "release_date", None),
        "page_url": getattr(item, "page_url", None),
        "poster": getattr(item, "poster", None) or getattr(item, "cover", None) or getattr(item, "image", None),
        "type": str(getattr(item, "subject_type", "")),
        "rating": getattr(item, "score", None) or getattr(item, "rating", None),
        "description": getattr(item, "description", None) or getattr(item, "intro", None),
    }

def s_video(f):
    return {
        "quality": str(getattr(f, "quality", "unknown")),
        "size": getattr(f, "size", None),
        "url": getattr(f, "url", None),
        "language": str(getattr(f, "language", "")),
    }

def s_caption(f):
    return {
        "language": str(getattr(f, "language", "unknown")),
        "url": getattr(f, "url", None),
    }

# ─── Background download ───────────────────────────────────────────────────────

async def run_download(
    job_id: str,
    title: str,
    quality: str,
    is_series: bool,
    season: Optional[int],
    episode: Optional[int],
    with_subtitle: bool,
    subtitle_language: str,
    dub: Optional[str],
):
    try:
        download_jobs[job_id]["status"] = "searching"
        client_session = Session()

        subject_type = SubjectType.TV_SERIES if is_series else SubjectType.MOVIES
        search = Search(client_session, query=title, subject_type=subject_type)
        results = await search.get_content_model()

        if not results.items:
            download_jobs[job_id].update({"status": "error", "error": f"No results found for '{title}'"})
            return

        first_item = results.first_item
        download_jobs[job_id]["found_title"] = getattr(first_item, "name", title)
        download_jobs[job_id]["status"] = "fetching_details"

        # ── Get download file metadata ──────────────────────────────────────
        if is_series:
            details_inst = TVSeriesDetails(first_item, session=client_session)
            details_model = await details_inst.get_content_model()
            dl_files = DownloadableTVSeriesFilesDetail(client_session, details_model)
            dl_detail = await dl_files.get_content_model(season=season, episode=episode)
        else:
            details_inst = MovieDetails(first_item, session=client_session)
            details_model = await details_inst.get_content_model()
            dl_files = DownloadableMovieFilesDetail(client_session, details_model)
            dl_detail = await dl_files.get_content_model()

        # ── Pick quality ────────────────────────────────────────────────────
        media_file = None
        for f in dl_detail.downloads:
            fq = str(getattr(f, "quality", "")).lower()
            # Also match dub language if requested
            flang = str(getattr(f, "language", "")).lower()
            quality_match = quality == "best" or quality.lower() in fq
            dub_match = not dub or dub.lower() in flang
            if quality_match and dub_match:
                media_file = f
                break
        if not media_file:
            media_file = dl_detail.best_media_file

        download_jobs[job_id]["quality"] = str(getattr(media_file, "quality", "?"))
        download_jobs[job_id]["language"] = str(getattr(media_file, "language", ""))
        download_jobs[job_id]["status"] = "downloading"

        # ── Progress hook ───────────────────────────────────────────────────
        async def on_progress(tracker):
            if getattr(tracker, "expected_size", 0) and tracker.expected_size > 0:
                pct = round((tracker.downloaded_size / tracker.expected_size) * 100, 1)
                download_jobs[job_id].update({
                    "percent": pct,
                    "downloaded_bytes": tracker.downloaded_size,
                    "total_bytes": tracker.expected_size,
                })

        # ── Download video ──────────────────────────────────────────────────
        downloader = MediaFileDownloader(download_dir=DOWNLOAD_DIR)
        if is_series:
            dl_result = await downloader.run(
                media_file, filename=first_item,
                season=season, episode=episode,
                progress_hook=on_progress
            )
        else:
            dl_result = await downloader.run(
                media_file, filename=first_item,
                progress_hook=on_progress
            )

        download_jobs[job_id]["video_path"] = str(dl_result.saved_to)
        download_jobs[job_id]["percent"] = 100

        # ── Subtitle ────────────────────────────────────────────────────────
        if with_subtitle and dl_detail.captions:
            try:
                download_jobs[job_id]["status"] = "downloading_subtitle"
                caption_file = None
                for cap in dl_detail.captions:
                    lang = str(getattr(cap, "language", "")).lower()
                    if subtitle_language.lower() in lang:
                        caption_file = cap
                        break
                if not caption_file:
                    caption_file = dl_detail.english_subtitle_file

                if caption_file:
                    cap_dl = CaptionFileDownloader(download_dir=DOWNLOAD_DIR)
                    if is_series:
                        cap_result = await cap_dl.run(caption_file, filename=first_item, season=season, episode=episode)
                    else:
                        cap_result = await cap_dl.run(caption_file, filename=first_item)
                    download_jobs[job_id]["subtitle_path"] = str(cap_result.saved_to)
            except Exception as sub_err:
                download_jobs[job_id]["subtitle_error"] = str(sub_err)

        download_jobs[job_id]["status"] = "done"

    except Exception as e:
        download_jobs[job_id].update({"status": "error", "error": str(e)})


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/")
async def root():
    return {"service": "Cymor MovieBox API", "version": "2.0.0 (v3 backend)", "status": "running"}

@app.get("/health")
async def health():
    return {"status": "ok"}


# ── SEARCH ─────────────────────────────────────────────────────────────────────

@app.get("/search/movies")
async def search_movies(
    q: str = Query(..., description="Movie title"),
    page: int = Query(1, ge=1),
    per_page: int = Query(10, ge=1, le=20),
):
    """Search movies via v3 backend."""
    try:
        s = Search(Session(), query=q, subject_type=SubjectType.MOVIES, page=page, per_page=per_page)
        results = await s.get_content_model()
        return {
            "query": q, "page": page,
            "has_more": getattr(getattr(results, "pager", None), "hasMore", False),
            "total": len(results.items),
            "results": [s_item(i) for i in results.items],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/search/series")
async def search_series(
    q: str = Query(..., description="TV series title"),
    page: int = Query(1, ge=1),
    per_page: int = Query(10, ge=1, le=20),
):
    """Search TV series via v3 backend."""
    try:
        s = Search(Session(), query=q, subject_type=SubjectType.TV_SERIES, page=page, per_page=per_page)
        results = await s.get_content_model()
        return {
            "query": q, "page": page,
            "has_more": getattr(getattr(results, "pager", None), "hasMore", False),
            "total": len(results.items),
            "results": [s_item(i) for i in results.items],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── FILE INFO ──────────────────────────────────────────────────────────────────

@app.get("/movies/files")
async def movie_files(title: str = Query(...)):
    """Get available video qualities + subtitles for a movie."""
    try:
        client_session = Session()
        search = Search(client_session, title, subject_type=SubjectType.MOVIES)
        results = await search.get_content_model()
        if not results.items:
            raise HTTPException(status_code=404, detail=f"No movie found for '{title}'")

        item = results.first_item
        details = await MovieDetails(item, session=client_session).get_content_model()
        dl_detail = await DownloadableMovieFilesDetail(client_session, details).get_content_model()

        return {
            **s_item(item),
            "videos": [s_video(f) for f in dl_detail.downloads],
            "subtitles": [s_caption(f) for f in dl_detail.captions],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/series/files")
async def series_files(
    title: str = Query(...),
    season: int = Query(1, ge=1),
    episode: int = Query(1, ge=1),
):
    """Get available video qualities + subtitles for a TV episode."""
    try:
        client_session = Session()
        search = Search(client_session, title, subject_type=SubjectType.TV_SERIES)
        results = await search.get_content_model()
        if not results.items:
            raise HTTPException(status_code=404, detail=f"No series found for '{title}'")

        item = results.first_item
        details = await TVSeriesDetails(item, session=client_session).get_content_model()
        dl_detail = await DownloadableTVSeriesFilesDetail(client_session, details).get_content_model(
            season=season, episode=episode
        )

        return {
            **s_item(item),
            "season": season,
            "episode": episode,
            "videos": [s_video(f) for f in dl_detail.downloads],
            "subtitles": [s_caption(f) for f in dl_detail.captions],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── DOWNLOAD JOBS ──────────────────────────────────────────────────────────────

@app.post("/download/movie")
async def download_movie(
    background_tasks: BackgroundTasks,
    title: str = Query(...),
    quality: str = Query("best", description="best | 1080p | 720p | 480p | 360p"),
    subtitle: bool = Query(True),
    subtitle_language: str = Query("English"),
    dub: Optional[str] = Query(None, description="Audio dub language e.g. English, Hindi"),
):
    """Start an async movie download. Returns job_id to poll progress."""
    job_id = str(uuid.uuid4())
    download_jobs[job_id] = {
        "job_id": job_id, "type": "movie", "title": title,
        "status": "queued", "percent": 0,
        "downloaded_bytes": 0, "total_bytes": 0,
        "quality": quality, "error": None, "video_path": None, "subtitle_path": None,
    }
    background_tasks.add_task(
        run_download, job_id, title, quality,
        False, None, None, subtitle, subtitle_language, dub
    )
    return {"job_id": job_id, "message": "Download started", "title": title}


@app.post("/download/series")
async def download_series(
    background_tasks: BackgroundTasks,
    title: str = Query(...),
    season: int = Query(1, ge=1),
    episode: int = Query(1, ge=1),
    quality: str = Query("best"),
    subtitle: bool = Query(True),
    subtitle_language: str = Query("English"),
    dub: Optional[str] = Query(None, description="Audio dub e.g. English, Hindi, te"),
):
    """Start an async series episode download. Returns job_id."""
    job_id = str(uuid.uuid4())
    download_jobs[job_id] = {
        "job_id": job_id, "type": "series", "title": title,
        "season": season, "episode": episode,
        "status": "queued", "percent": 0,
        "downloaded_bytes": 0, "total_bytes": 0,
        "quality": quality, "error": None, "video_path": None, "subtitle_path": None,
    }
    background_tasks.add_task(
        run_download, job_id, title, quality,
        True, season, episode, subtitle, subtitle_language, dub
    )
    return {"job_id": job_id, "message": "Download started", "title": title, "season": season, "episode": episode}


# ── PROGRESS + JOBS ────────────────────────────────────────────────────────────

@app.get("/download/progress/{job_id}")
async def download_progress(job_id: str):
    """Poll this endpoint to get live download progress."""
    job = download_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job

@app.get("/download/jobs")
async def list_jobs():
    return {"count": len(download_jobs), "jobs": list(download_jobs.values())}

@app.delete("/download/job/{job_id}")
async def delete_job(job_id: str):
    if job_id not in download_jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    del download_jobs[job_id]
    return {"message": "Job removed"}


# ── SERVE FILES ────────────────────────────────────────────────────────────────

@app.get("/files/{job_id}/video")
async def serve_video(job_id: str):
    """Stream/download the completed video file."""
    job = download_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "done":
        raise HTTPException(status_code=400, detail=f"Job status: {job['status']}")
    path = job.get("video_path")
    if not path or not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Video file not on disk")
    return FileResponse(path, media_type="video/mp4", filename=os.path.basename(path))

@app.get("/files/{job_id}/subtitle")
async def serve_subtitle(job_id: str):
    """Download the subtitle file for a completed job."""
    job = download_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    path = job.get("subtitle_path")
    if not path or not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Subtitle file not found")
    return FileResponse(path, media_type="text/plain", filename=os.path.basename(path))
