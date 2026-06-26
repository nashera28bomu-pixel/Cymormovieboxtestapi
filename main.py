"""
Cymor MovieBox Microservice
Python FastAPI wrapper around moviebox-api
Exposes REST endpoints for Node.js backend to consume
"""

import asyncio
import os
import uuid
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse

from moviebox_api.v1 import (
    Search,
    Session,
    SubjectType,
    MovieDetails,
    TVSeriesDetails,
    DownloadableMovieFilesDetail,
    DownloadableTVSeriesFilesDetail,
)
from moviebox_api.v1.download import MediaFileDownloader, CaptionFileDownloader

# ─── In-memory job tracker ───────────────────────────────────────────────────
# Tracks download progress: { job_id: { status, percent, filename, error } }
download_jobs: dict = {}

DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", "/tmp/cymor_downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ─── App setup ───────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🎬 Cymor MovieBox Microservice starting up...")
    yield
    print("🛑 Cymor MovieBox Microservice shutting down...")

app = FastAPI(
    title="Cymor MovieBox API",
    description="Python microservice wrapping moviebox-api for Cymor Movie Hub",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict to your Node backend URL in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Helpers ─────────────────────────────────────────────────────────────────

def session():
    return Session()


def serialize_search_item(item):
    """Convert a search result item to a plain dict."""
    try:
        return {
            "id": getattr(item, "id", None),
            "title": getattr(item, "name", None) or getattr(item, "title", None),
            "year": getattr(item, "year", None),
            "page_url": getattr(item, "page_url", None),
            "poster": getattr(item, "poster", None) or getattr(item, "cover", None),
            "type": getattr(item, "subject_type", None),
            "rating": getattr(item, "score", None),
        }
    except Exception:
        return {}


def serialize_media_file(f):
    """Convert a downloadable media file to a plain dict."""
    try:
        return {
            "quality": str(getattr(f, "quality", "unknown")),
            "size": getattr(f, "size", None),
            "url": getattr(f, "url", None),
        }
    except Exception:
        return {}


def serialize_caption_file(f):
    """Convert a subtitle/caption file to a plain dict."""
    try:
        return {
            "language": getattr(f, "language", "unknown"),
            "url": getattr(f, "url", None),
        }
    except Exception:
        return {}


# ─── Background download task ────────────────────────────────────────────────

async def run_download(job_id: str, title: str, quality: str, is_series: bool,
                       season: Optional[int], episode: Optional[int],
                       with_subtitle: bool, subtitle_language: str):
    """Background task — downloads file and tracks progress."""
    try:
        download_jobs[job_id]["status"] = "searching"
        client_session = session()

        subject_type = SubjectType.TV_SERIES if is_series else SubjectType.MOVIES
        search = Search(client_session, title, subject_type=subject_type)
        results = await search.get_content_model()

        if not results.items:
            download_jobs[job_id]["status"] = "error"
            download_jobs[job_id]["error"] = f"No results found for '{title}'"
            return

        first_item = results.first_item
        download_jobs[job_id]["status"] = "fetching_details"

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

        # Pick best quality or match requested
        media_file = None
        for f in dl_detail.downloads:
            fq = str(getattr(f, "quality", "")).lower()
            if quality.lower() in fq or quality == "best":
                media_file = f
                break
        if not media_file:
            media_file = dl_detail.best_media_file

        download_jobs[job_id]["status"] = "downloading"
        download_jobs[job_id]["quality"] = str(getattr(media_file, "quality", "?"))

        saved_files = []

        # Progress hook
        async def on_progress(tracker):
            if tracker.expected_size and tracker.expected_size > 0:
                pct = round((tracker.downloaded_size / tracker.expected_size) * 100, 1)
                download_jobs[job_id]["percent"] = pct
                download_jobs[job_id]["downloaded_bytes"] = tracker.downloaded_size
                download_jobs[job_id]["total_bytes"] = tracker.expected_size

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

        saved_files.append(str(dl_result.saved_to))
        download_jobs[job_id]["video_path"] = str(dl_result.saved_to)

        # Subtitle
        if with_subtitle:
            try:
                download_jobs[job_id]["status"] = "downloading_subtitle"
                # Find subtitle by language
                caption_file = None
                for cap in dl_detail.captions:
                    lang = str(getattr(cap, "language", "")).lower()
                    if subtitle_language.lower() in lang:
                        caption_file = cap
                        break
                if not caption_file and dl_detail.captions:
                    caption_file = dl_detail.english_subtitle_file

                if caption_file:
                    cap_downloader = CaptionFileDownloader(download_dir=DOWNLOAD_DIR)
                    if is_series:
                        cap_result = await cap_downloader.run(
                            caption_file, filename=first_item,
                            season=season, episode=episode
                        )
                    else:
                        cap_result = await cap_downloader.run(
                            caption_file, filename=first_item
                        )
                    saved_files.append(str(cap_result.saved_to))
                    download_jobs[job_id]["subtitle_path"] = str(cap_result.saved_to)
            except Exception as sub_err:
                download_jobs[job_id]["subtitle_error"] = str(sub_err)

        download_jobs[job_id]["status"] = "done"
        download_jobs[job_id]["percent"] = 100
        download_jobs[job_id]["files"] = saved_files

    except Exception as e:
        download_jobs[job_id]["status"] = "error"
        download_jobs[job_id]["error"] = str(e)


# ═══════════════════════════════════════════════════════════════════════════
# ROUTES
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/")
async def root():
    return {"service": "Cymor MovieBox API", "status": "running", "version": "1.0.0"}


@app.get("/health")
async def health():
    return {"status": "ok"}


# ─── SEARCH ──────────────────────────────────────────────────────────────────

@app.get("/search/movies")
async def search_movies(
    q: str = Query(..., description="Movie title to search"),
    page: int = Query(1, ge=1),
    per_page: int = Query(10, ge=1, le=20),
):
    """Search for movies by title."""
    try:
        client_session = session()
        search = Search(
            client_session, query=q,
            subject_type=SubjectType.MOVIES,
            page=page, per_page=per_page
        )
        results = await search.get_content_model()
        items = [serialize_search_item(i) for i in results.items]
        return {
            "query": q,
            "page": page,
            "has_more": getattr(results.pager, "hasMore", False) if hasattr(results, "pager") else False,
            "total": len(items),
            "results": items,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/search/series")
async def search_series(
    q: str = Query(..., description="TV series title to search"),
    page: int = Query(1, ge=1),
    per_page: int = Query(10, ge=1, le=20),
):
    """Search for TV series by title."""
    try:
        client_session = session()
        search = Search(
            client_session, query=q,
            subject_type=SubjectType.TV_SERIES,
            page=page, per_page=per_page
        )
        results = await search.get_content_model()
        items = [serialize_search_item(i) for i in results.items]
        return {
            "query": q,
            "page": page,
            "has_more": getattr(results.pager, "hasMore", False) if hasattr(results, "pager") else False,
            "total": len(items),
            "results": items,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── FILE INFO (qualities + subtitles available) ──────────────────────────────

@app.get("/movies/files")
async def movie_files(
    title: str = Query(..., description="Movie title"),
):
    """Get available video qualities and subtitles for a movie."""
    try:
        client_session = session()
        search = Search(client_session, title, subject_type=SubjectType.MOVIES)
        results = await search.get_content_model()
        if not results.items:
            raise HTTPException(status_code=404, detail=f"No movie found for '{title}'")

        first_item = results.first_item
        details_inst = MovieDetails(first_item, session=client_session)
        details_model = await details_inst.get_content_model()

        dl_files = DownloadableMovieFilesDetail(client_session, details_model)
        dl_detail = await dl_files.get_content_model()

        return {
            "title": serialize_search_item(first_item)["title"],
            "year": serialize_search_item(first_item)["year"],
            "videos": [serialize_media_file(f) for f in dl_detail.downloads],
            "subtitles": [serialize_caption_file(f) for f in dl_detail.captions],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/series/files")
async def series_files(
    title: str = Query(..., description="TV series title"),
    season: int = Query(1, ge=1),
    episode: int = Query(1, ge=1),
):
    """Get available video qualities and subtitles for a TV series episode."""
    try:
        client_session = session()
        search = Search(client_session, title, subject_type=SubjectType.TV_SERIES)
        results = await search.get_content_model()
        if not results.items:
            raise HTTPException(status_code=404, detail=f"No series found for '{title}'")

        first_item = results.first_item
        details_inst = TVSeriesDetails(first_item, session=client_session)
        details_model = await details_inst.get_content_model()

        dl_files = DownloadableTVSeriesFilesDetail(client_session, details_model)
        dl_detail = await dl_files.get_content_model(season=season, episode=episode)

        return {
            "title": serialize_search_item(first_item)["title"],
            "season": season,
            "episode": episode,
            "videos": [serialize_media_file(f) for f in dl_detail.downloads],
            "subtitles": [serialize_caption_file(f) for f in dl_detail.captions],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── DOWNLOAD (async job with progress) ───────────────────────────────────────

@app.post("/download/movie")
async def download_movie(
    background_tasks: BackgroundTasks,
    title: str = Query(..., description="Movie title"),
    quality: str = Query("best", description="Quality: best, 1080p, 720p, 480p, 360p"),
    subtitle: bool = Query(True, description="Include subtitle"),
    subtitle_language: str = Query("English"),
):
    """Start a movie download job. Returns a job_id to track progress."""
    job_id = str(uuid.uuid4())
    download_jobs[job_id] = {
        "job_id": job_id,
        "type": "movie",
        "title": title,
        "status": "queued",
        "percent": 0,
        "downloaded_bytes": 0,
        "total_bytes": 0,
        "quality": quality,
        "files": [],
        "error": None,
    }
    background_tasks.add_task(
        run_download,
        job_id=job_id, title=title, quality=quality,
        is_series=False, season=None, episode=None,
        with_subtitle=subtitle, subtitle_language=subtitle_language
    )
    return {"job_id": job_id, "message": "Download started", "title": title}


@app.post("/download/series")
async def download_series(
    background_tasks: BackgroundTasks,
    title: str = Query(..., description="Series title"),
    season: int = Query(1, ge=1),
    episode: int = Query(1, ge=1),
    quality: str = Query("best"),
    subtitle: bool = Query(True),
    subtitle_language: str = Query("English"),
):
    """Start a TV series episode download job. Returns a job_id to track progress."""
    job_id = str(uuid.uuid4())
    download_jobs[job_id] = {
        "job_id": job_id,
        "type": "series",
        "title": title,
        "season": season,
        "episode": episode,
        "status": "queued",
        "percent": 0,
        "downloaded_bytes": 0,
        "total_bytes": 0,
        "quality": quality,
        "files": [],
        "error": None,
    }
    background_tasks.add_task(
        run_download,
        job_id=job_id, title=title, quality=quality,
        is_series=True, season=season, episode=episode,
        with_subtitle=subtitle, subtitle_language=subtitle_language
    )
    return {"job_id": job_id, "message": "Download started", "title": title, "season": season, "episode": episode}


# ─── PROGRESS ────────────────────────────────────────────────────────────────

@app.get("/download/progress/{job_id}")
async def download_progress(job_id: str):
    """Poll this to get live download progress."""
    job = download_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/download/jobs")
async def list_jobs():
    """List all download jobs (for admin/debug)."""
    return {"jobs": list(download_jobs.values())}


@app.delete("/download/job/{job_id}")
async def delete_job(job_id: str):
    """Remove a job from tracker."""
    if job_id in download_jobs:
        del download_jobs[job_id]
        return {"message": "Job removed"}
    raise HTTPException(status_code=404, detail="Job not found")


# ─── SERVE DOWNLOADED FILE ───────────────────────────────────────────────────

@app.get("/files/{job_id}/video")
async def serve_video(job_id: str):
    """Serve the downloaded video file for a completed job."""
    job = download_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "done":
        raise HTTPException(status_code=400, detail=f"Job not done yet: {job['status']}")
    video_path = job.get("video_path")
    if not video_path or not os.path.exists(video_path):
        raise HTTPException(status_code=404, detail="Video file not found on disk")
    return FileResponse(video_path, media_type="video/mp4", filename=os.path.basename(video_path))


@app.get("/files/{job_id}/subtitle")
async def serve_subtitle(job_id: str):
    """Serve the downloaded subtitle file for a completed job."""
    job = download_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    subtitle_path = job.get("subtitle_path")
    if not subtitle_path or not os.path.exists(subtitle_path):
        raise HTTPException(status_code=404, detail="Subtitle file not found")
    return FileResponse(subtitle_path, media_type="text/plain", filename=os.path.basename(subtitle_path))
