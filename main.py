"""
Cymor MovieBox Microservice - v3
MovieBoxHttpClient must be used as: async with MovieBoxHttpClient() as http:
"""

import os
import uuid
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from moviebox_api.v3.core import (
    Search,
    SubjectType,
    ItemDetails,
    DownloadableFilesDetail,
    SeasonDetails,
    MovieBoxHttpClient,
)
from moviebox_api.v3.download import (
    MediaFileDownloader,
    CaptionFileDownloader,
)

# ─── Job tracker ──────────────────────────────────────────────────────────────
download_jobs: dict = {}
DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", "/tmp/cymor_downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ─── App ──────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🎬 Cymor MovieBox API v3 ready")
    yield
    print("🛑 Shutting down...")

app = FastAPI(title="Cymor MovieBox API", version="3.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ─── Serializers ──────────────────────────────────────────────────────────────

def s_item(item):
    return {
        "id": getattr(item, "id", None),
        "title": getattr(item, "name", None) or getattr(item, "title", None),
        "year": str(getattr(item, "year", None) or getattr(item, "release_date", "") or ""),
        "page_url": getattr(item, "page_url", None),
        "poster": getattr(item, "poster", None) or getattr(item, "cover", None) or getattr(item, "image", None),
        "type": str(getattr(item, "subject_type", "")),
        "rating": getattr(item, "score", None) or getattr(item, "rating", None),
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

        async with MovieBoxHttpClient() as http:
            subject_type = SubjectType.TV_SERIES if is_series else SubjectType.MOVIES
            results = await Search(http, query=title, subject_type=subject_type).get_content_model()

            if not results.items:
                download_jobs[job_id].update({"status": "error", "error": f"No results for '{title}'"})
                return

            first_item = results.first_item
            download_jobs[job_id]["found_title"] = getattr(first_item, "name", title)
            download_jobs[job_id]["status"] = "fetching_details"

            details = await ItemDetails(first_item, http_client=http).get_content_model()

            if is_series:
                dl_detail = await SeasonDetails(
                    http_client=http, item_details=details,
                    season=season, episode=episode,
                ).get_content_model()
            else:
                dl_detail = await DownloadableFilesDetail(
                    http_client=http, item_details=details,
                ).get_content_model()

            # Pick quality + dub
            media_file = None
            for f in dl_detail.downloads:
                fq = str(getattr(f, "quality", "")).lower()
                flang = str(getattr(f, "language", "")).lower()
                if (quality == "best" or quality.lower() in fq) and (not dub or dub.lower() in flang):
                    media_file = f
                    break
            if not media_file:
                media_file = dl_detail.best_media_file

            download_jobs[job_id].update({
                "quality": str(getattr(media_file, "quality", "?")),
                "language": str(getattr(media_file, "language", "")),
                "status": "downloading",
            })

            async def on_progress(tracker):
                exp = getattr(tracker, "expected_size", 0) or 0
                dl = getattr(tracker, "downloaded_size", 0) or 0
                if exp > 0:
                    download_jobs[job_id].update({
                        "percent": round((dl / exp) * 100, 1),
                        "downloaded_bytes": dl,
                        "total_bytes": exp,
                    })

            downloader = MediaFileDownloader(download_dir=DOWNLOAD_DIR)
            if is_series:
                dl_result = await downloader.run(
                    media_file, filename=first_item,
                    season=season, episode=episode,
                    progress_hook=on_progress,
                )
            else:
                dl_result = await downloader.run(
                    media_file, filename=first_item,
                    progress_hook=on_progress,
                )

            download_jobs[job_id]["video_path"] = str(dl_result.saved_to)
            download_jobs[job_id]["percent"] = 100

            # Subtitle
            captions = getattr(dl_detail, "captions", []) or []
            if with_subtitle and captions:
                try:
                    download_jobs[job_id]["status"] = "downloading_subtitle"
                    caption_file = None
                    for cap in captions:
                        if subtitle_language.lower() in str(getattr(cap, "language", "")).lower():
                            caption_file = cap
                            break
                    if not caption_file:
                        caption_file = getattr(dl_detail, "english_subtitle_file", None) or captions[0]

                    if caption_file:
                        cap_dl = CaptionFileDownloader(download_dir=DOWNLOAD_DIR)
                        if is_series:
                            cap_r = await cap_dl.run(caption_file, filename=first_item, season=season, episode=episode)
                        else:
                            cap_r = await cap_dl.run(caption_file, filename=first_item)
                        download_jobs[job_id]["subtitle_path"] = str(cap_r.saved_to)
                except Exception as sub_err:
                    download_jobs[job_id]["subtitle_error"] = str(sub_err)

        download_jobs[job_id]["status"] = "done"

    except Exception as e:
        import traceback
        download_jobs[job_id].update({"status": "error", "error": str(e), "traceback": traceback.format_exc()})


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/")
async def root():
    return {"service": "Cymor MovieBox API", "version": "3.0.0", "status": "running"}

@app.get("/health")
async def health():
    return {"status": "ok"}


# ── SEARCH ─────────────────────────────────────────────────────────────────────

@app.get("/search/movies")
async def search_movies(q: str = Query(...), page: int = Query(1, ge=1), per_page: int = Query(10, ge=1, le=20)):
    try:
        async with MovieBoxHttpClient() as http:
            results = await Search(http, query=q, subject_type=SubjectType.MOVIES, page=page, per_page=per_page).get_content_model()
        return {
            "query": q, "page": page,
            "has_more": getattr(getattr(results, "pager", None), "hasMore", False),
            "total": len(results.items),
            "results": [s_item(i) for i in results.items],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/search/series")
async def search_series(q: str = Query(...), page: int = Query(1, ge=1), per_page: int = Query(10, ge=1, le=20)):
    try:
        async with MovieBoxHttpClient() as http:
            results = await Search(http, query=q, subject_type=SubjectType.TV_SERIES, page=page, per_page=per_page).get_content_model()
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
    try:
        async with MovieBoxHttpClient() as http:
            results = await Search(http, title, subject_type=SubjectType.MOVIES).get_content_model()
            if not results.items:
                raise HTTPException(status_code=404, detail=f"No movie found for '{title}'")
            item = results.first_item
            details = await ItemDetails(item, http_client=http).get_content_model()
            dl = await DownloadableFilesDetail(http_client=http, item_details=details).get_content_model()
        return {
            **s_item(item),
            "videos": [s_video(f) for f in dl.downloads],
            "subtitles": [s_caption(f) for f in (dl.captions or [])],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/series/files")
async def series_files(title: str = Query(...), season: int = Query(1, ge=1), episode: int = Query(1, ge=1)):
    try:
        async with MovieBoxHttpClient() as http:
            results = await Search(http, title, subject_type=SubjectType.TV_SERIES).get_content_model()
            if not results.items:
                raise HTTPException(status_code=404, detail=f"No series found for '{title}'")
            item = results.first_item
            details = await ItemDetails(item, http_client=http).get_content_model()
            dl = await SeasonDetails(http_client=http, item_details=details, season=season, episode=episode).get_content_model()
        return {
            **s_item(item),
            "season": season, "episode": episode,
            "videos": [s_video(f) for f in dl.downloads],
            "subtitles": [s_caption(f) for f in (dl.captions or [])],
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
    quality: str = Query("best"),
    subtitle: bool = Query(True),
    subtitle_language: str = Query("English"),
    dub: Optional[str] = Query(None),
):
    job_id = str(uuid.uuid4())
    download_jobs[job_id] = {
        "job_id": job_id, "type": "movie", "title": title, "status": "queued",
        "percent": 0, "downloaded_bytes": 0, "total_bytes": 0,
        "quality": quality, "error": None, "video_path": None, "subtitle_path": None,
    }
    background_tasks.add_task(run_download, job_id, title, quality, False, None, None, subtitle, subtitle_language, dub)
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
    dub: Optional[str] = Query(None),
):
    job_id = str(uuid.uuid4())
    download_jobs[job_id] = {
        "job_id": job_id, "type": "series", "title": title,
        "season": season, "episode": episode, "status": "queued",
        "percent": 0, "downloaded_bytes": 0, "total_bytes": 0,
        "quality": quality, "error": None, "video_path": None, "subtitle_path": None,
    }
    background_tasks.add_task(run_download, job_id, title, quality, True, season, episode, subtitle, subtitle_language, dub)
    return {"job_id": job_id, "message": "Download started", "title": title, "season": season, "episode": episode}


@app.get("/download/progress/{job_id}")
async def download_progress(job_id: str):
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
    job = download_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "done":
        raise HTTPException(status_code=400, detail=f"Job not done: {job['status']}")
    path = job.get("video_path")
    if not path or not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File not on disk")
    return FileResponse(path, media_type="video/mp4", filename=os.path.basename(path))

@app.get("/files/{job_id}/subtitle")
async def serve_subtitle(job_id: str):
    job = download_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    path = job.get("subtitle_path")
    if not path or not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Subtitle not found")
    return FileResponse(path, media_type="text/plain", filename=os.path.basename(path))
