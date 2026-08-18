"""
Recolor Daggerheart character sheet PDF: remaps grayscale/black shades
to a chosen hue while preserving vector paths and text sharpness.

Uso:
    python3 recolor_pdf.py input.pdf output.pdf 0.6        # hue numerico (0-1)
    python3 recolor_pdf.py input.pdf output.pdf Blu        # nome colore (IT o EN)
    python3 recolor_pdf.py input.pdf output.pdf All        # genera tutte le varianti
                                                             # -> output_blue.pdf, output_red.pdf, ...
"""
import pikepdf
import re
import colorsys
import sys
import os

# ---------- color math ----------

def cmyk_to_rgb(c, m, y, k):
    r = (1 - c) * (1 - k)
    g = (1 - m) * (1 - k)
    b = (1 - y) * (1 - k)
    return r, g, b

def rgb_to_cmyk(r, g, b):
    k = 1 - max(r, g, b)
    if k >= 0.9999:
        return 0.0, 0.0, 0.0, 1.0
    c = (1 - r - k) / (1 - k)
    m = (1 - g - k) / (1 - k)
    y = (1 - b - k) / (1 - k)
    return c, m, y, k

def luminance(r, g, b):
    return 0.299 * r + 0.587 * g + 0.114 * b

def is_grayish(r, g, b, chroma_thresh=0.06):
    return (max(r, g, b) - min(r, g, b)) <= chroma_thresh

# ---------- named color palette ----------
# Ogni voce: hue (0-1), saturazione massima, modalita' speciale ('hue' | 'gray_dark' | 'gray_light'),
# e un tetto opzionale di luminosita' massima (usato per es. dal marrone, per non farlo virare
# verso l'arancione sui grigi piu' chiari).
COLOR_DEFS = {
    'blue':    {'hue': 0.60, 'sat': 0.55, 'mode': 'hue'},
    'red':     {'hue': 0.00, 'sat': 0.55, 'mode': 'hue'},
    'yellow':  {'hue': 0.14, 'sat': 0.60, 'mode': 'hue'},
    'green':   {'hue': 0.33, 'sat': 0.50, 'mode': 'hue'},
    'purple':  {'hue': 0.78, 'sat': 0.55, 'mode': 'hue'},
    'orange':  {'hue': 0.07, 'sat': 0.65, 'mode': 'hue'},
    'pink':    {'hue': 0.92, 'sat': 0.45, 'mode': 'hue'},
    'skyblue': {'hue': 0.53, 'sat': 0.45, 'mode': 'hue'},
    'brown':   {'hue': 0.08, 'sat': 0.35, 'mode': 'hue', 'max_light': 0.55},
    'black':   {'mode': 'gray_dark'},
    'white':   {'mode': 'gray_light'},
}

# Alias italiani/inglesi -> chiave canonica (usata anche come suffisso file in modalita' All)
ALIASES = {
    'blu': 'blue', 'blue': 'blue',
    'rosso': 'red', 'red': 'red',
    'giallo': 'yellow', 'yellow': 'yellow',
    'verde': 'green', 'green': 'green',
    'viola': 'purple', 'purple': 'purple',
    'arancione': 'orange', 'orange': 'orange',
    'nero': 'black', 'grigioscuro': 'black', 'nerogrigioscuro': 'black',
    'black': 'black', 'darkgray': 'black', 'darkgrey': 'black',
    'bianco': 'white', 'grigiochiaro': 'white', 'biancogrigiochiaro': 'white',
    'white': 'white', 'lightgray': 'white', 'lightgrey': 'white',
    'rosa': 'pink', 'pink': 'pink',
    'azzurro': 'skyblue', 'skyblue': 'skyblue', 'lightblue': 'skyblue',
    'marrone': 'brown', 'brown': 'brown',
}

def normalize(name: str) -> str:
    return re.sub(r'[^a-z]', '', name.lower())

def resolve_color_arg(arg: str):
    """Ritorna ('hue', float, None) oppure ('named', color_def_dict, canonical_key) oppure ('all', None, None)."""
    norm = normalize(arg)
    if norm in ('all', 'tutti', 'tutte'):
        return ('all', None, None)
    try:
        return ('hue', float(arg), None)
    except ValueError:
        pass
    if norm in ALIASES:
        key = ALIASES[norm]
        return ('named', COLOR_DEFS[key], key)
    raise ValueError(
        f"Colore non riconosciuto: '{arg}'. Usa un numero 0-1, un nome "
        f"({', '.join(sorted(set(ALIASES.keys())))}) oppure 'All'."
    )

def recolor_rgb(r, g, b, spec, white_thresh=0.985):
    """Mappa un grigio r,g,b nella nuova tinta secondo lo spec scelto,
    mantenendo la stessa luminosita' (lasciando il bianco quasi puro intatto)."""
    if not is_grayish(r, g, b):
        return None
    L = luminance(r, g, b)
    if L >= white_thresh:
        return None  # bianco/pagina non toccato

    mode = spec.get('mode', 'hue')

    if mode == 'gray_dark':
        # "Nero / grigio scuro": la palette e' gia' neutra, la lasciamo sostanzialmente invariata
        return None

    if mode == 'gray_light':
        # "Bianco / grigio chiaro": schiarisce sfondi e grigi decorativi, ma lascia
        # il nero pieno (il testo del corpo, quasi sempre a L molto bassa) leggibile e invariato.
        if L < 0.08:
            return None
        new_L = 1 - (1 - L) * 0.35
        nr, ng, nb = colorsys.hls_to_rgb(0.0, new_L, 0.0)
        return nr, ng, nb

    # modalita' 'hue'
    hue = spec['hue']
    max_sat = spec.get('sat', 0.55)
    sat = max_sat * (0.35 + 0.65 * (1 - L))
    sat = min(sat, max_sat)
    max_light = spec.get('max_light')
    use_L = min(L, max_light) if max_light is not None else L
    nr, ng, nb = colorsys.hls_to_rgb(hue, use_L, sat)
    return nr, ng, nb

# ---------- stream rewriting ----------

NUM = r'[-+]?[0-9]*\.?[0-9]+'
RE_CMYK = re.compile(rf'({NUM})\s+({NUM})\s+({NUM})\s+({NUM})\s+([kK])\b')
RE_RGB = re.compile(rf'({NUM})\s+({NUM})\s+({NUM})\s+(rg|RG)\b')
RE_GRAY = re.compile(rf'(?<![.\d])({NUM})\s+([gG])\b')

def process_stream(data: bytes, spec) -> bytes:
    text = data.decode('latin1')

    def repl_cmyk(m):
        c_, m_, y_, k_ = float(m.group(1)), float(m.group(2)), float(m.group(3)), float(m.group(4))
        op = m.group(5)
        r, g, b = cmyk_to_rgb(c_, m_, y_, k_)
        new = recolor_rgb(r, g, b, spec)
        if new is None:
            return m.group(0)
        nr, ng, nb = new
        nc, nm, ny, nk = rgb_to_cmyk(nr, ng, nb)
        return f'{nc:.4f} {nm:.4f} {ny:.4f} {nk:.4f} {op}'

    def repl_rgb(m):
        r_, g_, b_ = float(m.group(1)), float(m.group(2)), float(m.group(3))
        op = m.group(4)
        new = recolor_rgb(r_, g_, b_, spec)
        if new is None:
            return m.group(0)
        nr, ng, nb = new
        return f'{nr:.4f} {ng:.4f} {nb:.4f} {op}'

    def repl_gray(m):
        g_ = float(m.group(1))
        op = m.group(2)
        new = recolor_rgb(g_, g_, g_, spec)
        if new is None:
            return m.group(0)
        nr, ng, nb = new
        new_op = 'rg' if op == 'g' else 'RG'
        return f'{nr:.4f} {ng:.4f} {nb:.4f} {new_op}'

    text = RE_CMYK.sub(repl_cmyk, text)
    text = RE_RGB.sub(repl_rgb, text)
    text = RE_GRAY.sub(repl_gray, text)
    return text.encode('latin1')

def recolor_pdf(in_path: str, out_path: str, spec):
    pdf = pikepdf.open(in_path)
    seen = set()

    def walk_xobjects(resources):
        if '/XObject' not in resources:
            return
        for name, xobj in resources['/XObject'].items():
            key = int(xobj.objgen[0])
            if key in seen:
                continue
            seen.add(key)
            data = xobj.read_bytes()
            xobj.write(process_stream(data, spec))
            sub_res = xobj.get('/Resources')
            if sub_res:
                walk_xobjects(sub_res)

    for page in pdf.pages:
        contents = page.get('/Contents')
        if isinstance(contents, pikepdf.Array):
            data = b'\n'.join(c.read_bytes() for c in contents)
        else:
            data = contents.read_bytes()
        page.Contents = pdf.make_stream(process_stream(data, spec))
        res = page.get('/Resources')
        if res:
            walk_xobjects(res)

    pdf.save(out_path)

def main():
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(1)

    in_path, out_path, color_arg = sys.argv[1], sys.argv[2], sys.argv[3]
    kind, value, key = resolve_color_arg(color_arg)

    if kind == 'hue':
        spec = {'hue': value, 'sat': 0.55, 'mode': 'hue'}
        recolor_pdf(in_path, out_path, spec)
        print('Fatto:', out_path)

    elif kind == 'named':
        recolor_pdf(in_path, out_path, value)
        print('Fatto:', out_path)

    elif kind == 'all':
        base, ext = os.path.splitext(out_path)
        ext = ext or '.pdf'
        # ordine stabile e senza duplicati (piu' alias puntano alla stessa chiave)
        seen_keys = []
        for k in ALIASES.values():
            if k not in seen_keys:
                seen_keys.append(k)
        for k in seen_keys:
            spec = COLOR_DEFS[k]
            out_file = f'{base}_{k}{ext}'
            recolor_pdf(in_path, out_file, spec)
            print('Fatto:', out_file)

if __name__ == '__main__':
    main()