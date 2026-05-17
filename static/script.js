/**
 * Memories of Us: Misio i Misia — script.js
 *
 * Performance notes:
 *  - No createObjectURL for videos (avoids browser media probing lag).
 *  - Photos get lazy object URLs via IntersectionObserver.
 *  - Above SUMMARY_THRESHOLD staged files, a count badge replaces individual rows.
 *  - File deduplication uses a Map (O(1)) not Array.find (O(n^2)).
 *  - File processing is deferred via setTimeout so the browser paints first.
 *  - Uploads are chunked (CHUNK_SIZE per request).
 *  - Gallery updates are incremental (prepend only, no full rebuild on upload).
 */

const CHUNK_SIZE       = 20;  // files per upload request — raise for fewer round-trips
const SUMMARY_THRESHOLD = 8;

/* ============================================
   FLOATING HEARTS
   ============================================ */
(function () {
  const container = document.getElementById('floating-hearts');
  if (!container) return;
  for (let i = 0; i < 12; i++) {
    const h = document.createElement('span');
    h.classList.add('floating-heart');
    h.textContent = i % 3 === 0 ? '\u2764' : '\u2665';
    h.style.left = Math.random() * 100 + '%';
    h.style.fontSize = (0.7 + Math.random() * 0.9) + 'rem';
    const dur = 9 + Math.random() * 10;
    h.style.animationDuration = dur + 's';
    h.style.animationDelay = (Math.random() * dur) + 's';
    container.appendChild(h);
  }
})();

/* ============================================
   TOAST
   ============================================ */
function showToast(message, isError) {
  isError = isError || false;
  var toast = document.getElementById('app-toast');
  if (!toast) {
    toast = document.createElement('div');
    toast.id = 'app-toast';
    toast.className = 'toast';
    toast.setAttribute('role', 'status');
    toast.setAttribute('aria-live', 'polite');
    document.body.appendChild(toast);
  }
  toast.textContent = message;
  toast.classList.toggle('error', isError);
  toast.classList.add('show');
  clearTimeout(toast._timeout);
  toast._timeout = setTimeout(function () { toast.classList.remove('show'); }, 3200);
}

/* ============================================
   CHUNKED UPLOAD
   ============================================ */
function uploadInChunks(files, url, onProgress) {
  var total = files.length;
  var uploaded = [], errors = [], lastMedia = { photos: [], videos: [] };
  var chunks = prepareChunks(files);
  var index = 0;
  var processed = 0;

  function prepareChunks(files) {
    var maxChunkBytes = 300 * 1024 * 1024; // 300MB per request
    var chunked = [];
    var current = [];
    var currentBytes = 0;

    files.forEach(function (f) {
      var size = f.size || 0;
      if (current.length && (current.length >= CHUNK_SIZE || currentBytes + size > maxChunkBytes)) {
        chunked.push(current);
        current = [];
        currentBytes = 0;
      }
      if (size > maxChunkBytes) {
        if (current.length) {
          chunked.push(current);
          current = [];
          currentBytes = 0;
        }
        chunked.push([f]);
      } else {
        current.push(f);
        currentBytes += size;
      }
    });

    if (current.length) chunked.push(current);
    return chunked;
  }

  function doChunk() {
    if (index >= chunks.length) {
      return Promise.resolve({ uploaded: uploaded, errors: errors, photos: lastMedia.photos, videos: lastMedia.videos });
    }
    var chunk = chunks[index];
    var fd = new FormData();
    chunk.forEach(function (f) { fd.append('files', f); });
    var chunkStart = processed;
    index += 1;

    return new Promise(function (resolve, reject) {
      var xhr = new XMLHttpRequest();
      xhr.open('POST', url);
      xhr.upload.addEventListener('progress', function (e) {
        if (e.lengthComputable)
          onProgress(chunkStart + chunk.length * (e.loaded / e.total), total);
      });
      xhr.addEventListener('load', function () {
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(JSON.parse(xhr.responseText));
        } else {
          try { reject(JSON.parse(xhr.responseText)); }
          catch (ex) { reject({ error: 'HTTP ' + xhr.status }); }
        }
      });
      xhr.addEventListener('error', function () { reject({ error: 'Network error' }); });
      xhr.send(fd);
    }).then(function (result) {
      uploaded = uploaded.concat(result.uploaded || []);
      errors   = errors.concat(result.errors   || []);
      lastMedia = { photos: result.photos, videos: result.videos };
      processed += chunk.length;
      onProgress(Math.min(chunkStart + chunk.length, total), total);
      return doChunk();
    });
  }

  return doChunk();
}

/* ============================================
   LAZY PHOTO THUMBNAIL
   Only photos get object URLs — never videos.
   ============================================ */
var _thumbObserver = ('IntersectionObserver' in window)
  ? new IntersectionObserver(function (entries, obs) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        var el = entry.target;
        var file = el._pendingFile;
        if (!file) return;
        obs.unobserve(el);
        delete el._pendingFile;
        var url = URL.createObjectURL(file);
        var img = document.createElement('img');
        img.alt = file.name;
        img.onload = function () { URL.revokeObjectURL(url); };
        img.src = url;
        var ph = el.querySelector('.thumb-placeholder');
        if (ph) ph.replaceWith(img); else el.appendChild(img);
      });
    }, { rootMargin: '200px' })
  : null;

/* ============================================
   QR UPLOAD MODAL
   ============================================ */
(function () {
  var qrTrigger = document.getElementById('qr-trigger-btn');
  var qrModal   = document.getElementById('qr-modal');
  var qrClose   = document.getElementById('qr-modal-close');
  var qrSpinner = document.getElementById('qr-spinner');
  var qrContent = document.getElementById('qr-content');
  var qrImage   = document.getElementById('qr-image');
  var qrUrl     = document.getElementById('qr-url');
  var qrExpiry  = document.getElementById('qr-expiry');
  var qrRefresh = document.getElementById('qr-refresh-btn');

  if (!qrTrigger) return;

  var expiryTimer = null, expiresAt = null;

  qrTrigger.addEventListener('click', openQrModal);
  qrClose.addEventListener('click', closeQrModal);
  qrModal.addEventListener('click', function (e) { if (e.target === qrModal) closeQrModal(); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && !qrModal.hidden) closeQrModal(); });
  qrRefresh.addEventListener('click', loadQr);

  function openQrModal() {
    qrModal.hidden = false;
    document.body.style.overflow = 'hidden';
    loadQr();
  }
  function closeQrModal() {
    qrModal.hidden = true;
    document.body.style.overflow = '';
    clearInterval(expiryTimer);
  }

  function loadQr() {
    qrSpinner.hidden = false;
    qrContent.hidden = true;
    qrRefresh.hidden = true;
    clearInterval(expiryTimer);

    fetch(window.QR_URL)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        qrImage.src = data.qr_image;
        qrUrl.textContent = data.mobile_url;
        expiresAt = Date.now() + data.expires_in * 1000;
        qrSpinner.hidden = true;
        qrContent.hidden = false;
        qrRefresh.hidden = false;
        updateExpiry();
        expiryTimer = setInterval(updateExpiry, 10000);
      })
      .catch(function () {
        qrSpinner.hidden = true;
        qrExpiry.textContent = 'Could not generate QR code. Is the server running?';
        qrContent.hidden = false;
      });
  }

  function updateExpiry() {
    if (!expiresAt) return;
    var mins = Math.max(0, Math.round((expiresAt - Date.now()) / 60000));
    if (mins <= 0) {
      qrExpiry.textContent = 'This code has expired \u2014 generate a new one.';
      clearInterval(expiryTimer);
    } else {
      qrExpiry.textContent = 'Expires in ~' + mins + ' minute' + (mins !== 1 ? 's' : '');
    }
  }
})();

/* ============================================
   DESKTOP UPLOAD MODAL
   ============================================ */
(function () {
  var triggerBtn   = document.getElementById('upload-trigger-btn');
  var modal        = document.getElementById('upload-modal');
  var closeBtn     = document.getElementById('modal-close');
  var dropZone     = document.getElementById('drop-zone');
  var fileInput    = document.getElementById('file-input');
  var queue        = document.getElementById('upload-queue');
  var submitBtn    = document.getElementById('upload-submit-btn');
  var progressWrap = document.getElementById('upload-progress-wrap');
  var progressBar  = document.getElementById('upload-progress-bar');
  var progressLbl  = document.getElementById('upload-progress-label');

  if (!triggerBtn) return;

  // Map<"name|size", File> — O(1) dedup
  var stagedMap = new Map();

  triggerBtn.addEventListener('click', openModal);
  closeBtn.addEventListener('click', closeModal);
  modal.addEventListener('click', function (e) { if (e.target === modal) closeModal(); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && !modal.hidden) closeModal(); });

  function openModal() {
    modal.hidden = false;
    document.body.style.overflow = 'hidden';
    dropZone.focus();
  }
  function closeModal() {
    modal.hidden = true;
    document.body.style.overflow = '';
    stagedMap.clear();
    renderQueue();
  }

  dropZone.addEventListener('click', function () { fileInput.click(); });
  dropZone.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') fileInput.click(); });
  dropZone.addEventListener('dragover', function (e) { e.preventDefault(); dropZone.classList.add('drag-over'); });
  dropZone.addEventListener('dragleave', function () { dropZone.classList.remove('drag-over'); });
  dropZone.addEventListener('drop', function (e) {
    e.preventDefault();
    dropZone.classList.remove('drag-over');
    scheduleAddFiles(Array.from(e.dataTransfer.files));
  });
  fileInput.addEventListener('change', function () {
    var files = Array.from(fileInput.files);
    fileInput.value = '';
    scheduleAddFiles(files);
  });

  function scheduleAddFiles(files) {
    if (!files.length) return;
    // Show instant feedback, then yield so browser can paint before processing
    submitBtn.disabled = true;
    queue.innerHTML =
      '<li class="upload-queue-item queue-summary">' +
      '<span class="q-icon">\u23F3</span>' +
      '<span class="q-name">Processing ' + files.length + ' file' + (files.length !== 1 ? 's' : '') + '\u2026</span>' +
      '</li>';
    setTimeout(function () { addFiles(files); }, 0);
  }

  function addFiles(files) {
    var rejected = 0;
    files.forEach(function (f) {
      var isZip   = f.name.toLowerCase().endsWith('.zip') || f.type.indexOf('zip') !== -1;
      var isMedia = f.type.indexOf('image/') === 0 || f.type.indexOf('video/') === 0;
      if (!isZip && !isMedia) { rejected++; return; }
      var key = f.name + '|' + f.size;
      if (!stagedMap.has(key)) stagedMap.set(key, f);
    });
    if (rejected) showToast(rejected + ' unsupported file' + (rejected !== 1 ? 's' : '') + ' skipped', true);
    renderQueue();
  }

  function removeFile(key) {
    stagedMap.delete(key);
    renderQueue();
  }

  function formatBytes(b) {
    if (b < 1024) return b + ' B';
    if (b < 1048576) return (b / 1024).toFixed(1) + ' KB';
    return (b / 1048576).toFixed(1) + ' MB';
  }

  function renderQueue() {
    var files = Array.from(stagedMap.values());
    var frag  = document.createDocumentFragment();

    if (files.length === 0) {
      // empty — nothing to render
    } else if (files.length > SUMMARY_THRESHOLD) {
      var zips   = files.filter(function (f) { return f.name.toLowerCase().endsWith('.zip'); }).length;
      var videos = files.filter(function (f) { return f.type.indexOf('video/') === 0; }).length;
      var photos = files.length - zips - videos;
      var parts  = [];
      if (photos) parts.push('\uD83D\uDCF7 ' + photos);
      if (videos) parts.push('\uD83C\uDFAC ' + videos);
      if (zips)   parts.push('\uD83D\uDDDC ' + zips);
      var li = document.createElement('li');
      li.className = 'upload-queue-item queue-summary';
      li.innerHTML =
        '<span class="q-icon" aria-hidden="true">\uD83D\uDCE6</span>' +
        '<span class="q-name"><strong>' + files.length + ' files ready</strong>' +
        '<span class="q-zip-hint"> \u2014 ' + parts.join('  ') + '</span></span>' +
        '<button class="q-remove q-clear-all" aria-label="Clear all files">Clear all</button>';
      frag.appendChild(li);
    } else {
      files.forEach(function (f) {
        var isVid = f.type.indexOf('video/') === 0;
        var isZip = f.name.toLowerCase().endsWith('.zip');
        var icon  = isZip ? '\uD83D\uDDDC' : isVid ? '\uD83C\uDFAC' : '\uD83D\uDCF7';
        var hint  = isZip ? '<span class="q-zip-hint"> \u00B7 will be extracted</span>' : '';
        var li = document.createElement('li');
        li.className = 'upload-queue-item';
        li.dataset.key = f.name + '|' + f.size;
        li.innerHTML =
          '<span class="q-icon" aria-hidden="true">' + icon + '</span>' +
          '<span class="q-name" title="' + f.name + '">' + f.name + hint + '</span>' +
          '<span class="q-size">' + formatBytes(f.size) + '</span>' +
          '<button class="q-remove" aria-label="Remove ' + f.name + '">&times;</button>';
        frag.appendChild(li);
      });
    }

    queue.innerHTML = '';
    queue.appendChild(frag);

    queue.onclick = function (e) {
      var btn = e.target.closest('.q-remove');
      if (!btn) return;
      if (btn.classList.contains('q-clear-all')) {
        stagedMap.clear(); renderQueue();
      } else {
        var li = btn.closest('li');
        if (li && li.dataset.key) removeFile(li.dataset.key);
      }
    };

    submitBtn.disabled = files.length === 0;
  }

  submitBtn.addEventListener('click', doUpload);

  function doUpload() {
    var files = Array.from(stagedMap.values());
    if (!files.length) return;

    submitBtn.disabled = true;
    progressWrap.hidden = false;
    progressBar.style.width = '0%';
    progressLbl.textContent = 'Uploading ' + files.length + ' file' + (files.length !== 1 ? 's' : '') + '…';

    sendFiles(files).then(onSuccess).catch(onError);

    function sendFiles(filesToSend) {
      var total = filesToSend.length;
      return uploadInChunks(filesToSend, window.UPLOAD_URL, function (done, tot) {
        var pct = (done / tot) * 100;
        progressBar.style.width = pct + '%';
        progressBar.setAttribute('aria-valuenow', Math.round(pct));
        progressLbl.textContent = 'Uploading ' + Math.min(Math.ceil(done), tot) + ' / ' + tot;
      });
    }

    function onSuccess(result) {
      progressBar.style.width = '100%';
      var count = result.uploaded.length;
      progressLbl.textContent = '✓ ' + count + ' file' + (count !== 1 ? 's' : '') + ' added!';
      mergeUrlMaps(result);
      prependToGallery(result.photos, result.videos);
      showToast(
        result.errors.length
          ? count + ' added, ' + result.errors.length + ' skipped'
          : count + ' memor' + (count !== 1 ? 'ies' : 'y') + ' added ❤️',
        result.errors.length > 0
      );
      setTimeout(function () {
        closeModal();
        progressWrap.hidden = true;
        progressBar.style.width = '0%';
      }, 900);
    }

    function onError(err) {
      progressLbl.textContent = '✗ Upload failed';
      showToast((err && err.error) ? err.error : 'Upload failed', true);
      submitBtn.disabled = false;
    }
  }
})();

/* ============================================
   GALLERY — incremental DOM updates
   ============================================ */

// URL lookup: prefer server-provided maps, fall back to /media/ route
function mediaUrl(filename) {
  var urls = window.PHOTO_URLS || {};
  var vurls = window.VIDEO_URLS || {};
  if (urls[filename]) return urls[filename];
  if (vurls[filename]) return vurls[filename];
  // Local fallback
  return window.MEDIA_BASE.replace('__FILENAME__', encodeURIComponent(filename));
}

function deleteUrl(f) { return window.DELETE_BASE.replace('__FILENAME__', encodeURIComponent(f)); }

// Merge fresh URL maps from a server response into the global caches
function mergeUrlMaps(result) {
  if (result.photo_urls) Object.assign(window.PHOTO_URLS, result.photo_urls);
  if (result.video_urls) Object.assign(window.VIDEO_URLS, result.video_urls);
  if (result.video_poster_urls) Object.assign(window.VIDEO_POSTER_URLS, result.video_poster_urls);
  if (result.video_mime_types) Object.assign(window.VIDEO_MIME_TYPES, result.video_mime_types);
}

function createPhotoItem(filename) {
  var div = document.createElement('div');
  div.className = 'gallery-item';
  div.setAttribute('role', 'listitem');
  div.dataset.filename = filename;

  var img = document.createElement('img');
  img.src = mediaUrl(filename);
  img.alt = 'Memory \u2014 ' + filename;
  img.loading = 'lazy';

  var btn = document.createElement('button');
  btn.className = 'delete-btn';
  btn.setAttribute('aria-label', 'Delete ' + filename);
  btn.dataset.filename = filename;
  btn.dataset.type = 'photo';
  btn.textContent = '\u00D7';

  div.appendChild(img);
  div.appendChild(btn);
  attachItemListeners(div);
  return div;
}

function createVideoItem(filename) {
  var div = document.createElement('div');
  div.className = 'gallery-item placeholder-video';
  div.setAttribute('role', 'listitem');
  div.dataset.filename = filename;

  var video = document.createElement('video');
  video.controls = true;
  video.preload = 'none';
  video.setAttribute('aria-label', 'Video memory \u2014 ' + filename);

  var source = document.createElement('source');
  source.src = mediaUrl(filename);
  source.type = window.VIDEO_MIME_TYPES && window.VIDEO_MIME_TYPES[filename]
    ? window.VIDEO_MIME_TYPES[filename]
    : 'video/mp4';
  video.appendChild(source);

  if (window.VIDEO_POSTER_URLS && window.VIDEO_POSTER_URLS[filename]) {
    video.poster = window.VIDEO_POSTER_URLS[filename];
  }

  var btn = document.createElement('button');
  btn.className = 'delete-btn';
  btn.setAttribute('aria-label', 'Delete ' + filename);
  btn.dataset.filename = filename;
  btn.dataset.type = 'video';
  btn.textContent = '\u00D7';

  div.appendChild(video);
  div.appendChild(btn);
  attachItemListeners(div);
  return div;
}

function prependToGallery(photos, videos) {
  var photoGrid = document.getElementById('photo-grid');
  var videoGrid = document.getElementById('video-grid');

  var renderedPhotos = new Set(Array.from(photoGrid ? photoGrid.querySelectorAll('.gallery-item') : []).map(function (el) { return el.dataset.filename; }));
  var renderedVideos = new Set(Array.from(videoGrid ? videoGrid.querySelectorAll('.gallery-item') : []).map(function (el) { return el.dataset.filename; }));

  if (photoGrid) {
    var newPhotos = photos.filter(function (f) { return !renderedPhotos.has(f); });
    if (newPhotos.length) {
      var es = photoGrid.querySelector('.empty-state');
      if (es) es.remove();
      var frag = document.createDocumentFragment();
      newPhotos.forEach(function (f) { frag.appendChild(createPhotoItem(f)); });
      photoGrid.insertBefore(frag, photoGrid.firstChild);
    }
    if (!photos.length) photoGrid.innerHTML = '<div class="empty-state"><span class="empty-icon" aria-hidden="true">\uD83D\uDCF7</span><p>No photos yet \u2014 click <strong>Add Memories</strong> to upload your first one.</p></div>';
  }

  if (videoGrid) {
    var newVideos = videos.filter(function (f) { return !renderedVideos.has(f); });
    if (newVideos.length) {
      var es2 = videoGrid.querySelector('.empty-state');
      if (es2) es2.remove();
      var frag2 = document.createDocumentFragment();
      newVideos.forEach(function (f) { frag2.appendChild(createVideoItem(f)); });
      videoGrid.insertBefore(frag2, videoGrid.firstChild);
    }
    if (!videos.length) videoGrid.innerHTML = '<div class="empty-state"><span class="empty-icon" aria-hidden="true">\uD83C\uDFAC</span><p>No videos yet \u2014 click <strong>Add Memories</strong> to upload your first one.</p></div>';
  }
}

function refreshGallery(photos, videos) {
  var photoGrid = document.getElementById('photo-grid');
  var videoGrid = document.getElementById('video-grid');

  if (photoGrid) {
    if (!photos.length) {
      photoGrid.innerHTML = '<div class="empty-state"><span class="empty-icon" aria-hidden="true">\uD83D\uDCF7</span><p>No photos yet \u2014 click <strong>Add Memories</strong> to upload your first one.</p></div>';
    } else {
      var frag = document.createDocumentFragment();
      photos.forEach(function (f) { frag.appendChild(createPhotoItem(f)); });
      photoGrid.innerHTML = '';
      photoGrid.appendChild(frag);
    }
  }

  if (videoGrid) {
    if (!videos.length) {
      videoGrid.innerHTML = '<div class="empty-state"><span class="empty-icon" aria-hidden="true">\uD83C\uDFAC</span><p>No videos yet \u2014 click <strong>Add Memories</strong> to upload your first one.</p></div>';
    } else {
      var frag2 = document.createDocumentFragment();
      videos.forEach(function (f) { frag2.appendChild(createVideoItem(f)); });
      videoGrid.innerHTML = '';
      videoGrid.appendChild(frag2);
    }
  }
}

/* ============================================
   DELETE
   ============================================ */
function attachItemListeners(item) {
  var btn = item.querySelector('.delete-btn');
  if (btn) {
    btn.addEventListener('click', function (e) {
      e.stopPropagation();
      deleteItem(btn.dataset.filename, btn.dataset.type);
    });
  }
  var img = item.querySelector('img');
  if (img) {
    item.addEventListener('click', function (e) {
      if (e.target !== btn) openLightbox(img.src);
    });
  }
}

function deleteItem(filename, type) {
  if (!confirm('Remove this ' + type + ' from your memories?')) return;
  fetch(deleteUrl(filename), {
    method: 'DELETE',
    credentials: 'same-origin',
  })
    .then(function (res) {
      if (!res.ok) throw new Error();
      return res.json();
    })
    .then(function (data) {
      mergeUrlMaps(data);
      refreshGallery(data.photos, data.videos);
      showToast('Memory removed');
    })
    .catch(function () {
      showToast('Could not delete file', true);
    });
}

document.querySelectorAll('.gallery-item').forEach(attachItemListeners);

/* ============================================
   LIGHTBOX
   ============================================ */
(function () {
  var overlay = null;

  window.openLightbox = function (src) {
    if (!overlay) {
      overlay = document.createElement('div');
      overlay.className = 'lightbox-overlay';
      overlay.setAttribute('role', 'dialog');
      overlay.setAttribute('aria-modal', 'true');
      overlay.setAttribute('aria-label', 'Photo viewer');
      overlay.hidden = true;

      var img = document.createElement('img');
      img.className = 'lightbox-img';
      img.alt = 'Full size memory';

      var closeBtn = document.createElement('button');
      closeBtn.className = 'lightbox-close';
      closeBtn.setAttribute('aria-label', 'Close photo viewer');
      closeBtn.textContent = '\u00D7';
      closeBtn.addEventListener('click', closeLightbox);

      overlay.appendChild(img);
      overlay.appendChild(closeBtn);
      overlay.addEventListener('click', function (e) { if (e.target === overlay) closeLightbox(); });
      document.body.appendChild(overlay);
      document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && !overlay.hidden) closeLightbox(); });
    }

    overlay.querySelector('.lightbox-img').src = src;
    overlay.hidden = false;
    document.body.style.overflow = 'hidden';
  };

  function closeLightbox() {
    if (overlay) { overlay.hidden = true; document.body.style.overflow = ''; }
  }
})();
