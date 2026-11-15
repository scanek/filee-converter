import os
import shutil
import tempfile
import subprocess
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="Filee Web Converter", version="2.4.0")

# Mount static directory for UI assets
static_path = Path(__file__).parent / "static"
static_path.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

def cleanup_file(path: str):
    """Safely remove temporary file after response is sent."""
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass

@app.get("/", response_class=HTMLResponse)
async def index():
    html_file = static_path / "index.html"
    if html_file.exists():
        return html_file.read_text(encoding="utf-8")
    return "<h1>Filee Web Converter</h1><p>UI is loading...</p>"

@app.get("/api/health")
async def health():
    return {"status": "ok", "engine": "Filee 2.4.0"}

@app.post("/api/convert")
async def convert_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    target_format: str = Form(...),
    quality: int = Form(default=None)
):
    target_format = target_format.strip().lower().lstrip(".")
    if not target_format:
        raise HTTPException(status_code=400, detail="Не указан целевой формат")

    # Create temporary directory for isolated execution
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        input_file_path = temp_path / file.filename

        with open(input_file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        output_dir = temp_path / "output"
        output_dir.mkdir(exist_ok=True)

        cmd = [
            "filee", "convert",
            str(input_file_path),
            "--to", target_format,
            "-o", str(output_dir)
        ]

        if quality is not None and 1 <= quality <= 100:
            cmd.extend(["--quality", str(quality)])

        env = os.environ.copy()
        env["FILEE_DATA_DIR"] = "/tmp/filee-data"

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=env)
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

        download_name = f"{Path(file.filename).stem}.{target_format}"
        return FileResponse(
            path=temp_out.name,
            filename=download_name,
            media_type="application/octet-stream"
        )
