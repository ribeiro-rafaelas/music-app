import logging
import os
import shlex
import subprocess
import tempfile
import uuid
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request, send_file

from process_score import process_musicxml_file

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)

# External tools
AUDIVERIS_CLI = os.environ.get("AUDIVERIS_CLI")  # Path to audiveris-cli jar or executable

# MuseScore: read from env, but fall back to common Windows install path if missing
_default_ms_paths = [
    r"C:\Program Files\MuseScore 4\bin\MuseScore4.exe",
    r"C:\Program Files\MuseScore Studio 4\bin\MuseScore4.exe",
]
_musescore_env = os.environ.get("MUSESCORE_PATH")
if _musescore_env and os.path.exists(_musescore_env):
    MUSESCORE_PATH = _musescore_env
else:
    MUSESCORE_PATH = next((p for p in _default_ms_paths if os.path.exists(p)), None)
    if _musescore_env and not MUSESCORE_PATH:
        logging.warning("MUSESCORE_PATH is set but was not found on disk: %s", _musescore_env)
AUDIVERIS_TIMEOUT = int(os.environ.get("AUDIVERIS_TIMEOUT", "360"))

app = Flask(__name__, static_folder="static", template_folder="templates")


def run_audiveris(pdf_path: Path, output_dir: Path) -> Path:
    """Run Audiveris CLI to convert PDF into MusicXML. Returns path to the MusicXML."""
    if not AUDIVERIS_CLI:
        raise RuntimeError(
            "AUDIVERIS_CLI environment variable is not set. Point it to audiveris-cli.jar or the audiveris executable."
        )

    output_dir.mkdir(parents=True, exist_ok=True)

    # Build command; support jar path or direct executable
    if AUDIVERIS_CLI.lower().endswith(".jar"):
        cmd = ["java", "-jar", AUDIVERIS_CLI, "-batch", "-export", "-output", str(output_dir), str(pdf_path)]
    else:
        cmd = shlex.split(AUDIVERIS_CLI) + ["-batch", "-export", "-output", str(output_dir), str(pdf_path)]

    logging.info("Running Audiveris: %s", " ".join(cmd))
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=AUDIVERIS_TIMEOUT,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Audiveris failed (exit {proc.returncode}): {proc.stderr.decode(errors='ignore')}")

    xml_candidates = list(output_dir.glob("*.musicxml")) + list(output_dir.glob("*.xml")) + list(output_dir.glob("*.mxl"))
    if not xml_candidates:
        raise RuntimeError("Audiveris did not produce a MusicXML file. Check the input PDF quality and CLI path.")

    return xml_candidates[0]


def build_score_from_pdf(pdf_file) -> dict:
    """Pipeline: save PDF, run OMR, insert chords, return metadata and download token."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="score_work_"))
    pdf_path = tmp_dir / "input.pdf"
    pdf_file.save(pdf_path)

    audiveris_out = tmp_dir / "audiveris"
    musicxml_path = run_audiveris(pdf_path, audiveris_out)

    token = uuid.uuid4().hex
    output_pdf = OUTPUT_DIR / f"{token}.pdf"
    output_xml = tmp_dir / "with_chords.musicxml"

    meta = process_musicxml_file(
        input_path=musicxml_path,
        out_xml=output_xml,
        out_pdf=output_pdf,
        musescore_path=MUSESCORE_PATH,
    )

    if not meta.get("pdf_written") or not output_pdf.exists():
        raise RuntimeError("PDF export failed. Verify MuseScore is installed and MUSESCORE_PATH is set.")

    return {
        "token": token,
        "output_pdf": output_pdf,
        "meta": meta,
    }


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/api/annotate", methods=["POST"])
def annotate():
    if "score" not in request.files:
        return jsonify({"error": "Upload a PDF file using the 'score' field."}), 400
    score_file = request.files["score"]
    if score_file.filename == "":
        return jsonify({"error": "No file selected."}), 400
    if not score_file.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Only PDF files are accepted."}), 400

    try:
        result = build_score_from_pdf(score_file)
    except Exception as exc:
        logging.exception("Processing failed")
        return jsonify({"error": str(exc)}), 500

    download_url = f"/download/{result['token']}"
    return jsonify(
        {
            "downloadUrl": download_url,
            "key": result["meta"].get("key"),
            "measures": result["meta"].get("measures"),
            "uncertain_measures": result["meta"].get("uncertain_measures"),
        }
    )


@app.route("/download/<token>", methods=["GET"])
def download(token):
    pdf_path = OUTPUT_DIR / f"{token}.pdf"
    if not pdf_path.exists():
        abort(404)
    return send_file(pdf_path, as_attachment=True, download_name="partitura_com_cifras.pdf", mimetype="application/pdf")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
