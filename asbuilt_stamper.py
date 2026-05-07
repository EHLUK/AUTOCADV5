"""
=============================================================================
  AS-BUILT DRAWING STAMPER  —  Streamlit Edition  v5
  Hargreaves / Exentec Hargreaves

  Run locally:   streamlit run asbuilt_stamper.py
  Streamlit Cloud: upload DXF → download buttons appear

  When running LOCALLY the app saves files directly to an output folder
  on your machine AND shows download buttons.
  On Streamlit Cloud only the download buttons are shown.
=============================================================================
"""

import io, os, re, datetime, traceback, zipfile, tempfile, socket
import streamlit as st

st.set_page_config(
    page_title="As-Built Stamper | Hargreaves",
    page_icon="🏗️", layout="wide", initial_sidebar_state="expanded",
)
st.markdown("""
<style>
[data-testid="stAppViewContainer"] { background: #0d1117; color: #e6edf3; }
[data-testid="stSidebar"] { background: #161b22; border-right: 1px solid #30363d; }
[data-testid="stSidebar"] * { color: #e6edf3 !important; }
h1, h2, h3 { color: #f97316 !important; letter-spacing: 0.03em; }
h1 { font-family: Courier New, monospace !important; font-size: 1.5rem !important; }
.stButton > button { background: #f97316 !important; color: white !important;
    border: none !important; font-weight: 700 !important;
    padding: 0.55rem 1.4rem !important; border-radius: 4px !important; }
.stButton > button:hover { background: #e06414 !important; }
.stTextInput > div > div > input { background: #161b22 !important; color: #e6edf3 !important;
    border: 1px solid #30363d !important; border-radius: 4px !important; }
.card { background: #161b22; border: 1px solid #30363d; border-radius: 6px;
    padding: 1rem 1.2rem; margin-bottom: 0.8rem; }
.card-title { color: #f97316; font-family: Courier New, monospace; font-size: 0.75rem;
    font-weight: 700; letter-spacing: 0.1em; border-bottom: 1px solid #30363d;
    padding-bottom: 0.4rem; margin-bottom: 0.8rem; }
.rev-row { background: #0d1117; border: 1px solid #30363d; border-radius: 4px;
    padding: 0.4rem 0.8rem; margin: 0.25rem 0; font-family: Courier New, monospace;
    font-size: 0.82rem; color: #8b949e; }
.rev-row.latest  { border-color: #f97316; color: #e6edf3; }
.rev-row.new-row { border-color: #3fb950; color: #3fb950; }
.saved-path { background: #0d1117; border: 1px solid #3fb950; border-radius: 4px;
    padding: 0.5rem 0.8rem; font-family: Courier New, monospace; font-size: 0.82rem;
    color: #3fb950; margin: 0.25rem 0; }
</style>
""", unsafe_allow_html=True)


# =============================================================================
#  LOCAL vs CLOUD DETECTION
# =============================================================================

def is_running_locally() -> bool:
    """True when running on the user's own machine (not Streamlit Cloud)."""
    # Streamlit Cloud sets this env var
    if os.environ.get("STREAMLIT_SHARING_MODE"):
        return False
    if os.environ.get("IS_STREAMLIT_CLOUD"):
        return False
    # Check if hostname looks like a cloud server
    try:
        hostname = socket.gethostname()
        if any(x in hostname.lower() for x in ["streamlit", "cloud", "heroku", "railway"]):
            return False
    except Exception:
        pass
    return True


IS_LOCAL = is_running_locally()


# =============================================================================
#  COLUMN HEADER DETECTION
# =============================================================================

HEADER_MAP = [
    ("reason",   "Reason"),
    ("desc",     "Reason"),
    ("approv",   "Approved"),
    ("status",   "Status"),
    ("check",    "Checked"),
    ("prep",     "Prep"),
    ("date",     "Date"),
    ("rev",      "Rev"),
]
HEADER_KWS    = [kw for kw, _ in HEADER_MAP]
COL_MATCH_TOL = 12.0
POSITIONAL_FALLBACK = ["Rev", "Date", "Prep", "Checked", "Status", "Reason", "Approved"]


def mtext_plain(e) -> str:
    for attr in ("plain_text", "plain_mtext"):
        try: return getattr(e, attr)()
        except: pass
    try: return e.dxf.text
    except: return ""


def get_text_entities(layout) -> list:
    out = []
    for e in layout:
        if e.dxftype() not in ("TEXT", "MTEXT"):
            continue
        try:
            txt = mtext_plain(e).strip() if e.dxftype() == "MTEXT" else e.dxf.text.strip()
            ip  = e.dxf.insert
            out.append({
                "entity": e, "text": txt,
                "x": float(ip[0]), "y": float(ip[1]),
                "height": float(e.dxf.height) if e.dxf.hasattr("height") else 2.5,
                "layer":  e.dxf.layer,
            })
        except:
            pass
    return out


def group_by_y(entities: list, tol: float = 3.0) -> list:
    groups = []
    for ent in entities:
        for grp in groups:
            if abs(ent["y"] - grp[0]) <= tol:
                grp[1].append(ent); break
        else:
            groups.append((ent["y"], [ent]))
    return groups


def detect_columns(layout) -> dict:
    all_ents = get_text_entities(layout)
    groups   = group_by_y(all_ents, tol=4.0)
    best_score, best_members = 0, []
    for _, members in groups:
        score = sum(1 for m in members if any(kw in m["text"].lower() for kw in HEADER_KWS))
        if score > best_score:
            best_score, best_members = score, members
    if best_score < 2:
        return {}
    col_x = {}
    for cell in sorted(best_members, key=lambda c: c["x"]):
        lower = cell["text"].lower()
        for kw, col_name in HEADER_MAP:
            if kw in lower and col_name not in col_x:
                col_x[col_name] = cell["x"]; break
    return col_x


def positional_col_x(row: list) -> dict:
    cells  = sorted(row, key=lambda c: c["x"])
    labels = POSITIONAL_FALLBACK[:len(cells)]
    return {labels[i]: cells[i]["x"] for i in range(len(labels))}


def get_revision_rows(layout) -> list:
    all_ents = get_text_entities(layout)
    rev_pat  = re.compile(r"^[A-Z]{1,3}$")
    groups   = group_by_y(all_ents, tol=3.0)
    rows = []
    for _, members in groups:
        if any(rev_pat.match(m["text"]) for m in members):
            members.sort(key=lambda m: m["x"])
            rows.append(members)
    rows.sort(key=lambda r: r[0]["y"])
    return rows


def detect_row_gap(rows: list) -> float:
    if len(rows) < 2:
        return 10.0
    gaps = sorted(
        r2[0]["y"] - r1[0]["y"]
        for r1, r2 in zip(rows, rows[1:])
        if r2[0]["y"] - r1[0]["y"] > 0
    )
    return gaps[0] if gaps else 10.0


# =============================================================================
#  DXF IO
# =============================================================================

def load_dxf(file_bytes: bytes):
    import ezdxf, ezdxf.recover
    with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as tmp:
        tmp.write(file_bytes); tmp_path = tmp.name
    try:
        try:
            with open(tmp_path, "r", encoding="latin-1") as fh:
                doc = ezdxf.read(fh)
            return doc, None
        except Exception as e1:
            try:
                doc, _ = ezdxf.recover.readfile(tmp_path)
                return doc, f"Loaded with recovery ({e1})"
            except Exception as e2:
                raise ValueError(
                    f"Cannot read file.\n  Primary: {e1}\n  Recovery: {e2}\n\n"
                    "If AutoCAD 2018+ DWG: Save As → AutoCAD 2013 DXF first."
                )
    finally:
        try: os.unlink(tmp_path)
        except: pass


def increment_rev(rev: str) -> str:
    rev = rev.strip().upper()
    if not rev or not rev.isalpha(): return "A"
    result, carry = [], 1
    for ch in reversed(rev):
        val = ord(ch) - ord("A") + carry
        carry, rem = divmod(val, 26)
        result.append(chr(ord("A") + rem))
    if carry:
        result.append(chr(ord("A") + carry - 1))
    return "".join(reversed(result))


def stamp_revision(doc, layout, rows, col_x, field_values):
    latest_row = rows[-1]
    row_gap    = detect_row_gap(rows)
    latest_y   = latest_row[0]["y"]
    new_y      = latest_y + row_gap
    new_rev    = field_values.get("Rev", "?")

    def field_for(cell):
        best_col, best_dist = None, 1e9
        for col_name, cx in col_x.items():
            d = abs(cell["x"] - cx)
            if d < best_dist: best_dist, best_col = d, col_name
        return best_col if best_dist < COL_MATCH_TOL else None

    log = []
    for cell in latest_row:
        old_txt = cell["text"]
        col     = field_for(cell)
        new_txt = field_values.get(col, old_txt) if col else old_txt
        try:
            new_ent = cell["entity"].copy()
            ip = new_ent.dxf.insert
            new_ent.dxf.insert = (ip[0], new_y, ip[2] if len(ip) > 2 else 0)
            if new_ent.dxftype() == "TEXT": new_ent.dxf.text = new_txt
            else:                            new_ent.text = new_txt
            if col == "Rev": new_ent.dxf.layer = f"REV {new_rev}"
            layout.add_entity(new_ent)
            log.append((old_txt, new_txt, True, col))
        except Exception as ex:
            log.append((old_txt, f"ERROR: {ex}", False, col))

    # Copy bounding polyline (best-effort)
    try:
        top_y = new_y + row_gap
        for e in list(layout):
            if e.dxftype() != "LWPOLYLINE": continue
            pts = list(e.get_points())
            ys  = [p[1] for p in pts]
            xs  = [p[0] for p in pts]
            if (max(ys)-min(ys) < 0.5
                    and abs(min(ys)-(latest_y+row_gap*0.5)) < row_gap
                    and max(xs)-min(xs) > 50):
                new_poly = e.copy()
                shift = top_y - min(ys)
                new_poly.set_points([(p[0], p[1]+shift)+p[2:] for p in pts])
                layout.add_entity(new_poly)
                break
    except: pass

    return log


def render_pdf(dxf_bytes: bytes):
    try:
        import ezdxf, ezdxf.recover, matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from ezdxf.addons.drawing import RenderContext, Frontend
        from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
        with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as tmp:
            tmp.write(dxf_bytes); tmp_path = tmp.name
        try:
            doc, _ = ezdxf.recover.readfile(tmp_path)
        finally:
            try: os.unlink(tmp_path)
            except: pass
        target = next((l for l in doc.layouts if l.name.upper() != "MODEL"), doc.modelspace())
        fig = plt.figure(figsize=(23.39, 16.54))
        ax  = fig.add_axes([0, 0, 1, 1])
        Frontend(RenderContext(doc), MatplotlibBackend(ax)).draw_layout(target, finalize=True)
        buf = io.BytesIO()
        fig.savefig(buf, format="pdf", dpi=150, bbox_inches="tight")
        plt.close(fig)
        return buf.getvalue()
    except: return None


def save_to_disk(dxf_bytes: bytes, pdf_bytes, out_dir: str, base: str):
    """Write DXF and PDF to out_dir. Returns list of saved paths."""
    os.makedirs(out_dir, exist_ok=True)
    saved = []
    dxf_path = os.path.join(out_dir, base + "_ASBUILT.dxf")
    with open(dxf_path, "wb") as f:
        f.write(dxf_bytes)
    saved.append(dxf_path)
    if pdf_bytes:
        pdf_path = os.path.join(out_dir, base + "_ASBUILT.pdf")
        with open(pdf_path, "wb") as f:
            f.write(pdf_bytes)
        saved.append(pdf_path)
    return saved


# =============================================================================
#  PROCESS ONE FILE
# =============================================================================

def process_file(file_bytes: bytes, filename: str, field_values: dict) -> dict:
    result = {
        "success": False, "dxf_bytes": None, "log": [],
        "rows": [], "row_log": [], "new_rev": "",
        "col_x": {}, "col_method": "", "error": None,
    }
    def log(msg, level="info"): result["log"].append((level, msg))
    try:
        import ezdxf  # noqa
        doc, warn = load_dxf(file_bytes)
        if warn: log(f"⚠ {warn}", "warn")
        else:    log(f"✓ Loaded {filename} (DXF {doc.dxfversion})", "ok")

        best_layout, best_rows = None, []
        for layout in doc.layouts:
            rows = get_revision_rows(layout)
            if len(rows) > len(best_rows):
                best_rows, best_layout = rows, layout

        if not best_rows:
            result["error"] = (
                "No revision rows detected.\n"
                "Ensure MTEXT entities with revision letters (A, B, C…) exist in the title block."
            )
            return result

        log(f"✓ {len(best_rows)} revision row(s) in layout '{best_layout.name}'", "ok")
        result["rows"] = [[{"text": c["text"], "x": c["x"], "y": c["y"]} for c in r] for r in best_rows]

        col_x = detect_columns(best_layout)
        if col_x:
            log(f"✓ Columns detected from header row ({len(col_x)} columns)", "ok")
            result["col_method"] = "header"
        else:
            col_x = positional_col_x(best_rows[-1])
            log("⚠ No header row found — using positional fallback", "warn")
            result["col_method"] = "positional"
        result["col_x"] = col_x

        rev_pat     = re.compile(r"^[A-Z]{1,3}$")
        current_rev = next((c["text"].strip().upper() for c in best_rows[-1]
                            if rev_pat.match(c["text"].strip().upper())), "")
        new_rev     = increment_rev(current_rev) if current_rev else "A"
        result["new_rev"] = new_rev
        log(f"✓ Rev: '{current_rev}' → '{new_rev}'", "ok")

        fv = {**field_values, "Rev": new_rev}
        row_log = stamp_revision(doc, best_layout, best_rows, col_x, fv)
        result["row_log"] = row_log
        log(f"✓ Added {sum(1 for *_,s,_ in row_log if s)}/{len(row_log)} entities", "ok")

        buf = io.BytesIO()
        doc.write(buf)
        result["dxf_bytes"] = buf.getvalue()
        result["success"]   = True
        log("✓ DXF ready", "ok")
    except Exception as ex:
        result["error"] = str(ex)
        result["log"].append(("err", traceback.format_exc()))
    return result


# =============================================================================
#  UI HELPERS
# =============================================================================

def row_html(cells, label, css=""):
    content = "  |  ".join(c["text"] for c in cells if c["text"].strip())
    return (f'<div class="rev-row {css}">'
            f'<span style="color:#f97316;margin-right:8px;font-size:0.7rem">{label}</span>'
            f'{content}</div>')


def preview_new_row(latest_row, col_x, field_values, new_rev):
    fv = {**field_values, "Rev": new_rev}
    preview = []
    for cell in latest_row:
        best_col, best_dist = None, 1e9
        for col_name, cx in col_x.items():
            d = abs(cell["x"] - cx)
            if d < best_dist: best_dist, best_col = d, col_name
        new_txt = fv.get(best_col, cell["text"]) if best_col and best_dist < COL_MATCH_TOL else cell["text"]
        preview.append({"text": new_txt, "x": cell["x"], "y": cell["y"]})
    return preview


# =============================================================================
#  SIDEBAR
# =============================================================================

def sidebar():
    with st.sidebar:
        st.markdown("## 🏗️ AS-BUILT STAMPER")
        st.markdown("*Hargreaves / Exentec Hargreaves*")

        if IS_LOCAL:
            st.success("🖥️ Running locally — files saved to disk")
        else:
            st.info("☁️ Cloud mode — use download buttons")

        st.divider()
        st.markdown("### Revision Fields")
        today  = datetime.date.today().strftime("%d/%m/%Y")
        date_v = st.text_input("Date",        value=today,  help="DD/MM/YYYY")
        prep_v = st.text_input("Prep By",     value="",     placeholder="Initials")
        chk_v  = st.text_input("Checked By",  value="",     placeholder="Initials")
        sta_v  = st.text_input("Status",      value="P7")
        rsn_v  = st.text_input("Reason",      value="As Built")
        apr_v  = st.text_input("Approved By", value="",     placeholder="Initials")

        out_dir = ""
        if IS_LOCAL:
            st.divider()
            st.markdown("### Output Folder")
            default_out = os.path.join(os.path.expanduser("~"), "Desktop", "AsBuilt_Output")
            out_dir = st.text_input("Save files to:", value=default_out,
                                    help="Full path to output folder. Created if it doesn't exist.")
            st.caption(f"Files will be saved to:\n`{out_dir}`")

        st.divider()
        st.caption(
            "Rev is auto-incremented per drawing. "
            "Column positions are read from each drawing's header row automatically."
        )
        st.caption("⚠ 2018+ DWG → Save As DXF first.")

    return {
        "Date": date_v, "Prep": prep_v, "Checked": chk_v,
        "Status": sta_v, "Reason": rsn_v, "Approved": apr_v,
    }, out_dir


# =============================================================================
#  MAIN
# =============================================================================

def main():
    field_values, out_dir = sidebar()

    st.markdown("# AS-BUILT STAMPER")
    mode_badge = (
        "🖥️ **Local mode** — files saved directly to your output folder + download buttons"
        if IS_LOCAL else
        "☁️ **Cloud mode** — click the download buttons after processing"
    )
    st.markdown(mode_badge)
    st.divider()

    st.markdown('<div class="card"><div class="card-title">① UPLOAD DRAWINGS</div>', unsafe_allow_html=True)
    uploaded = st.file_uploader(
        "Drop DXF files here", type=["dxf", "dwg"],
        accept_multiple_files=True, label_visibility="collapsed",
    )
    st.markdown('</div>', unsafe_allow_html=True)

    if not uploaded:
        st.info("👆 Upload one or more DXF files to get started.")
        return

    st.markdown('<div class="card"><div class="card-title">② STAMP</div>', unsafe_allow_html=True)
    col_l, col_r = st.columns([4, 1])
    with col_r:
        run = st.button("▶  Stamp As-Built", use_container_width=True)
    with col_l:
        st.caption(
            f"**{len(uploaded)}** file(s)  ·  "
            f"Status=**{field_values['Status']}**  ·  "
            f"Reason=**{field_values['Reason']}**  ·  "
            f"Date=**{field_values['Date']}**"
        )
    st.markdown('</div>', unsafe_allow_html=True)

    # ── Preview ───────────────────────────────────────────────────────────────
    if not run:
        st.markdown('<div class="card"><div class="card-title">PREVIEW — DETECTED REVISION TABLE</div>', unsafe_allow_html=True)
        for uf in uploaded:
            with st.expander(f"📄 {uf.name}", expanded=True):
                try:
                    doc, warn = load_dxf(uf.read())
                    if warn: st.warning(warn)
                    best_layout, best_rows = None, []
                    for layout in doc.layouts:
                        rows = get_revision_rows(layout)
                        if len(rows) > len(best_rows):
                            best_rows, best_layout = rows, layout
                    if not best_rows:
                        st.warning("No revision rows detected.")
                        continue
                    col_x  = detect_columns(best_layout) or positional_col_x(best_rows[-1])
                    method = "header row" if detect_columns(best_layout) else "positional"
                    rev_pat = re.compile(r"^[A-Z]{1,3}$")
                    cur_rev = next((c["text"].strip().upper() for c in best_rows[-1]
                                    if rev_pat.match(c["text"].strip().upper())), "")
                    new_rev = increment_rev(cur_rev) if cur_rev else "?"
                    st.caption(f"Layout: **{best_layout.name}** · {len(best_rows)} rows · Rev: **{cur_rev}** → **{new_rev}** · Columns: **{method}**")
                    for i, row in enumerate(best_rows):
                        st.markdown(row_html(row, f"Rev {i+1}{'  ← LATEST' if i==len(best_rows)-1 else ''}", "latest" if i==len(best_rows)-1 else ""), unsafe_allow_html=True)
                    fv = {**field_values, "Rev": new_rev}
                    st.markdown(row_html(preview_new_row(best_rows[-1], col_x, fv, new_rev), f"→ NEW ROW (Rev {new_rev})", "new-row"), unsafe_allow_html=True)
                except Exception as ex:
                    st.error(f"Error: {ex}")
                    st.code(traceback.format_exc())
        st.markdown('</div>', unsafe_allow_html=True)
        return

    # ── Process ───────────────────────────────────────────────────────────────
    st.markdown('<div class="card"><div class="card-title">③ RESULTS</div>', unsafe_allow_html=True)

    if IS_LOCAL and not out_dir.strip():
        st.error("Please enter an output folder path in the sidebar.")
        st.markdown('</div>', unsafe_allow_html=True)
        return

    all_outputs, any_success = [], False

    for uf in uploaded:
        st.markdown(f"#### 📄 {uf.name}")
        with st.spinner(f"Processing {uf.name}…"):
            result = process_file(uf.read(), uf.name, field_values)

        with st.expander("Activity log", expanded=not result["success"]):
            for level, msg in result["log"]:
                colour = {"ok": "#3fb950", "warn": "#d29922", "err": "#f85149"}.get(level, "#8b949e")
                if level == "err": st.code(msg)
                else: st.markdown(f'<span style="color:{colour}">{msg}</span>', unsafe_allow_html=True)

        if not result["success"]:
            st.error(f"✗ {result['error']}")
            st.markdown("---"); continue

        any_success = True

        # Show row diff
        if result["rows"]:
            st.markdown(row_html(result["rows"][-1], "Old row", "latest"), unsafe_allow_html=True)
        if result["row_log"]:
            new_cells = [{"text": nt, "x": i*20, "y": 0}
                         for i, (_, nt, ok, _c) in enumerate(result["row_log"]) if ok]
            st.markdown(row_html(new_cells, f"New row  Rev {result['new_rev']}", "new-row"), unsafe_allow_html=True)

        base = os.path.splitext(uf.name)[0]

        # Generate PDF
        with st.spinner("Generating PDF…"):
            pdf_bytes = render_pdf(result["dxf_bytes"])

        # ── Save to disk (local mode) ─────────────────────────────────────────
        if IS_LOCAL:
            try:
                saved = save_to_disk(result["dxf_bytes"], pdf_bytes, out_dir.strip(), base)
                for path in saved:
                    st.markdown(
                        f'<div class="saved-path">✓ Saved: {path}</div>',
                        unsafe_allow_html=True,
                    )
            except Exception as ex:
                st.error(f"Could not save to disk: {ex}")

        # ── Download buttons (always shown) ───────────────────────────────────
        c1, c2 = st.columns(2)
        with c1:
            st.download_button(
                f"⬇ {base}_ASBUILT.dxf",
                result["dxf_bytes"],
                f"{base}_ASBUILT.dxf",
                "application/dxf",
                use_container_width=True,
            )
        with c2:
            if pdf_bytes:
                st.download_button(
                    "⬇ PDF",
                    pdf_bytes,
                    f"{base}_ASBUILT.pdf",
                    "application/pdf",
                    use_container_width=True,
                )
                all_outputs.append((f"{base}_ASBUILT.dxf", result["dxf_bytes"], f"{base}_ASBUILT.pdf", pdf_bytes))
            else:
                st.caption("⚠ PDF render failed — DXF saved/available above")
                all_outputs.append((f"{base}_ASBUILT.dxf", result["dxf_bytes"], None, None))

        st.success(f"✓ Stamped — Rev {result['new_rev']}")
        st.markdown("---")

    # ── Bulk ZIP (cloud / multi-file) ─────────────────────────────────────────
    if any_success and len(all_outputs) > 1:
        zb = io.BytesIO()
        with zipfile.ZipFile(zb, "w", zipfile.ZIP_DEFLATED) as zf:
            for dn, db, pn, pb in all_outputs:
                zf.writestr(dn, db)
                if pb: zf.writestr(pn, pb)
        st.download_button(
            "⬇ Download All (ZIP)", zb.getvalue(),
            "AsBuilt_Drawings.zip", "application/zip",
        )

    st.markdown('</div>', unsafe_allow_html=True)


if __name__ == "__main__":
    main()
