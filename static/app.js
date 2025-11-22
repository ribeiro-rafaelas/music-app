const fileInput = document.getElementById('fileInput');
const dropzone = document.getElementById('dropzone');
const uploadForm = document.getElementById('uploadForm');
const statusEl = document.getElementById('status');
const submitBtn = document.getElementById('submitBtn');
const downloadArea = document.getElementById('downloadArea');
const downloadLink = document.getElementById('downloadLink');
const downloadMeta = document.getElementById('downloadMeta');

function setStatus(text, isError = false) {
    statusEl.textContent = text;
    statusEl.classList.toggle('error', isError);
}

function showDownload(url, meta) {
    downloadLink.href = url;
    const pieces = [];
    if (meta.key) pieces.push(`Key: ${meta.key}`);
    if (meta.measures) pieces.push(`Measures: ${meta.measures}`);
    if (meta.uncertain_measures) pieces.push(`Uncertain: ${meta.uncertain_measures}`);
    downloadMeta.textContent = pieces.join(' | ');
    downloadArea.hidden = false;
}

dropzone.addEventListener('click', () => fileInput.click());

['dragenter', 'dragover'].forEach(evt =>
    dropzone.addEventListener(evt, e => {
        e.preventDefault();
        e.stopPropagation();
        dropzone.classList.add('dragging');
    })
);

['dragleave', 'drop'].forEach(evt =>
    dropzone.addEventListener(evt, e => {
        e.preventDefault();
        e.stopPropagation();
        dropzone.classList.remove('dragging');
    })
);

dropzone.addEventListener('drop', e => {
    const [file] = e.dataTransfer.files;
    if (file) {
        fileInput.files = e.dataTransfer.files;
        setStatus(`Ready to process: ${file.name}`);
    }
});

uploadForm.addEventListener('submit', async e => {
    e.preventDefault();
    if (!fileInput.files.length) {
        setStatus('Select a PDF first.', true);
        return;
    }

    const file = fileInput.files[0];
    if (!file.name.toLowerCase().endsWith('.pdf')) {
        setStatus('Only PDF files are accepted.', true);
        return;
    }

    submitBtn.disabled = true;
    setStatus('Running OMR and chord analysis...');
    downloadArea.hidden = true;

    const data = new FormData();
    data.append('score', file);

    try {
        const response = await fetch('/api/annotate', { method: 'POST', body: data });
        const payload = await response.json().catch(() => ({}));

        if (!response.ok) {
            throw new Error(payload.error || 'Processing failed. Check server logs.');
        }

        setStatus('Success. Chord part added above the staff.');
        showDownload(payload.downloadUrl, payload);
    } catch (err) {
        setStatus(err.message, true);
    } finally {
        submitBtn.disabled = false;
    }
});
