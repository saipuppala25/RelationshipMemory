import { createFFmpeg } from '/static/ffmpeg.min.js';

const ffmpeg = createFFmpeg({
  log: true,
  corePath: '/static/ffmpeg-core.js',
});

const MAX_FILE_SIZE = 500 * 1024 * 1024; // 500MB
const CHUNK_CLEANUP_DELAY = 100;

let ffmpegLoaded = false;
let currentlyProcessing = false;

// -----------------------------------------------------------------------------
// Load FFmpeg Once
// -----------------------------------------------------------------------------

async function ensureFfmpegLoaded() {
  if (ffmpegLoaded) {
    return;
  }

  self.postMessage({
    type: 'status',
    message: 'Loading FFmpeg core...'
  });

  await ffmpeg.load();

  ffmpegLoaded = true;

  self.postMessage({
    type: 'status',
    message: 'FFmpeg loaded successfully'
  });
}

// -----------------------------------------------------------------------------
// Logging
// -----------------------------------------------------------------------------

ffmpeg.setLogger(({ type, message }) => {
  self.postMessage({
    type: 'log',
    logType: type,
    message,
  });
});

ffmpeg.setProgress(({ ratio, time }) => {

  const percent = Math.max(
    0,
    Math.min(100, Math.round(ratio * 100))
  );

  console.log(
    `[FFMPEG PROGRESS] ${percent}% complete`
  );

  self.postMessage({
    type: 'progress',
    progress: percent,
    time,
  });
});

// -----------------------------------------------------------------------------
// Utilities
// -----------------------------------------------------------------------------

function makeInputName(fileName) {
  const extension = fileName.split('.').pop() || 'mov';

  return (
    'input_' +
    Math.random().toString(36).slice(2) +
    '.' +
    extension
  );
}

function makeOutputName() {
  return (
    'output_' +
    Math.random().toString(36).slice(2) +
    '.mp4'
  );
}

function cleanupFiles(files) {
  for (const file of files) {
    try {
      ffmpeg.FS('unlink', file);
    } catch (err) {
      // Ignore cleanup errors
    }
  }
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

// -----------------------------------------------------------------------------
// Smart Encoding Strategy
// -----------------------------------------------------------------------------

async function tryFastRemux(inputName, outputName) {

  self.postMessage({
    type: 'status',
    message: 'Attempting fast video remux...'
  });

  await ffmpeg.run(
    '-i', inputName,

    // Copy video stream directly if compatible
    '-c:v', 'copy',

    // Re-encode audio for compatibility
    '-c:a', 'aac',
    '-b:a', '128k',

    // Optimize playback
    '-movflags', '+faststart',

    outputName
  );
}

async function fallbackEncode(inputName, outputName) {

  self.postMessage({
    type: 'status',
    message: 'Using fallback encoding mode...'
  });

  await ffmpeg.run(
    '-i', inputName,

    // Faster encoding preset
    '-c:v', 'libx264',
    '-preset', 'ultrafast',

    // Lower quality for speed/stability
    '-crf', '28',

    // Browser compatibility
    '-pix_fmt', 'yuv420p',

    // Optimize web playback
    '-movflags', '+faststart',

    // Audio
    '-c:a', 'aac',
    '-b:a', '96k',

    outputName
  );
}

// -----------------------------------------------------------------------------
// Main Worker
// -----------------------------------------------------------------------------

self.onmessage = async function (event) {

  const data = event.data;

  if (!data || data.type !== 'convert') {
    return;
  }

  // Prevent multiple simultaneous FFmpeg runs
  if (currentlyProcessing) {
    self.postMessage({
      type: 'error',
      id: data.id,
      error: 'FFmpeg is already processing another video'
    });

    return;
  }

  currentlyProcessing = true;

  const {
    id,
    fileName,
    fileBuffer,
  } = data;

  const inputName = makeInputName(fileName);
  const outputName = makeOutputName();

  try {

    // -------------------------------------------------------------------------
    // Validate File Size
    // -------------------------------------------------------------------------

    if (fileBuffer.byteLength > MAX_FILE_SIZE) {
      throw new Error(
        'Video exceeds 500MB browser conversion limit. ' +
        'Upload directly to the server instead.'
      );
    }

    self.postMessage({
      type: 'status',
      message: 'Preparing video conversion...'
    });

    // -------------------------------------------------------------------------
    // Load FFmpeg
    // -------------------------------------------------------------------------

    await ensureFfmpegLoaded();

    // -------------------------------------------------------------------------
    // Write File To Virtual FS
    // -------------------------------------------------------------------------

    self.postMessage({
      type: 'status',
      message: 'Writing video into memory...'
    });

    ffmpeg.FS(
      'writeFile',
      inputName,
      new Uint8Array(fileBuffer)
    );

    await sleep(CHUNK_CLEANUP_DELAY);

    // -------------------------------------------------------------------------
    // Attempt Fast Remux
    // -------------------------------------------------------------------------

    let remuxWorked = false;

    try {

      await tryFastRemux(inputName, outputName);

      remuxWorked = true;

      self.postMessage({
        type: 'status',
        message: 'Fast remux completed successfully'
      });

    } catch (fastError) {

      self.postMessage({
        type: 'status',
        message: 'Fast remux failed, using fallback encoder'
      });

      cleanupFiles([outputName]);

      await sleep(CHUNK_CLEANUP_DELAY);
    }

    // -------------------------------------------------------------------------
    // Fallback Encoding
    // -------------------------------------------------------------------------

    if (!remuxWorked) {
      await fallbackEncode(inputName, outputName);
    }

    // -------------------------------------------------------------------------
    // Read Converted Output
    // -------------------------------------------------------------------------

    self.postMessage({
      type: 'status',
      message: 'Reading converted video...'
    });

    const result = ffmpeg.FS('readFile', outputName);

    const convertedBuffer = result.buffer.slice(0);

    // -------------------------------------------------------------------------
    // Return Converted Video
    // -------------------------------------------------------------------------

    self.postMessage(
      {
        type: 'converted',
        id,
        name: fileName.replace(/\.[^.]+$/i, '.mp4'),
        data: convertedBuffer,
      },
      [convertedBuffer]
    );

    self.postMessage({
      type: 'status',
      message: 'Conversion completed successfully'
    });

  } catch (error) {

    self.postMessage({
      type: 'error',
      id,
      error: error?.message || String(error),
    });

  } finally {

    // -------------------------------------------------------------------------
    // Cleanup
    // -------------------------------------------------------------------------

    cleanupFiles([
      inputName,
      outputName,
    ]);

    await sleep(CHUNK_CLEANUP_DELAY);

    currentlyProcessing = false;

    // Attempt memory cleanup
    try {
      if (typeof gc === 'function') {
        gc();
      }
    } catch (err) {
      // Ignore GC errors
    }
  }
};