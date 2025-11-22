#!/usr/bin/env python3
import argparse
import logging
import os
import sys
from music21 import converter, stream, note, chord, harmony, meter, key as m21key, environment

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')


def choose_melody_part(score):
    """Choose the part with the largest number of Note objects (heuristic for melody)."""
    parts = list(score.parts)
    if len(parts) == 0:
        raise ValueError("No parts found in score")
    best = None
    best_count = -1
    for p in parts:
        n_count = len(list(p.recurse().getElementsByClass(note.Note)))
        logging.debug(f"Part {p.id} has {n_count} notes")
        if n_count > best_count:
            best = p
            best_count = n_count
    logging.info(f"Selected part '{best.id if hasattr(best,'id') else best.partName or 'unnamed'}' as melody (notes={best_count})")
    return best


def analyze_measures(melody_part, score_key=None):
    """Return list of (measure, chordSymbol, uncertain_flag).
    chordSymbol is a music21.harmony.ChordSymbol or None.
    """
    results = []
    measures = list(melody_part.getElementsByClass(stream.Measure))
    if not measures:
        # try by measureNumber
        measures = [m for m in melody_part if isinstance(m, stream.Measure)]
    for meas in measures:
        # collect pitches sounding in this measure (notes and chord tones)
        pitches = []
        uncertain = False
        # Use measure.recurse() to pick up chord objects and notes
        for n in meas.recurse().getElementsByClass(note.Note):
            if n.pitch is None:
                uncertain = True
            else:
                pitches.append(n.pitch)
        for c in meas.recurse().getElementsByClass(chord.Chord):
            if len(c.pitches) == 0:
                uncertain = True
            else:
                pitches.extend(c.pitches)
        cs = None
        if len(pitches) > 0:
            try:
                ch = chord.Chord(pitches)
                # music21.harmony.chordSymbolFromChord may produce a chord symbol
                cs = harmony.chordSymbolFromChord(ch, forceTriad=False)
                # If the chord symbol is (None), try to force a simpler symbol
                if cs is None:
                    cs = harmony.chordSymbolFromChord(ch, forceTriad=True)
            except Exception as e:
                logging.debug(f"chordSymbolFromChord failed for measure {meas.measureNumber}: {e}")
                cs = None
        # fallback: use scale degree of downbeat note together with key to choose simple triad
        if cs is None and len(pitches) > 0 and score_key is not None:
            try:
                # choose the pitch that occurs on the downbeat (closest to offset 0)
                downbeat_notes = [n for n in meas.notes if abs(n.offset - 0) < 1e-8]
                ref_note = downbeat_notes[0] if downbeat_notes else meas.notes[0]
                from music21 import roman
                rn = roman.romanNumeralFromChord(chord.Chord([ref_note.pitch]), score_key)
                # convert RomanNumeral to chord symbol
                cs = harmony.ChordSymbol(figure=rn.figure)
            except Exception:
                cs = None
        # if still None, but there is at least one pitch, create a simple root triad name
        if cs is None and len(pitches) > 0:
            try:
                root = pitches[0].name
                cs = harmony.ChordSymbol(figure=root)
            except Exception:
                cs = None
        results.append((meas, cs, uncertain))
    return results


def insert_chords_into_score(score, melody_part, analysis_results):
    """Insert chord symbols into a new part placed before melody_part (so they appear above)."""
    chord_part = stream.Part()
    chord_part.id = 'Chords'
    # minimal staff lines so it doesn't draw a staff (or we can set small staff)
    chord_part.staffLines = 0
    # Carry over global attributes: time signature and key
    # Insert measures with chord symbols aligned with melody measures
    for meas, cs, uncertain in analysis_results:
        # create a measure in chord_part with same measureNumber and timeSignature
        new_m = stream.Measure(number=meas.measureNumber)
        ts = meas.timeSignature
        if ts is not None:
            new_m.timeSignature = ts
        k = meas.keySignature
        if k is not None:
            new_m.keySignature = k
        # create a ChordSymbol at offset 0 of this measure
        if cs is not None:
            cs_offset = 0.0
            cs_copy = harmony.ChordSymbol(cs.figure)
            if uncertain:
                # add a textual annotation
                cs_copy.addLyric('(uncertain)')
            new_m.insert(cs_offset, cs_copy)
        chord_part.append(new_m)
    # Place chord_part above melody_part: insert before in score.parts
    # We'll create a new score with chord_part plus all original parts, but keep melody part order.
    new_score = stream.Score()
    # insert chord_part first
    new_score.insert(0, chord_part)
    # then insert original parts preserving their order
    for p in score.parts:
        new_score.insert(0, p)  # insert preserves offsets
    return new_score


def process_musicxml_file(input_path, out_xml, out_pdf=None, musescore_path=None):
    """Process a MusicXML file, inject chord symbols, and optionally export PDF.

    Returns a small dict with metadata about the run.
    """
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Input file not found: {input_path}")

    logging.info('Parsing input MusicXML...')
    score = converter.parse(input_path)

    # try to determine global key (score-level)
    score_key = None
    try:
        ks = score.analyze('key')
        score_key = ks
        logging.info(f"Detected key: {ks}")
    except Exception:
        logging.info("Could not analyze key automatically; proceeding without strong key signal")

    melody_part = choose_melody_part(score)

    logging.info('Analyzing measures to generate chord suggestions...')
    analysis_results = analyze_measures(melody_part, score_key)

    logging.info('Inserting chord symbols into a new score...')
    final_score = insert_chords_into_score(score, melody_part, analysis_results)

    # Save MusicXML
    logging.info(f"Writing cleaned MusicXML to {out_xml}")
    final_score.write('musicxml', fp=out_xml)

    # Configure environment for PDF export if user supplied MuseScore
    if musescore_path:
        try:
            logging.info('Setting MuseScore path in music21 environment for PDF export')
            environment.set('musicxmlPath', musescore_path)
        except Exception as e:
            logging.warning(f"Could not set musescore path: {e}")

    pdf_written = False
    if out_pdf:
        logging.info(f"Attempting to write PDF to {out_pdf}")
        try:
            final_score.write('musicxml.pdf', fp=out_pdf)
            logging.info('PDF export succeeded')
            pdf_written = True
        except Exception as e:
            logging.error(
                f"PDF export failed: {e}\nIf you don't have MuseScore or LilyPond configured, "
                "install MuseScore and provide its executable path with --musescore."
            )

    return {
        "key": str(score_key) if score_key else None,
        "measures": len(analysis_results),
        "uncertain_measures": sum(1 for _, _, flag in analysis_results if flag),
        "pdf_written": pdf_written,
    }


def main():
    parser = argparse.ArgumentParser(description='Process MusicXML and add chord symbols per measure.')
    parser.add_argument('--input', '-i', required=True, help='Input MusicXML file')
    parser.add_argument('--out-xml', '-xo', required=True, help='Output cleaned MusicXML file (musicxml_limpo.xml)')
    parser.add_argument('--out-pdf', '-xp', required=True, help='Output PDF with chord symbols (partitura_com_cifras.pdf)')
    parser.add_argument('--musescore', '-m', required=False, help='Path to MuseScore executable for PDF export (optional)')
    args = parser.parse_args()

    if not os.path.exists(args.input):
        logging.error(f"Input file not found: {args.input}")
        sys.exit(1)

    process_musicxml_file(
        input_path=args.input,
        out_xml=args.out_xml,
        out_pdf=args.out_pdf,
        musescore_path=args.musescore,
    )

    logging.info('Done.')

if __name__ == '__main__':
    main()
