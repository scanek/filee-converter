import os
import re
import shutil
import tempfile
import subprocess
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="Filee Web Converter", version="2.5.0")

# Mount static directory for UI assets
static_path = Path(__file__).parent / "static"
static_path.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

MEDIA_FORMATS = {
    "mp4", "mkv", "avi", "mov", "webm", "flv", "wmv", "m4v", "ts", "3gp",
    "mp3", "wav", "flac", "ogg", "m4a", "aac", "wma", "opus", "gif"
}

def cleanup_file(path: str):
    """Safely remove temporary file after response is sent."""
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass

def validate_and_clean_time(time_str: Optional[str]) -> Optional[str]:
    """Validate and normalize time format for ffmpeg: e.g. 00:01:23, 01:23, 45, 12.5"""
    if not time_str:
        return None
    time_str = time_str.strip()
    if not time_str:
        return None

    # Matches HH:MM:SS, MM:SS, SS, or seconds with decimals
    if re.match(r"^(\d{1,2}:)?(\d{1,2}:)?\d{1,2}(\.\d+)?$", time_str) or re.match(r"^\d+(\.\d+)?$", time_str):
        return time_str

    raise HTTPException(
        status_code=400,
        detail=f"Неверный формат времени: '{time_str}'. Используйте секунды (например 45) или ЧЧ:ММ:СС (например 00:01:30)"
    )

@app.get("/", response_class=HTMLResponse)
async def index():
    html_file = static_path / "index.html"
    if html_file.exists():
        return html_file.read_text(encoding="utf-8")
    return "<h1>Filee Web Converter</h1><p>UI is loading...</p>"

@app.get("/api/health")
async def health():
    return {"status": "ok", "engine": "Filee 2.5.0 with FFmpeg Media Trimming"}

@app.post("/api/convert")
async def convert_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    target_format: Optional[str] = Form(default=None),
    quality: Optional[int] = Form(default=None),
    start_time: Optional[str] = Form(default=None),
    end_time: Optional[str] = Form(default=None)
):
    target_format = (target_format or "").strip().lower().lstrip(".")
    input_suffix = Path(file.filename).suffix.lstrip(".").lower()

    # If target format not provided, fallback to input format
    if not target_format:
        if input_suffix:
            target_format = input_suffix
        else:
            raise HTTPException(status_code=400, detail="Не указан целевой формат")

    # Validate trimming timestamps
    clean_start = validate_and_clean_time(start_time)
    clean_end = validate_and_clean_time(end_time)
    is_trimming = clean_start is not None or clean_end is not None

    # Create temporary directory for isolated execution
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        input_file_path = temp_path / file.filename

        with open(input_file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        output_dir = temp_path / "output"
        output_dir.mkdir(exist_ok=True)

        env = os.environ.copy()
        env["FILEE_DATA_DIR"] = "/tmp/filee-data"

        # Case 1: Trimming media via FFmpeg directly
        if is_trimming:
            # If target format is a media format, FFmpeg can trim & convert in a single step
            if target_format in MEDIA_FORMATS or input_suffix in MEDIA_FORMATS:
                output_file_path = output_dir / f"result.{target_format}"
                ffmpeg_cmd = ["ffmpeg", "-y"]

                # Seek before input for fast seek, or accurate seek
                if clean_start:
                    ffmpeg_cmd.extend(["-ss", clean_start])
                if clean_end:
                    ffmpeg_cmd.extend(["-to", clean_end])

                ffmpeg_cmd.extend(["-i", str(input_file_path)])

                # Codec handling depending on target format
                if target_format == "gif":
                    ffmpeg_cmd.extend(["-vf", "fps=15,scale=480:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse"])
                elif target_format in {"mp3", "m4a", "aac", "ogg", "opus", "flac", "wav", "wma"}:
                    ffmpeg_cmd.append("-vn")
                    if target_format == "mp3":
                        ffmpeg_cmd.extend(["-c:a", "libmp3lame", "-q:a", "2"])
                    elif target_format == "aac":
                        ffmpeg_cmd.extend(["-c:a", "aac", "-b:a", "192k"])
                    elif target_format == "wav":
                        ffmpeg_cmd.extend(["-c:a", "pcm_s16le"])
                elif target_format in {"mp4", "mkv", "mov", "webm", "avi"}:
                    if target_format == "mp4":
                        ffmpeg_cmd.extend(["-c:v", "libx264", "-preset", "fast", "-crf", "22", "-c:a", "aac", "-b:a", "192k"])
                    elif target_format == "webm":
                        ffmpeg_cmd.extend(["-c:v", "libvpx-vp9", "-crf", "30", "-b:v", "0", "-c:a", "libopus"])

                ffmpeg_cmd.append(str(output_file_path))

                try:
                    result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True, timeout=600, env=env)
                except subprocess.TimeoutExpired:
                    raise HTTPException(status_code=504, detail="Таймаут обрезки медиафайла (превышен лимит 10 минут)")

                if result.returncode != 0 or not output_file_path.exists():
                    error_msg = result.stderr.strip() or result.stdout.strip() or "Ошибка обрезки медиа через FFmpeg"
                    raise HTTPException(status_code=422, detail=error_msg)

            else:
                # If target format is non-media, trim input file first, then run Filee
                trimmed_input = temp_path / f"trimmed_{file.filename}"
                trim_cmd = ["ffmpeg", "-y"]
                if clean_start:
                    trim_cmd.extend(["-ss", clean_start])
                if clean_end:
                    trim_cmd.extend(["-to", clean_end])
                trim_cmd.extend(["-i", str(input_file_path), "-c", "copy", str(trimmed_input)])

                subprocess.run(trim_cmd, capture_output=True, text=True, timeout=300, env=env)
                actual_input = trimmed_input if trimmed_input.exists() else input_file_path

                filee_cmd = [
                    "filee", "convert",
                    str(actual_input),
                    "--to", target_format,
                    "-o", str(output_dir)
                ]
                if quality is not None and 1 <= quality <= 100:
                    filee_cmd.extend(["--quality", str(quality)])

                result = subprocess.run(filee_cmd, capture_output=True, text=True, timeout=300, env=env)
                if result.returncode != 0:
                    error_msg = result.stderr.strip() or result.stdout.strip() or "Ошибка конвертации"
                    raise HTTPException(status_code=422, detail=error_msg)

        # Case 2: Standard conversion without trimming
        else:
            filee_cmd = [
                "filee", "convert",
                str(input_file_path),
                "--to", target_format,
                "-o", str(output_dir)
            ]

            if quality is not None and 1 <= quality <= 100:
                filee_cmd.extend(["--quality", str(quality)])

            try:
                result = subprocess.run(filee_cmd, capture_output=True, text=True, timeout=300, env=env)
            except subprocess.TimeoutExpired:
                raise HTTPException(status_code=504, detail="Таймаут конвертации (превышен лимит 5 минут)")

            if result.returncode != 0:
                error_msg = result.stderr.strip() or result.stdout.strip() or "Ошибка конвертации"
                raise HTTPException(status_code=422, detail=error_msg)

        # Locate converted file
        output_files = list(output_dir.glob("*"))
        if not output_files:
            raise HTTPException(status_code=500, detail="Файл не был создан конвертером")

        output_file = output_files[0]

        # Copy to a persistent temp file so it outlives the TemporaryDirectory
        temp_out = tempfile.NamedTemporaryFile(delete=False, suffix=f".{target_format}")
        shutil.copyfile(output_file, temp_out.name)
        temp_out.close()

        # Add background task to clean up file after response is completed
        background_tasks.add_task(cleanup_file, temp_out.name)

        prefix = "trimmed_" if is_trimming else ""
        download_name = f"{prefix}{Path(file.filename).stem}.{target_format}"
        return FileResponse(
            path=temp_out.name,
            filename=download_name,
            media_type="application/octet-stream"
        )
