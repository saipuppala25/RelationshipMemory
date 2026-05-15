# Revised `app.py` with Production-Ready Cloudflare R2 Integration

"""
Memories of Us: Misio i Misia
Flask web application — media stored on Cloudflare R2 (S3-compatible).

Features:
- Local storage fallback
- Cloudflare R2 object storage
- Signed URL support
- Streaming ZIP uploads
- Organized media folders
- Mobile upload support
- Secure filename handling
- Presigned URL delivery
"""

import os
import uuid
import socket
import secrets
import time
import zipfile
import tempfile
import subprocess
import shutil
from pathlib import Path
from io import BytesIO
from datetime import datetime
import base64

from dotenv import load_dotenv
import certifi

load_dotenv()

certifi_bundle = certifi.where()
if certifi_bundle and Path(certifi_bundle).exists():
    os.environ.setdefault("SSL_CERT_FILE", certifi_bundle)
    os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi_bundle)
    os.environ.setdefault("AWS_CA_BUNDLE", certifi_bundle)

import boto3
from botocore.exceptions import SSLError
import qrcode
import qrcode.image.svg

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    jsonify,
    send_from_directory,
    abort,
)

from werkzeug.utils import secure_filename

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = Flask(__name__)
app.secret_key = os.environ.get(
    "SECRET_KEY",
    "misio-misia-secret-key-change-me"
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CORRECT_PASSWORD = os.environ.get("SITE_PASSWORD", "misioimisia")

ALLOWED_PHOTO_EXTENSIONS = {
    "jpg",
    "jpeg",
    "png",
    "gif",
    "webp",
    "heic",
}

ALLOWED_VIDEO_EXTENSIONS = {
    "mp4",
    "mov",
    "avi",
    "webm",
    "mkv",
}

ALLOWED_EXTENSIONS = (
    ALLOWED_PHOTO_EXTENSIONS |
    ALLOWED_VIDEO_EXTENSIONS
)

# 4GB max request size
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024 * 1024

# Local fallback storage
LOCAL_UPLOAD_FOLDER = Path(__file__).parent / "static" / "media"
LOCAL_UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)

app.config["UPLOAD_FOLDER"] = str(LOCAL_UPLOAD_FOLDER)

MEDIA_URL_EXPIRY = int(
    os.environ.get("MEDIA_URL_EXPIRY", "3600")
)

# ---------------------------------------------------------------------------
# Cloudflare R2 Configuration
#
# IMPORTANT:
# The endpoint URL should NEVER include the bucket name.
# Correct:
#   https://<account_id>.r2.cloudflarestorage.com
# Incorrect:
#   https://<account_id>.r2.cloudflarestorage.com/<bucket>
#
# SSL validation is handled through certifi to avoid
# local OpenSSL certificate chain issues.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------

R2_BUCKET = (
    os.environ.get("R2_BUCKET_NAME", "") or
    os.environ.get("R2_BUCKET", "")
)
R2_ACCOUNT_ID = (
    os.environ.get("R2_ACCOUNT_ID", "") or
    os.environ.get("R2_ACCOUNT", "")
)
R2_ACCESS_KEY_ID = (
    os.environ.get("R2_ACCESS_KEY_ID", "") or
    os.environ.get("R2_ACCESS_KEY", "")
)
R2_SECRET_ACCESS_KEY = (
    os.environ.get("R2_SECRET_ACCESS_KEY", "") or
    os.environ.get("R2_SECRET_KEY", "")
)
R2_PUBLIC_URL = (
    os.environ.get("R2_PUBLIC_URL", "") or
    os.environ.get("R2_URL", "")
)

_r2_client = None


def using_r2() -> bool:
    return bool(
        R2_BUCKET and
        R2_ACCOUNT_ID and
        R2_ACCESS_KEY_ID and
        R2_SECRET_ACCESS_KEY
    )



def _get_r2():
    """Create and cache R2 S3-compatible client."""

    global _r2_client

    if _r2_client is not None:
        return _r2_client

    if not using_r2():
        raise RuntimeError("R2 is not configured")

    skip_ssl = os.environ.get("R2_SKIP_SSL_VERIFY", "").lower() in ("1", "true", "yes", "y")
    verify_path = False if skip_ssl else certifi.where()
    if not skip_ssl and (not verify_path or not Path(verify_path).exists()):
        verify_path = True

    _r2_client = boto3.client(
        "s3",
        endpoint_url=(
            f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
        ),
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        region_name="auto",
        verify=verify_path,
    )

    return _r2_client


# ---------------------------------------------------------------------------
# Mobile Upload Tokens
# ---------------------------------------------------------------------------

TOKEN_TTL = 3600
_mobile_tokens: dict[str, float] = {}



def _purge_expired_tokens() -> None:
    now = time.time()
    expired = [t for t, exp in _mobile_tokens.items() if exp < now]

    for token in expired:
        del _mobile_tokens[token]



def create_mobile_token() -> str:
    _purge_expired_tokens()

    token = secrets.token_urlsafe(32)
    _mobile_tokens[token] = time.time() + TOKEN_TTL

    return token



def validate_mobile_token(token: str) -> bool:
    _purge_expired_tokens()
    return token in _mobile_tokens



def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def allowed_file(filename: str) -> bool:
    return (
        "." in filename and
        filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
    )



def is_video(filename: str) -> bool:
    return (
        "." in filename and
        filename.rsplit(".", 1)[1].lower() in ALLOWED_VIDEO_EXTENSIONS
    )



def get_content_type(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower()

    mapping = {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "gif": "image/gif",
        "webp": "image/webp",
        "heic": "image/heic",
        "mp4": "video/mp4",
        "mov": "video/quicktime",
        "avi": "video/x-msvideo",
        "webm": "video/webm",
        "mkv": "video/x-matroska",
    }

    return mapping.get(ext, "application/octet-stream")



def _unique_name(original_filename: str) -> str:
    """Create organized cloud storage path."""

    ext = original_filename.rsplit(".", 1)[1].lower()

    folder = (
        "videos"
        if ext in ALLOWED_VIDEO_EXTENSIONS
        else "photos"
    )

    now = datetime.utcnow()

    return (
        f"{folder}/"
        f"{now.year}/"
        f"{now.month:02d}/"
        f"{uuid.uuid4().hex}.{ext}"
    )


# ---------------------------------------------------------------------------
# Storage Layer
# ---------------------------------------------------------------------------


def storage_save(file_storage, unique_name: str) -> None:
    """Save uploaded file."""

    if using_r2():
        s3 = _get_r2()

        file_storage.seek(0)

        s3.upload_fileobj(
            file_storage,
            R2_BUCKET,
            unique_name,
            ExtraArgs={
                "ContentType": (
                    file_storage.content_type or
                    get_content_type(unique_name)
                )
            },
        )

    else:
        local_path = LOCAL_UPLOAD_FOLDER / unique_name
        local_path.parent.mkdir(parents=True, exist_ok=True)

        file_storage.save(str(local_path))



def storage_save_bytes(data: bytes, unique_name: str) -> None:
    """Save raw bytes."""

    if using_r2():
        s3 = _get_r2()

        s3.put_object(
            Bucket=R2_BUCKET,
            Key=unique_name,
            Body=data,
            ContentType=get_content_type(unique_name),
        )

    else:
        local_path = LOCAL_UPLOAD_FOLDER / unique_name
        local_path.parent.mkdir(parents=True, exist_ok=True)

        local_path.write_bytes(data)


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _transcode_mov_to_mp4(source_path: Path, target_path: Path) -> bool:
    if not _ffmpeg_available():
        return False

    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(source_path),
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "23",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-movflags",
                "+faststart",
                str(target_path),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def storage_list() -> dict:
    """List all uploaded media."""

    photos = []
    videos = []

    if using_r2():
        s3 = _get_r2()

        paginator = s3.get_paginator("list_objects_v2")

        objects = []

        for page in paginator.paginate(Bucket=R2_BUCKET):
            objects.extend(page.get("Contents", []))

        objects.sort(
            key=lambda obj: obj["LastModified"],
            reverse=True,
        )

        for obj in objects:
            name = obj["Key"]

            if not allowed_file(name):
                continue

            if is_video(name):
                videos.append(name)
            else:
                photos.append(name)

    else:
        all_files = [
            f for f in LOCAL_UPLOAD_FOLDER.rglob("*")
            if f.is_file()
        ]

        all_files.sort(
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        for f in all_files:
            rel = f.relative_to(LOCAL_UPLOAD_FOLDER).as_posix()

            if not allowed_file(rel):
                continue

            if is_video(rel):
                videos.append(rel)
            else:
                photos.append(rel)

    return {
        "photos": photos,
        "videos": videos,
    }



def storage_delete(filename: str) -> bool:
    if using_r2():
        s3 = _get_r2()

        try:
            s3.delete_object(
                Bucket=R2_BUCKET,
                Key=filename,
            )
            return True
        except Exception:
            return False

    local_path = LOCAL_UPLOAD_FOLDER / filename

    if not local_path.exists():
        return False

    local_path.unlink()
    return True



def storage_url(filename: str) -> str:
    """
    Return media URL.

    Public URL is preferred if configured.
    Otherwise generate signed URLs.
    """

    if using_r2():

        # Public CDN URL
        if R2_PUBLIC_URL:
            return f"{R2_PUBLIC_URL.rstrip('/')}/{filename}"

        # Signed URL fallback
        s3 = _get_r2()

        return s3.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": R2_BUCKET,
                "Key": filename,
            },
            ExpiresIn=MEDIA_URL_EXPIRY,
        )

    return url_for("serve_media", filename=filename)


# ---------------------------------------------------------------------------
# Multipart Upload Support
# ---------------------------------------------------------------------------

MULTIPART_CHUNK_SIZE = 25 * 1024 * 1024


def create_presigned_upload(filename: str, content_type: str):

    key = _unique_name(filename)

    s3 = _get_r2()

    upload_url = s3.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": R2_BUCKET,
            "Key": key,
            "ContentType": content_type,
        },
        ExpiresIn=3600,
    )

    return {
        "uploadUrl": upload_url,
        "key": key,
        "fileUrl": storage_url(key),
    }



def create_multipart_upload(filename: str, content_type: str):

    key = _unique_name(filename)

    s3 = _get_r2()

    response = s3.create_multipart_upload(
        Bucket=R2_BUCKET,
        Key=key,
        ContentType=content_type,
    )

    return {
        "uploadId": response["UploadId"],
        "key": key,
    }



def generate_multipart_url(
    key: str,
    upload_id: str,
    part_number: int,
):

    s3 = _get_r2()

    return s3.generate_presigned_url(
        "upload_part",
        Params={
            "Bucket": R2_BUCKET,
            "Key": key,
            "UploadId": upload_id,
            "PartNumber": part_number,
        },
        ExpiresIn=3600,
    )



def complete_multipart_upload(
    key: str,
    upload_id: str,
    parts: list,
):

    s3 = _get_r2()

    return s3.complete_multipart_upload(
        Bucket=R2_BUCKET,
        Key=key,
        UploadId=upload_id,
        MultipartUpload={
            "Parts": parts,
        },
    )



def abort_multipart_upload(
    key: str,
    upload_id: str,
):

    s3 = _get_r2()

    return s3.abort_multipart_upload(
        Bucket=R2_BUCKET,
        Key=key,
        UploadId=upload_id,
    )


# ---------------------------------------------------------------------------
# Upload Processing
# ---------------------------------------------------------------------------


def save_uploaded_file(file_storage) -> str | None:
    if not allowed_file(file_storage.filename):
        return None

    name = _unique_name(file_storage.filename)
    ext = file_storage.filename.rsplit('.', 1)[-1].lower() if '.' in file_storage.filename else ''

    if ext == 'mov' and _ffmpeg_available():
        with tempfile.TemporaryDirectory() as tmpdir:
            source_path = Path(tmpdir) / file_storage.filename
            file_storage.save(str(source_path))

            mp4_name = f"{Path(name).stem}.mp4"
            target_path = Path(tmpdir) / mp4_name

            if _transcode_mov_to_mp4(source_path, target_path):
                if using_r2():
                    s3 = _get_r2()
                    with target_path.open('rb') as fh:
                        s3.upload_fileobj(
                            fh,
                            R2_BUCKET,
                            mp4_name,
                            ExtraArgs={
                                'ContentType': get_content_type(mp4_name),
                            },
                        )
                else:
                    local_path = LOCAL_UPLOAD_FOLDER / mp4_name
                    local_path.parent.mkdir(parents=True, exist_ok=True)
                    target_path.replace(local_path)

                return mp4_name

    storage_save(file_storage, name)
    return name



def extract_zip(file_storage) -> tuple[list[str], list[str]]:
    """
    Stream ZIP contents safely.
    """

    uploaded = []
    errors = []

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            zip_path = Path(tmpdir) / "upload.zip"

            file_storage.save(str(zip_path))

            with zipfile.ZipFile(zip_path, "r") as zf:

                total_size = sum(
                    info.file_size
                    for info in zf.infolist()
                )

                if total_size > 2 * 1024 * 1024 * 1024:
                    return [], [
                        "ZIP archive exceeds 2GB limit"
                    ]

                for info in zf.infolist():

                    if info.is_dir():
                        continue

                    parts = Path(info.filename).parts

                    if any(
                        p.startswith("__MACOSX") or
                        p.startswith(".")
                        for p in parts
                    ):
                        continue

                    basename = Path(info.filename).name

                    if not basename:
                        continue

                    if not allowed_file(basename):
                        errors.append(
                            f"{basename}: unsupported file type"
                        )
                        continue

                    unique_name = _unique_name(basename)
                    ext = basename.rsplit('.', 1)[-1].lower()

                    if ext == 'mov' and _ffmpeg_available():
                        with tempfile.TemporaryDirectory() as tmpdir:
                            source_path = Path(tmpdir) / basename
                            with zf.open(info.filename) as extracted, source_path.open('wb') as out:
                                out.write(extracted.read())

                            mp4_name = f"{Path(unique_name).stem}.mp4"
                            target_path = Path(tmpdir) / mp4_name

                            if _transcode_mov_to_mp4(source_path, target_path):
                                if using_r2():
                                    s3 = _get_r2()
                                    with target_path.open('rb') as fh:
                                        s3.upload_fileobj(
                                            fh,
                                            R2_BUCKET,
                                            mp4_name,
                                            ExtraArgs={
                                                'ContentType': get_content_type(mp4_name),
                                            },
                                        )
                                else:
                                    local_path = LOCAL_UPLOAD_FOLDER / mp4_name
                                    local_path.parent.mkdir(parents=True, exist_ok=True)
                                    target_path.replace(local_path)

                                uploaded.append(mp4_name)
                                continue

                    if using_r2():
                        s3 = _get_r2()

                        with zf.open(info.filename) as extracted:
                            s3.upload_fileobj(
                                extracted,
                                R2_BUCKET,
                                unique_name,
                                ExtraArgs={
                                    "ContentType": (
                                        get_content_type(unique_name)
                                    )
                                },
                            )
                    else:
                        data = zf.read(info.filename)
                        storage_save_bytes(data, unique_name)

                    uploaded.append(unique_name)

    except zipfile.BadZipFile:
        errors.append("Invalid ZIP file")

    except Exception as exc:
        errors.append(f"ZIP extraction failed: {exc}")

    return uploaded, errors



def _handle_upload_request() -> tuple[list[str], list[str]]:

    uploaded = []
    errors = []

    for file in request.files.getlist("files"):

        if file.filename == "":
            continue

        ext = (
            file.filename.rsplit(".", 1)[-1].lower()
            if "." in file.filename
            else ""
        )

        if ext == "zip":
            z, e = extract_zip(file)
            uploaded.extend(z)
            errors.extend(e)
        else:
            saved = save_uploaded_file(file)

            if saved:
                uploaded.append(saved)
            else:
                errors.append(
                    f"{file.filename}: unsupported file type"
                )

    return uploaded, errors


# ---------------------------------------------------------------------------
# Decorators
# ---------------------------------------------------------------------------


def login_required(f):
    from functools import wraps

    @wraps(f)
    def decorated(*args, **kwargs):

        if not session.get("authenticated"):
            return redirect(url_for("login"))

        return f(*args, **kwargs)

    return decorated



def mobile_token_required(f):
    from functools import wraps

    @wraps(f)
    def decorated(*args, **kwargs):

        token = (
            kwargs.get("token") or
            request.args.get("token", "")
        )

        if not validate_mobile_token(token):
            return render_template(
                "mobile_expired.html"
            ), 403

        return f(*args, **kwargs)

    return decorated


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/", methods=["GET", "POST"])
def login():

    error = None

    if request.method == "POST":

        if (
            request.form.get("password", "").strip() ==
            CORRECT_PASSWORD
        ):
            session["authenticated"] = True
            return redirect(url_for("gallery"))

        error = "Incorrect password"

    return render_template(
        "login.html",
        error=error,
    )


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/gallery")
@login_required
def gallery():

    media = storage_list()

    photo_urls = {
        f: storage_url(f)
        for f in media["photos"]
    }

    video_urls = {
        f: storage_url(f)
        for f in media["videos"]
    }

    video_mime_types = {
        f: get_content_type(f)
        for f in media["videos"]
    }

    return render_template(
        "gallery.html",
        photos=media["photos"],
        videos=media["videos"],
        photo_urls=photo_urls,
        video_urls=video_urls,
        video_mime_types=video_mime_types,
    )


@app.route("/upload", methods=["POST"])
@login_required
def upload():

    if "files" not in request.files:
        return jsonify({
            "error": "No files provided"
        }), 400

    uploaded, errors = _handle_upload_request()

    if not uploaded and errors:
        return jsonify({
            "error": errors[0],
            "all_errors": errors,
        }), 400

    media = storage_list()

    return jsonify({
        "uploaded": uploaded,
        "errors": errors,
        "photos": media["photos"],
        "videos": media["videos"],
        "photo_urls": {
            f: storage_url(f)
            for f in media["photos"]
        },
        "video_urls": {
            f: storage_url(f)
            for f in media["videos"]
        },
        "video_mime_types": {
            f: get_content_type(f)
            for f in media["videos"]
        },
    })


@app.route("/delete/<path:filename>", methods=["DELETE"])
@login_required
def delete_media(filename: str):

    if not storage_delete(filename):
        abort(404)

    media = storage_list()

    return jsonify({
        "deleted": filename,
        "photos": media["photos"],
        "videos": media["videos"],
        "photo_urls": {
            f: storage_url(f)
            for f in media["photos"]
        },
        "video_urls": {
            f: storage_url(f)
            for f in media["videos"]
        },
        "video_mime_types": {
            f: get_content_type(f)
            for f in media["videos"]
        },
    })


@app.route("/media/<path:filename>")
@login_required
def serve_media(filename: str):

    if using_r2():
        return redirect(storage_url(filename))

    return send_from_directory(
        str(LOCAL_UPLOAD_FOLDER),
        filename,
    )


# ---------------------------------------------------------------------------
# Direct Upload Routes
# ---------------------------------------------------------------------------

@app.route("/upload/direct", methods=["POST"])
@login_required
def direct_upload():

    if not using_r2():
        return jsonify({
            "error": "R2 storage is not configured"
        }), 400

    data = request.get_json()

    filename = data.get("filename")
    content_type = data.get(
        "contentType",
        "application/octet-stream",
    )

    if not filename:
        return jsonify({
            "error": "Filename is required"
        }), 400

    if not allowed_file(filename):
        return jsonify({
            "error": "Unsupported file type"
        }), 400

    result = create_presigned_upload(
        filename,
        content_type,
    )

    return jsonify(result)


@app.route("/multipart/start", methods=["POST"])
@login_required
def multipart_start():

    if not using_r2():
        return jsonify({
            "error": "R2 storage is not configured"
        }), 400

    data = request.get_json()

    filename = data.get("filename")

    content_type = data.get(
        "contentType",
        "application/octet-stream",
    )

    if not filename:
        return jsonify({
            "error": "Filename is required"
        }), 400

    if not allowed_file(filename):
        return jsonify({
            "error": "Unsupported file type"
        }), 400

    result = create_multipart_upload(
        filename,
        content_type,
    )

    return jsonify(result)


@app.route("/multipart/url", methods=["POST"])
@login_required
def multipart_url():

    data = request.get_json()

    key = data.get("key")
    upload_id = data.get("uploadId")
    part_number = data.get("partNumber")

    if not key or not upload_id or not part_number:
        return jsonify({
            "error": "Missing multipart parameters"
        }), 400

    url = generate_multipart_url(
        key,
        upload_id,
        int(part_number),
    )

    return jsonify({
        "url": url
    })


@app.route("/multipart/complete", methods=["POST"])
@login_required
def multipart_complete():

    data = request.get_json()

    key = data.get("key")
    upload_id = data.get("uploadId")
    parts = data.get("parts")

    if not key or not upload_id or not parts:
        return jsonify({
            "error": "Missing completion parameters"
        }), 400

    complete_multipart_upload(
        key,
        upload_id,
        parts,
    )

    return jsonify({
        "success": True,
        "fileUrl": storage_url(key),
        "key": key,
    })


@app.route("/multipart/abort", methods=["POST"])
@login_required
def multipart_abort():

    data = request.get_json()

    key = data.get("key")
    upload_id = data.get("uploadId")

    if not key or not upload_id:
        return jsonify({
            "error": "Missing abort parameters"
        }), 400

    abort_multipart_upload(
        key,
        upload_id,
    )

    return jsonify({
        "success": True
    })


# ---------------------------------------------------------------------------
# Recommended Frontend Upload Example
# ---------------------------------------------------------------------------

"""
async function uploadLargeFile(file) {

    // 1. Start multipart upload
    const startRes = await fetch('/multipart/start', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            filename: file.name,
            contentType: file.type
        })
    });

    const startData = await startRes.json();

    const chunkSize = 25 * 1024 * 1024;
    const parts = [];

    // 2. Upload chunks
    for (let i = 0; i < file.size; i += chunkSize) {

        const chunk = file.slice(i, i + chunkSize);
        const partNumber = Math.floor(i / chunkSize) + 1;

        const urlRes = await fetch('/multipart/url', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                key: startData.key,
                uploadId: startData.uploadId,
                partNumber
            })
        });

        const { url } = await urlRes.json();

        const uploadRes = await fetch(url, {
            method: 'PUT',
            body: chunk
        });

        parts.push({
            ETag: uploadRes.headers.get('ETag'),
            PartNumber: partNumber
        });
    }

    // 3. Complete upload
    await fetch('/multipart/complete', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            key: startData.key,
            uploadId: startData.uploadId,
            parts
        })
    });
}
"""


# ---------------------------------------------------------------------------
# QR Routes
# ---------------------------------------------------------------------------

@app.route("/qr")
@login_required
def qr_page():

    token = create_mobile_token()

    port = (
        request.host.split(":")[-1]
        if ":" in request.host
        else "5000"
    )

    ip = get_local_ip()

    mobile_url = f"http://{ip}:{port}/mobile/{token}"

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=3,
    )

    qr.add_data(mobile_url)
    qr.make(fit=True)

    img = qr.make_image(
        fill_color="#FF4D6D",
        back_color="#FFF0F3",
    )

    buf = BytesIO()

    img.save(buf, format="PNG")

    data_uri = (
        "data:image/png;base64," +
        base64.b64encode(buf.getvalue()).decode()
    )

    return jsonify({
        "qr_image": data_uri,
        "mobile_url": mobile_url,
        "token": token,
        "expires_in": TOKEN_TTL,
    })


@app.route("/mobile/<token>", methods=["GET"])
@mobile_token_required
def mobile_upload_page(token: str):
    return render_template(
        "mobile_upload.html",
        token=token,
    )


@app.route("/mobile/<token>/upload", methods=["POST"])
@mobile_token_required
def mobile_upload(token: str):

    if "files" not in request.files:
        return jsonify({
            "error": "No files provided"
        }), 400

    uploaded, errors = _handle_upload_request()

    if not uploaded and errors:
        return jsonify({
            "error": errors[0],
            "all_errors": errors,
        }), 400

    media = storage_list()

    return jsonify({
        "uploaded": uploaded,
        "errors": errors,
        "photos": media["photos"],
        "videos": media["videos"],
        "photo_urls": {
            f: storage_url(f)
            for f in media["photos"]
        },
        "video_urls": {
            f: storage_url(f)
            for f in media["videos"]
        },
        "video_mime_types": {
            f: get_content_type(f)
            for f in media["videos"]
        },
    })


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":

    if using_r2():
        print(f"Storage backend: Cloudflare R2 (bucket: {R2_BUCKET})")
    else:
        print("Storage backend: Local Disk")
        if R2_ACCOUNT_ID or R2_ACCESS_KEY_ID or R2_SECRET_ACCESS_KEY or R2_PUBLIC_URL:
            print("WARNING: R2 is partially configured but missing required settings. Falling back to local storage.")
            if not R2_BUCKET:
                print("Missing env var: R2_BUCKET_NAME or R2_BUCKET")
            if not R2_ACCOUNT_ID:
                print("Missing env var: R2_ACCOUNT_ID or R2_ACCOUNT")
            if not R2_ACCESS_KEY_ID:
                print("Missing env var: R2_ACCESS_KEY_ID or R2_ACCESS_KEY")
            if not R2_SECRET_ACCESS_KEY:
                print("Missing env var: R2_SECRET_ACCESS_KEY or R2_SECRET_KEY")

    app.run(
        debug=True,
        host="0.0.0.0",
        port=5000,
    )

