"""
ATS CV Scorer — Application Streamlit
======================================
Upload un CV (PDF, DOCX ou TXT) → parsing → scoring ATS sur 487 offres Data.

Usage :
    streamlit run app.py
    (Les fichiers de données doivent être dans le même dossier que ce script)
"""

import io
import json
import math
import os
import re
import sqlite3
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

import streamlit as st
import streamlit.components.v1 as components

# ── Imports parsing (optionnels, dégradés si absent) ──
try:
    import pdfplumber
    PDF_OK = True
except ImportError:
    PDF_OK = False

try:
    from docx import Document as DocxDocument
    DOCX_OK = True
except ImportError:
    DOCX_OK = False


# ══════════════════════════════════════════════════════
#  CONFIG ATS
# ══════════════════════════════════════════════════════

ATS_KEYWORDS: Dict[str, List[str]] = {
    "programmation": ["python", "sql", "r", "vba", "scala", "java", "c++", "bash", "javascript", "typescript", "dax"],
    "visualisation":  ["tableau", "power bi", "looker", "metabase", "plotly", "matplotlib", "seaborn", "qlik", "grafana", "superset"],
    "stack_data":     ["spark", "pandas", "numpy", "dbt", "airflow", "dagster", "snowflake", "bigquery", "redshift", "databricks", "hadoop", "kafka", "duckdb", "polars"],
    "machine_learning": ["scikit-learn", "tensorflow", "pytorch", "keras", "mlflow", "nlp", "computer vision", "xgboost", "lightgbm", "regression", "clustering", "random forest", "llm", "rag"],
    "infrastructure": ["aws", "azure", "gcp", "docker", "kubernetes", "git", "ci/cd", "terraform", "gitlab", "github", "linux"],
    "concepts":       ["etl", "elt", "data modeling", "data warehousing", "data lake", "data governance", "a/b testing", "statistiques", "statistics", "api", "data mesh"],
    "soft_skills":    ["agile", "scrum", "communication", "curiosité", "esprit d'équipe", "vulgarisation", "autonomie", "anglais", "leadership", "rigueur"],
}

CATEGORY_WEIGHTS = {
    "programmation":    0.25,
    "stack_data":       0.20,
    "visualisation":    0.15,
    "machine_learning": 0.15,
    "infrastructure":   0.10,
    "concepts":         0.10,
    "soft_skills":      0.05,
}

CATEGORY_LABELS = {
    "programmation":    "Programmation",
    "visualisation":    "Visualisation",
    "stack_data":       "Stack Data",
    "machine_learning": "Machine Learning",
    "infrastructure":   "Infrastructure",
    "concepts":         "Concepts Data",
    "soft_skills":      "Soft Skills",
}

SCORE_COLOR = {
    "excellent": "#22c55e",
    "bon":       "#84cc16",
    "moyen":     "#f59e0b",
    "faible":    "#ef4444",
}


# ══════════════════════════════════════════════════════
#  PARSING CV
# ══════════════════════════════════════════════════════

def parse_pdf(file_bytes: bytes) -> str:
    if not PDF_OK:
        st.error("pdfplumber non installé : `pip install pdfplumber`")
        return ""
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        return "\n".join(
            page.extract_text() or "" for page in pdf.pages
        )


def parse_docx(file_bytes: bytes) -> str:
    if not DOCX_OK:
        st.error("python-docx non installé : `pip install python-docx`")
        return ""
    doc = DocxDocument(io.BytesIO(file_bytes))
    return "\n".join(p.text for p in doc.paragraphs)


def parse_uploaded_file(uploaded) -> str:
    ext = uploaded.name.lower().rsplit(".", 1)[-1]
    raw = uploaded.read()
    if ext == "pdf":
        return parse_pdf(raw)
    if ext in ("docx", "doc"):
        return parse_docx(raw)
    # txt / md / autres
    for enc in ("utf-8", "latin-1", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


# ══════════════════════════════════════════════════════
#  MOTEUR DE SCORING (identique au POC CLI)
# ══════════════════════════════════════════════════════

def _regex(kw: str) -> str:
    if kw == "r":
        return r'\br\b(?!\s*\+\+|\s*#)'
    return rf'\b{re.escape(kw.strip())}\b'

def extract_keywords(text: str) -> Dict[str, List[str]]:
    tl = text.lower()
    out = {}
    for cat, kws in ATS_KEYWORDS.items():
        found = [kw for kw in kws if re.search(_regex(kw), tl)]
        if found:
            out[cat] = list(set(found))
    return out

def extract_contract(text: str) -> str:
    tl = text.lower()
    for pat, label in [
        (r'\bstage\b', "Stage"), (r'\bcdi\b', "CDI"),
        (r'\bcdd\b', "CDD"), (r'\balternance\b', "Alternance"),
    ]:
        if re.search(pat, tl):
            return label
    return "N/A"

def extract_education(text: str) -> str:
    tl = text.lower()
    if re.search(r'bac\s*\+?\s*5|master|m2|ingénieur|msc', tl):  return "Bac+5"
    if re.search(r'bac\s*\+?\s*4|m1', tl):                        return "Bac+4"
    if re.search(r'bac\s*\+?\s*3|licence|bachelor', tl):          return "Bac+3"
    if re.search(r'bac\s*\+?\s*2|bts|iut|dut', tl):               return "Bac+2"
    return "Non précisé"


@dataclass
class JobOffer:
    id: str; keywords: Dict[str, List[str]]
    contrat: str; niveau_etude: str; source: str

@dataclass
class MarketProfile:
    total_offers: int
    keyword_frequency: Dict[str, Dict[str, float]]
    category_coverage: Dict[str, float]
    top_keywords: List[Tuple[str, float]]
    education_dist: Dict[str, float]
    contract_dist: Dict[str, float]


@st.cache_data(show_spinner=False)
def load_market_profile(base_dir: str) -> MarketProfile:
    offers: List[JobOffer] = []
    seen = set()

    # DB
    db = os.path.join(base_dir, "jobs_database.db")
    if os.path.exists(db):
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT id, description FROM job_offers")
        for row in cur.fetchall():
            oid = str(row["id"])
            if oid in seen: continue
            seen.add(oid)
            desc = row["description"] or ""
            offers.append(JobOffer(oid, extract_keywords(desc), extract_contract(desc), extract_education(desc), "db"))
        conn.close()

    # JSON
    for fname, src in [("all_data_offers.json", "meteojob"), ("hellowork_data_analyst_france.json", "hellowork")]:
        fpath = os.path.join(base_dir, fname)
        if not os.path.exists(fpath): continue
        with open(fpath, encoding="utf-8") as f:
            data = json.load(f)
        for o in data.get("offers", []):
            oid = str(o.get("id", ""))
            if oid in seen: continue
            seen.add(oid)
            dr = o.get("description_raw") or ""
            dp = (o.get("description_poste") or "") + " " + (o.get("description_profil") or "")
            desc = dr if len(dr) > len(dp) else dp
            offers.append(JobOffer(oid, extract_keywords(desc), extract_contract(desc), extract_education(desc), src))

    n = len(offers)
    kw_counts = defaultdict(Counter)
    cat_counts = Counter()
    all_kw = Counter()
    edu_c = Counter()
    ctr_c = Counter()

    for o in offers:
        edu_c[o.niveau_etude] += 1
        ctr_c[o.contrat] += 1
        for cat, kws in o.keywords.items():
            cat_counts[cat] += 1
            for kw in kws:
                kw_counts[cat][kw] += 1
                all_kw[kw] += 1

    return MarketProfile(
        total_offers=n,
        keyword_frequency={c: {k: v/n for k, v in cc.items()} for c, cc in kw_counts.items()},
        category_coverage={c: v/n for c, v in cat_counts.items()},
        top_keywords=[(k, v/n) for k, v in all_kw.most_common(20)],
        education_dist={k: v/n for k, v in edu_c.items()},
        contract_dist={k: v/n for k, v in ctr_c.items()},
    )


def score_cv(cv_text: str, market: MarketProfile):
    cv_kws = extract_keywords(cv_text)
    cv_edu = extract_education(cv_text)
    dominant_edu = max(market.education_dist, key=market.education_dist.get)

    cat_scores, matched, missing, market_bonus = {}, {}, {}, {}

    for cat, all_kws in ATS_KEYWORDS.items():
        freq = market.keyword_frequency.get(cat, {})
        cv_cat = set(cv_kws.get(cat, []))
        matched[cat] = list(cv_cat)
        num = sum(freq.get(k, 0) for k in cv_cat)
        den = sum(freq.values()) or 1
        score = min(num / den, 1.0) * 100 if den else 0
        if cat not in market.keyword_frequency:
            score = 100.0
        cat_scores[cat] = round(score, 1)
        for k in cv_cat:
            if k in freq:
                market_bonus[k] = round(freq[k] * 100, 1)
        missing_kws = [k for k in ATS_KEYWORDS[cat] if k not in cv_cat]
        missing[cat] = sorted(missing_kws, key=lambda k: freq.get(k, 0), reverse=True)[:5]

    overall = sum(cat_scores.get(c, 0) * w for c, w in CATEGORY_WEIGHTS.items())
    if cv_edu == dominant_edu:
        overall = min(overall + 5, 100)

    return {
        "overall": round(overall, 1),
        "cat_scores": cat_scores,
        "matched": matched,
        "missing": missing,
        "market_bonus": market_bonus,
        "cv_edu": cv_edu,
        "dominant_edu": dominant_edu,
        "contract_targets": [c for c, _ in sorted(market.contract_dist.items(), key=lambda x: x[1], reverse=True)[:2] if c != "N/A"],
    }


# ══════════════════════════════════════════════════════
#  INTERFACE STREAMLIT
# ══════════════════════════════════════════════════════

def score_color(s: float) -> str:
    if s >= 70: return SCORE_COLOR["excellent"]
    if s >= 50: return SCORE_COLOR["bon"]
    if s >= 30: return SCORE_COLOR["moyen"]
    return SCORE_COLOR["faible"]

def score_badge(s: float) -> str:
    if s >= 70: return "Excellent"
    if s >= 50: return "Correct"
    if s >= 30: return "Faible"
    return "Insuffisant"

def score_badge_icon(s: float) -> str:
    """Retourne un indicateur coloré HTML (pas d'emoji)."""
    if s >= 70: return f'<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:{SCORE_COLOR["excellent"]};margin-right:2px;"></span>'
    if s >= 50: return f'<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:{SCORE_COLOR["bon"]};margin-right:2px;"></span>'
    if s >= 30: return f'<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:{SCORE_COLOR["moyen"]};margin-right:2px;"></span>'
    return f'<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:{SCORE_COLOR["faible"]};margin-right:2px;"></span>'

def gauge_svg(score: float, size: int = 240) -> str:
        """SVG gauge demi-cercle raffiné : aiguille contrastée, ombre et indicateurs lisibles."""
        c = score_color(score)
        # taille et métriques
        r = int(size * 0.36)
        cx = size // 2
        cy = int(size * 0.55)
        stroke_w = max(12, int(size * 0.08))

        # fonction utilitaire pour arc
        def arc_path(start_pct, end_pct, radius):
                a1 = math.pi * (1 - start_pct / 100)
                a2 = math.pi * (1 - end_pct / 100)
                x1 = cx + radius * math.cos(a1)
                y1 = cy - radius * math.sin(a1)
                x2 = cx + radius * math.cos(a2)
                y2 = cy - radius * math.sin(a2)
                large = 1 if abs(end_pct - start_pct) > 50 else 0
                return f"M {x1:.2f},{y1:.2f} A {radius},{radius} 0 {large},1 {x2:.2f},{y2:.2f}"

        zones = [
                (0, 30, "#fb7185"),
                (30, 50, "#fb923c"),
                (50, 70, "#f59e0b"),
                (70, 100, "#10b981"),
        ]
        zone_svgs = "\n".join(
                f'<path d="{arc_path(s,e,r)}" fill="none" stroke="{col}" stroke-width="{stroke_w}" stroke-linecap="round" opacity="0.95"/>'
                for s, e, col in zones
        )

        # arc de progression (plus épais, avec léger outline)
        angle = math.pi * (1 - score / 100)
        x_end = cx + r * math.cos(angle)
        y_end = cy - r * math.sin(angle)
        large_arc = 1 if score > 50 else 0
        path_fg = f"M {cx-r:.2f},{cy} A {r},{r} 0 {large_arc},1 {x_end:.2f},{y_end:.2f}"

        # ticks et labels
        ticks_svg = ""
        for t in [0, 25, 50, 75, 100]:
                ta = math.pi * (1 - t / 100)
                r_out = r + stroke_w/2 + 6
                r_in = r - stroke_w/2 - 2
                x1t = cx + r_out * math.cos(ta)
                y1t = cy - r_out * math.sin(ta)
                x2t = cx + r_in * math.cos(ta)
                y2t = cy - r_in * math.sin(ta)
                ticks_svg += f'<line x1="{x1t:.1f}" y1="{y1t:.1f}" x2="{x2t:.1f}" y2="{y2t:.1f}" stroke="#94a3b8" stroke-width="1.5" stroke-linecap="round" opacity="0.9"/>'
                # label
                r_lbl = r_out + 14
                xl = cx + r_lbl * math.cos(ta)
                yl = cy - r_lbl * math.sin(ta)
                ticks_svg += f'<text x="{xl:.1f}" y="{yl:.1f}" text-anchor="middle" dominant-baseline="middle" font-size="10" fill="#6b7280">{t}</text>'

        # aiguille stylée : outline + trait coloré + pointe
        needle_len = r - 14
        nx = cx + needle_len * math.cos(angle)
        ny = cy - needle_len * math.sin(angle)

        # centre
        hub_r = max(8, int(size * 0.03))

        h = int(size * 0.6)
        return f"""
<svg width="{size}" height="{h}" viewBox="0 0 {size} {h}" xmlns="http://www.w3.org/2000/svg">
    <defs>
        <filter id="glow"><feGaussianBlur stdDeviation="3" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
        <filter id="shadow"><feDropShadow dx="0" dy="2" stdDeviation="4" flood-color="#000" flood-opacity="0.12"/></filter>
    </defs>
    <!-- zones -->
    {zone_svgs}

    <!-- outline progression (subtle) -->
    <path d="{path_fg}" fill="none" stroke="#0f172a22" stroke-width="{stroke_w+6}" stroke-linecap="round" opacity="0.18"/>
    <!-- progression visible -->
    <path d="{path_fg}" fill="none" stroke="{c}" stroke-width="{stroke_w}" stroke-linecap="round" filter="url(#glow)"/>

    <!-- ticks -->
    {ticks_svg}

    <!-- aiguille outline (pour contraste) -->
    <line x1="{cx}" y1="{cy}" x2="{nx:.2f}" y2="{ny:.2f}" stroke="#ffffff" stroke-width="8" stroke-linecap="round" opacity="0.85"/>
    <!-- trait coloré au-dessus -->
    <line x1="{cx}" y1="{cy}" x2="{nx:.2f}" y2="{ny:.2f}" stroke="{c}" stroke-width="4" stroke-linecap="round" filter="url(#shadow)"/>
    <!-- pointe (petit triangle) -->
    <polygon points="{nx:.2f},{ny:.2f} {nx+6*math.cos(angle+0.5):.2f},{ny-6*math.sin(angle+0.5):.2f} {nx+6*math.cos(angle-0.5):.2f},{ny-6*math.sin(angle-0.5):.2f}" fill="{c}" />

    <!-- centre hub -->
    <circle cx="{cx}" cy="{cy}" r="{hub_r+4}" fill="#ffffff" stroke="#e6eef7" stroke-width="2"/>
    <circle cx="{cx}" cy="{cy}" r="{hub_r}" fill="{c}" />

    <!-- score large -->
    <text x="{cx}" y="{cy+28}" text-anchor="middle" font-size="28" font-weight="800" fill="#0f172a">{score:.0f}</text>
    <text x="{cx}" y="{cy+46}" text-anchor="middle" font-size="11" fill="#6b7280">score / 100</text>
</svg>
"""

def horizontal_bar(score: float, height: int = 10, show_label: bool = False) -> str:
    c = score_color(score)
    label = f'<span style="font-size:11px;font-weight:600;color:{c};margin-left:6px">{score:.0f}%</span>' if show_label else ""
    return f"""
<div style="display:flex;align-items:center;gap:6px;">
  <div style="flex:1;background:#f1f5f9;border-radius:99px;height:{height}px;overflow:hidden;">
    <div style="background:linear-gradient(90deg,{c}bb,{c});height:100%;width:{score:.1f}%;border-radius:99px;
                transition:width .8s cubic-bezier(.4,0,.2,1);"></div>
  </div>
  {label}
</div>"""

def keyword_chip(kw: str, freq: float | None = None, variant: str = "match") -> str:
    colors = {
        "match":   ("background:#dcfce7;color:#166534;border:1px solid #86efac;", "+"),
        "missing": ("background:#fef2f2;color:#991b1b;border:1px solid #fca5a5;", "−"),
        "top":     ("background:#eff6ff;color:#1d4ed8;border:1px solid #93c5fd;", "◆"),
    }
    style, prefix = colors.get(variant, colors["match"])
    freq_txt = f"&nbsp;<span style='opacity:.5;font-size:10px'>{freq:.0f}%</span>" if freq is not None else ""
    return (
        f"<span style='display:inline-flex;align-items:center;gap:3px;margin:2px 3px;"
        f"padding:3px 10px;border-radius:99px;font-size:12px;font-weight:500;{style}'>"
        f"<span style='font-size:10px;font-weight:700'>{prefix}</span>&nbsp;{kw}{freq_txt}</span>"
    )


def render_app():
    # ── Page config ──
    st.set_page_config(
        page_title="PassTheFilter — ATS CV Scorer",
        page_icon=None,
        layout="wide",
        initial_sidebar_state="collapsed",
    )

    # ══════════════════════════════════════════════════════
    #  CSS GLOBAL — design system professionnel
    # ══════════════════════════════════════════════════════
    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap');

    /* ─── Reset & Base ─── */
    html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
    .stApp { background: #f0f4f8; }
    footer, #MainMenu { visibility: hidden; }

    /* Masque la barre d'outils Streamlit et ajuste le layout */
    header[data-testid="stHeader"] { display: none !important; }
    #MainMenu, footer { visibility: hidden !important; }
    .block-container {
        padding-top: 1.5rem !important;
        padding-bottom: 2rem !important;
        padding-left: 2rem !important;
        padding-right: 2rem !important;
        max-width: 100% !important;
    }

    /* ─── Hero Banner ─── */
    .hero {
        background: linear-gradient(135deg, #0f172a 0%, #1a2942 55%, #162035 100%);
        border-radius: 16px;
        padding: 28px 48px;
        margin-bottom: 22px;
        position: relative;
        overflow: hidden;
        border: 1px solid rgba(255,255,255,.06);
    }
    .hero::before {
        content: '';
        position: absolute; top: -80px; right: -80px;
        width: 320px; height: 320px;
        background: radial-gradient(circle, rgba(99,102,241,.22) 0%, transparent 65%);
        border-radius: 50%;
    }
    .hero::after {
        content: '';
        position: absolute; bottom: -60px; left: 30%;
        width: 200px; height: 200px;
        background: radial-gradient(circle, rgba(16,185,129,.12) 0%, transparent 65%);
        border-radius: 50%;
    }
    .logo-circle {
        width:44px;height:44px;border-radius:10px;
        background:linear-gradient(135deg,#6366f1,#4f46e5);
        display:inline-flex;align-items:center;justify-content:center;
        color:white;font-weight:800;font-size:14px;flex-shrink:0;
        box-shadow:0 4px 14px rgba(99,102,241,.28);
    }
    .card {
        background: #ffffff;
        border-radius: 18px;
        padding: 26px 30px;
        box-shadow: 0 1px 3px rgba(0,0,0,.07), 0 4px 16px rgba(0,0,0,.04);
        margin-bottom: 20px;
        border: 1px solid #e8edf3;
    }
    .card-title {
        font-size: 11px; font-weight: 700; color: #94a3b8;
        text-transform: uppercase; letter-spacing: 1px;
        margin-bottom: 20px; display: flex; align-items: center; gap: 8px;
    }
    .card-title::after {
        content: '';
        flex: 1;
        height: 0px;
        border-top: 1.5px dashed #cbd5e1;
        opacity: 0.6;
    }

    /* ─── Séparateur en tirets réutilisable ─── */
    .section-divider {
        width: 100%;
        height: 0;
        border: none;
        border-top: 1.5px dashed #cbd5e1;
        opacity: 0.55;
        margin: 14px 0;
    }

    /* ─── Score ─── */
    .score-number {
        font-size: 3.2rem; font-weight: 800; line-height: 1;
        letter-spacing: -2px; margin-bottom: 6px;
    }
    .score-number sub { font-size: 1.1rem; color: #94a3b8; font-weight: 500; letter-spacing: 0; }
    .score-pill {
        display: inline-flex; align-items: center; gap: 6px;
        border-radius: 99px; padding: 5px 16px;
        font-size: 13px; font-weight: 600; margin-bottom: 18px;
    }
    .score-meta { font-size: 13.5px; color: #475569; line-height: 1.8; }
    .score-meta strong { color: #1e293b; }

    /* ─── Category Cards ─── */
    .cat-card {
        background: #ffffff;
        border: 1px solid rgba(15,23,42,0.06);
        border-radius: 12px;
        padding: 18px;
        transition: transform .12s ease, box-shadow .12s ease, border-color .12s;
        cursor: default;
        box-shadow: 0 6px 18px rgba(16,24,40,0.04);
    }
    .cat-card:hover { transform: translateY(-4px); box-shadow: 0 12px 30px rgba(16,24,40,0.06); border-color: #c7d2fe; }
    .cat-card.weak { border-color: rgba(239,68,68,0.22); }
    .cat-card.strong { border-color: rgba(34,197,94,0.18); }
    .cat-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; gap: 10px; }
    .cat-label { font-size: 14px; font-weight: 700; color: #0f172a; display:flex; align-items:center; gap:8px; }
    .cat-score-badge {
        font-size: 12px; font-weight: 700; padding: 6px 10px;
        border-radius: 999px; font-family: 'JetBrains Mono', monospace;
        min-width:56px; text-align:center; border:1px solid rgba(0,0,0,0.04);
        background: rgba(0,0,0,0.02);
    }
    .cat-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }

    /* ─── Metrics ─── */
    .metrics-strip {
        display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-bottom: 20px;
    }
    .metric-tile {
        background: #f8fafc; border: 1px solid #e2e8f0;
        border-radius: 14px; padding: 16px 18px;
    }
    .metric-label { font-size: 11px; color: #94a3b8; font-weight: 600; text-transform: uppercase; letter-spacing: .5px; }
    .metric-value { font-size: 1.5rem; font-weight: 800; color: #0f172a; line-height: 1.1; margin: 4px 0 2px; }
    .metric-sub { font-size: 11px; color: #94a3b8; }

    /* ─── Top Keywords ─── */
    .kw-row {
        display: grid; grid-template-columns: 120px 1fr 42px;
        align-items: center; gap: 12px; margin-bottom: 9px;
    }
    .kw-name {
        font-size: 13px; font-weight: 500; color: #1e293b;
        overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }
    .kw-pct { font-size: 12px; font-weight: 600; color: #64748b; text-align: right; font-family: 'JetBrains Mono', monospace; }

    /* ─── Recommendations ─── */
    .rec-item {
        display: flex; gap: 14px; align-items: flex-start;
        border-radius: 14px; padding: 16px 18px;
        border: 1px solid transparent; margin-bottom: 8px;
    }
    .rec-item.success { background: #f0fdf4; border-color: #86efac; }
    .rec-item.warning { background: #fffbeb; border-color: #fcd34d; }
    .rec-item.danger  { background: #fff1f2; border-color: #fda4af; }
    .rec-item.info    { background: #eff6ff; border-color: #93c5fd; }
    .rec-body { flex: 1; }
    .rec-title { font-size: 14px; font-weight: 700; color: #1e293b; margin-bottom: 4px; }
    .rec-desc  { font-size: 13px; color: #475569; line-height: 1.55; }
    .rec-chips { display: flex; flex-wrap: wrap; gap: 3px; margin-top: 8px; }

    /* ─── Progress bar ─── */
    .pbar-outer {
        background: #f1f5f9; border-radius: 99px; overflow: hidden;
    }

    /* ─── Upload zone ─── */
    div[data-testid="stFileUploader"] {
        background: #f8fafc; border-radius: 14px;
        border: 2px dashed #cbd5e1 !important;
    }
    div[data-testid="stFileUploader"]:hover { border-color: #6366f1 !important; }

    /* ─── Button ─── */
    .stButton > button {
        background: linear-gradient(135deg, #4f46e5, #6366f1) !important;
        color: white !important; border: none !important;
        border-radius: 12px !important; padding: 12px 28px !important;
        font-weight: 700 !important; font-size: 15px !important;
        box-shadow: 0 4px 14px rgba(99,102,241,.35) !important;
        transition: all .2s !important;
    }
    .stButton > button:hover {
        transform: translateY(-1px) !important;
        box-shadow: 0 6px 20px rgba(99,102,241,.45) !important;
    }

    .analyze-primary { width:100%; max-width:360px; margin:0 auto; display:block; }

    /* ─── Radio ─── */
    .stRadio [role="radiogroup"] { gap: 8px !important; }
    </style>
    """, unsafe_allow_html=True)

    # ── Chargement du profil marché ──
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    with st.spinner("Chargement du profil marché…"):
        try:
            market = load_market_profile(BASE_DIR)
        except Exception as e:
            st.error(f"Impossible de charger les données : {e}")
            st.info("Vérifiez que les fichiers JSON et la DB sont dans le même dossier que app.py")
            return

    dom_contract = max(market.contract_dist, key=market.contract_dist.get)

    # ══════════════════════════════════════════════════════
    #  HERO BANNER
    # ══════════════════════════════════════════════════════
    st.markdown(f"""
    <div class="hero">
        <div style="display:flex;flex-direction:column;align-items:center;text-align:center;gap:10px;">
            <div style="display:flex;align-items:center;gap:12px;">
                <div class="logo-circle">PTF</div>
                <div style="font-size:22px;font-weight:800;color:#f8fafc;letter-spacing:-.5px">Pass<span style='color:#818cf8'>The</span>Filter</div>
            </div>
            <div style="font-size:13px;color:#94a3b8;max-width:520px;line-height:1.5;">
                Évaluez votre CV face au marché Data français — recommandations actionnables, basées sur <strong style="color:#c7d2fe">{market.total_offers}</strong> offres réelles.
            </div>
            <div style="display:flex;align-items:center;gap:0;margin-top:8px;background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08);border-radius:12px;overflow:hidden;">
                <div style="text-align:center;padding:10px 28px;">
                    <div style="font-size:18px;font-weight:800;color:#f8fafc;line-height:1">{market.total_offers}</div>
                    <div style="font-size:10px;color:#64748b;text-transform:uppercase;letter-spacing:.7px;margin-top:3px">Offres</div>
                </div>
                <div style="width:1px;height:36px;background:rgba(255,255,255,.09);"></div>
                <div style="text-align:center;padding:10px 28px;">
                    <div style="font-size:18px;font-weight:800;color:#f8fafc;line-height:1">7</div>
                    <div style="font-size:10px;color:#64748b;text-transform:uppercase;letter-spacing:.7px;margin-top:3px">Catégories</div>
                </div>
                <div style="width:1px;height:36px;background:rgba(255,255,255,.09);"></div>
                <div style="text-align:center;padding:10px 28px;">
                    <div style="font-size:18px;font-weight:800;color:#f8fafc;line-height:1">{dom_contract if dom_contract != 'N/A' else '—'}</div>
                    <div style="font-size:10px;color:#64748b;text-transform:uppercase;letter-spacing:.7px;margin-top:3px">Contrat</div>
                </div>
                <div style="width:1px;height:36px;background:rgba(255,255,255,.09);"></div>
                <div style="text-align:center;padding:10px 28px;">
                    <div style="font-size:18px;font-weight:800;color:#818cf8;line-height:1">{market.top_keywords[0][0].upper() if market.top_keywords else '—'}</div>
                    <div style="font-size:10px;color:#64748b;text-transform:uppercase;letter-spacing:.7px;margin-top:3px">Top skill</div>
                </div>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════
    #  LAYOUT PRINCIPAL  ──  gauche / droite
    # ══════════════════════════════════════════════════════
    col_left, col_right = st.columns([1, 2.2], gap="large")

    # ──────────────────────────────────────────────────────
    #  PANNEAU GAUCHE
    # ──────────────────────────────────────────────────────
    with col_left:
        # ── Uploader CV ──
        st.markdown('<div class="card-title">CV</div>', unsafe_allow_html=True)
        upload_mode = st.radio(
            "Source", ["Fichier (PDF / DOCX / TXT)", "Texte libre"],
            horizontal=True, label_visibility="collapsed",
        )
        cv_text = ""
        uploaded = None
        if "Fichier" in upload_mode:
            uploaded = st.file_uploader(
                "Glissez votre CV ici",
                type=["pdf", "docx", "doc", "txt", "md"],
                label_visibility="collapsed",
                key="cv_uploader",
            )
            if uploaded:
                with st.spinner("Extraction du texte…"):
                    cv_text = parse_uploaded_file(uploaded)
                if cv_text.strip():
                    nb_words = len(cv_text.split())
                    st.success(f"**{nb_words}** mots extraits depuis **{uploaded.name}**")
                    with st.expander("Aperçu du texte extrait"):
                        st.code(cv_text[:2000] + ("…" if len(cv_text) > 2000 else ""), language=None)
                else:
                    st.warning("Aucun texte extrait. Essayez le mode Texte libre.")
        else:
            cv_text = st.text_area(
                "Collez le contenu de votre CV",
                height=300,
                placeholder="Formation, expériences, compétences techniques, soft skills…",
                label_visibility="collapsed",
                key="cv_text_area",
            )

        # --- Bande bouton Analyser mon CV ---
        btn_label = "Réanalyser" if st.session_state.get("analyse_cv") else "Analyser mon CV"
        analyse_clicked = st.button(btn_label, use_container_width=True, key="analyze_btn")
        if analyse_clicked:
            source_text = ""
            if "Fichier" in upload_mode:
                up = st.session_state.get("cv_uploader")
                if up is not None:
                    try:
                        source_text = parse_uploaded_file(up)
                    except Exception:
                        source_text = ""
            else:
                source_text = st.session_state.get("cv_text_area", "")

            if source_text and source_text.strip():
                with st.spinner("Scoring ATS en cours…"):
                    res = score_cv(source_text, market)
                    st.session_state["result"] = res
                    st.session_state["analyse_cv"] = True
                    st.session_state["last_run_ts"] = time.time()
            else:
                st.warning("Aucun texte de CV détecté. Téléversez un fichier ou collez le texte avant d'analyser.")

        # --- Reset sur changement d'input ---
        if "cv_uploader" in st.session_state:
            up = st.session_state.get("cv_uploader")
            up_name = up.name if up is not None and hasattr(up, 'name') else None
            if st.session_state.get("last_uploaded_name") != up_name:
                st.session_state["analyse_cv"] = False
                st.session_state.pop("result", None)
                st.session_state["last_uploaded_name"] = up_name

        if "cv_text_area" in st.session_state:
            text_snap = (st.session_state.get("cv_text_area") or "")[:200]
            if st.session_state.get("last_text_snapshot") != text_snap:
                st.session_state["analyse_cv"] = False
                st.session_state.pop("result", None)
                st.session_state["last_text_snapshot"] = text_snap

        # ── Métriques marché ──
        st.divider()
        st.markdown('<div class="card-title">Profil du marché</div>', unsafe_allow_html=True)

        dom_edu = max(market.education_dist, key=market.education_dist.get)
        dom_edu_pct = market.education_dist.get(dom_edu, 0) * 100
        dom_ct_pct  = market.contract_dist.get(dom_contract, 0) * 100

        st.markdown(f"""
        <div class="metrics-strip">
          <div class="metric-tile">
            <div class="metric-label">Offres analysées</div>
            <div class="metric-value">{market.total_offers}</div>
            <div class="metric-sub">WTTJ · Meteojob · HW</div>
          </div>
          <div class="metric-tile">
            <div class="metric-label">Contrat dominant</div>
            <div class="metric-value">{dom_contract}</div>
            <div class="metric-sub">{dom_ct_pct:.0f}% des offres</div>
          </div>
          <div class="metric-tile">
            <div class="metric-label">Formation dominante</div>
            <div class="metric-value">{dom_edu}</div>
            <div class="metric-sub">{dom_edu_pct:.0f}% des offres</div>
          </div>
        </div>
        """, unsafe_allow_html=True)

        # ── Top 12 keywords ──
        st.markdown('<div class="card-title" style="margin-top:4px">Top keywords du marché</div>', unsafe_allow_html=True)
        max_freq = market.top_keywords[0][1] if market.top_keywords else 1
        for kw, freq in market.top_keywords[:12]:
            pct = freq * 100
            bar_pct = (freq / max_freq) * 100
            c_bar = score_color(pct)
            st.markdown(f"""
            <div class="kw-row">
              <span class="kw-name">{kw}</span>
              <div class="pbar-outer" style="height:8px;">
                <div style="background:linear-gradient(90deg,{c_bar}88,{c_bar});
                            height:100%;width:{bar_pct:.1f}%;border-radius:99px;"></div>
              </div>
              <span class="kw-pct">{pct:.0f}%</span>
            </div>""", unsafe_allow_html=True)

    # ──────────────────────────────────────────────────────
    #  PANNEAU DROIT
    # ──────────────────────────────────────────────────────
    with col_right:
        # Initialisation
        if 'analyse_cv' not in st.session_state:
            st.session_state['analyse_cv'] = False

        result = st.session_state.get('result') if st.session_state.get('analyse_cv') else None

        if result:
            overall = result["overall"]
            c_main = score_color(overall)
            badge = score_badge(overall)
            b_icon = score_badge_icon(overall)

            # SCORE GLOBAL
            st.markdown('<div class="card-title">Score ATS Global</div>', unsafe_allow_html=True)

            g_col1, g_col2 = st.columns([1, 1.3], gap="medium")
            with g_col1:
                svg_html = gauge_svg(overall, 240)
                components.html(svg_html, height=int(240 * 0.62))
            with g_col2:
                st.markdown(f"""
                <div style="padding-top:14px;">
                  <div class="score-number" style="color:{c_main}">{overall:.1f}<sub>/100</sub></div>
                  <div class="score-pill" style="background:{c_main}18;color:{c_main};border:1.5px solid {c_main}44;">{b_icon} {badge}</div>
                  <div class="score-meta">
                    <strong>Formation détectée :</strong> {result['cv_edu']}<br>
                    <strong>Marché :</strong> {result['dominant_edu']} dominant<br>
                    <strong>Types de contrat :</strong> {', '.join(result['contract_targets']) or 'N/A'}
                  </div>
                </div>
                """, unsafe_allow_html=True)

            st.markdown('<hr class="section-divider"/>', unsafe_allow_html=True)

            # Résumé par catégorie (compact)
            st.markdown('<div class="card-title">Résumé par catégorie</div>', unsafe_allow_html=True)
            for cat, label in CATEGORY_LABELS.items():
                s = result['cat_scores'].get(cat, 0)
                w = CATEGORY_WEIGHTS.get(cat, 0) * 100
                cc = score_color(s)
                st.markdown(f"""
                <div style="display:grid;grid-template-columns:160px 1fr 50px 42px;align-items:center;gap:10px;margin-bottom:7px;">
                  <span style="font-size:12.5px;font-weight:500;color:#334155">{label}</span>
                  <div class="pbar-outer" style="height:9px;">
                    <div style="background:linear-gradient(90deg,{cc}99,{cc});height:100%;width:{s:.1f}%;border-radius:99px;"></div>
                  </div>
                  <span style="font-size:11px;color:#94a3b8;font-family:'JetBrains Mono',monospace">{w:.0f}% poids</span>
                  <span style="font-size:13px;font-weight:700;color:{cc};font-family:'JetBrains Mono',monospace;text-align:right">{s:.0f}</span>
                </div>
                """, unsafe_allow_html=True)

            st.divider()

            # Détail par catégorie
            st.markdown('<div class="card-title">Analyse détaillée par compétence</div>', unsafe_allow_html=True)

            cats_sorted = sorted(list(CATEGORY_LABELS.items()), key=lambda x: result['cat_scores'].get(x[0], 0))

            # Rendu en grille 2 colonnes avec Streamlit (évite le rendu du HTML brut)
            for i in range(0, len(cats_sorted), 2):
                row = cats_sorted[i:i+2]
                cols = st.columns(len(row), gap="small")
                for col_obj, (cat, label) in zip(cols, row):
                    s = result['cat_scores'].get(cat, 0)
                    matched_kws = result['matched'].get(cat, [])
                    missing_kws = result['missing'].get(cat, [])[:4]
                    c2 = score_color(s)
                    badge_cls = 'strong' if s >= 70 else ('weak' if s < 40 else '')
                    chips_match = ''.join(keyword_chip(kw, result['market_bonus'].get(kw), 'match') for kw in matched_kws) if matched_kws else "<span style='color:#94a3b8;font-size:12px;font-style:italic'>Aucun mot-clé détecté</span>"
                    chips_miss = ''.join(keyword_chip(kw, market.keyword_frequency.get(cat, {}).get(kw, 0) * 100, 'missing') for kw in missing_kws) if missing_kws else "<span style='color:#94a3b8;font-size:12px;font-style:italic'>Aucun ajout nécessaire</span>"
                    cov_pct = market.category_coverage.get(cat, 0) * 100
                    bg_score = f"{c2}18"

                    with col_obj:
                        st.markdown(f"""
                        <div class="cat-card {badge_cls}">
                            <div class="cat-header">
                                <div class="cat-label">{label}</div>
                                <div class="cat-score-badge" style="background:{bg_score};color:{c2};border-color:{c2}33">{s:.0f}/100</div>
                            </div>
                            {horizontal_bar(s, height=8)}
                            <div style="font-size:12px;color:#64748b;margin-bottom:8px">Présent dans <strong style="color:#475569">{cov_pct:.0f}%</strong> des offres</div>
                            <div style="display:flex;gap:8px;align-items:center;margin-bottom:6px">
                                <div style="font-size:12px;font-weight:700;color:#475569">Dans votre CV</div>
                                <div style="font-size:12px;color:#94a3b8">· {len(matched_kws)} mots-clés</div>
                            </div>
                            <div class="cat-chips">{chips_match}</div>
                            <div style="display:flex;gap:8px;align-items:center;margin:10px 0 6px">
                                <div style="font-size:12px;font-weight:700;color:#475569">À ajouter</div>
                                <div style="font-size:12px;color:#94a3b8">· suggestions ciblées</div>
                            </div>
                            <div class="cat-chips">{chips_miss}</div>
                        </div>
                        """, unsafe_allow_html=True)

            st.divider()

            # Recommandations
            st.markdown('<div class="card-title">Plan d\'action recommandé</div>', unsafe_allow_html=True)

            if overall >= 70:
                st.markdown(f"""
                <div class="rec-item success"><div class="rec-icon-dot" style="width:10px;height:10px;border-radius:50%;background:#22c55e;flex-shrink:0;margin-top:4px"></div><div class="rec-body"><div class="rec-title">Excellent alignement ATS — {overall:.0f}/100</div><div class="rec-desc">Votre profil est très compétitif sur ce marché. Vous pouvez postuler en confiance.</div></div></div>
                """, unsafe_allow_html=True)
            elif overall >= 50:
                st.markdown(f"""
                <div class="rec-item warning"><div class="rec-icon-dot" style="width:10px;height:10px;border-radius:50%;background:#f59e0b;flex-shrink:0;margin-top:4px"></div><div class="rec-body"><div class="rec-title">Profil correct — des ajouts ciblés peuvent améliorer votre score</div><div class="rec-desc">Score actuel : <strong>{overall:.0f}/100</strong>. Travaillez les catégories en orange ou rouge ci-dessous.</div></div></div>
                """, unsafe_allow_html=True)
            else:
                st.markdown(f"""
                <div class="rec-item danger"><div class="rec-icon-dot" style="width:10px;height:10px;border-radius:50%;background:#ef4444;flex-shrink:0;margin-top:4px"></div><div class="rec-body"><div class="rec-title">Score insuffisant — optimisation requise avant de postuler</div><div class="rec-desc">Score actuel : <strong>{overall:.0f}/100</strong>. Concentrez-vous sur les priorités ci-dessous.</div></div></div>
                """, unsafe_allow_html=True)

            weak = sorted([(cat, s) for cat, s in result['cat_scores'].items() if s < 50], key=lambda x: x[1])[:3]
            for i, (cat, s) in enumerate(weak, 1):
                top_m = result['missing'].get(cat, [])[:4]
                cov = market.category_coverage.get(cat, 0) * 100
                freq_map = market.keyword_frequency.get(cat, {})
                chips = ''.join(keyword_chip(k, freq_map.get(k, 0)*100, 'missing') for k in top_m)
                level = 'danger' if s < 30 else 'warning'
                dot_color = '#ef4444' if s < 30 else '#f59e0b'
                st.markdown(f"""
                <div class="rec-item {level}"><div class="rec-icon-dot" style="width:10px;height:10px;border-radius:50%;background:{dot_color};flex-shrink:0;margin-top:4px"></div><div class="rec-body"><div class="rec-title">Priorité {i} — {CATEGORY_LABELS[cat]} · {s:.0f}/100</div><div class="rec-desc">Catégorie présente dans <strong>{cov:.0f}%</strong> des offres. Ajoutez ces mots-clés à votre CV :</div><div class="rec-chips" style="margin-top:8px">{chips}</div></div></div>
                """, unsafe_allow_html=True)

            cv_edu = result['cv_edu']
            dom_edu = result['dominant_edu']
            if cv_edu != dom_edu:
                pct_edu = market.education_dist.get(dom_edu, 0) * 100
                st.markdown(f"""
                <div class="rec-item info"><div class="rec-icon-dot" style="width:10px;height:10px;border-radius:50%;background:#3b82f6;flex-shrink:0;margin-top:4px"></div><div class="rec-body"><div class="rec-title">Formation : mettez en valeur votre parcours</div><div class="rec-desc">Le marché cible principalement <strong>{dom_edu}</strong> ({pct_edu:.0f}% des offres). Niveau détecté dans votre CV : <strong>{cv_edu}</strong>. Valorisez votre expérience pratique et vos projets concrets.</div></div></div>
                """, unsafe_allow_html=True)

        else:
            # État vide — demander l'utilisateur de lancer l'analyse
            last_ts = st.session_state.get('last_run_ts')
            last_run_txt = ''
            if last_ts:
                last_run_txt = f"Dernière analyse : {time.strftime('%d/%m/%Y à %H:%M', time.localtime(last_ts))}"

            st.markdown(f"""
            <div class="card" style="text-align:center;padding:72px 24px 80px;">
                <div style="display:flex;align-items:center;justify-content:center;gap:16px;margin-bottom:20px;">
                    <div style="width:52px;height:52px;border-radius:14px;background:linear-gradient(135deg,#4f46e5,#6366f1);display:flex;align-items:center;justify-content:center;">
                        <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>
                    </div>
                    <div style="text-align:left">
                        <div style="font-size:1.15rem;font-weight:700;color:#1e293b;">Analyse ATS de votre CV</div>
                        <div style="font-size:13px;color:#64748b;margin-top:4px">Importez votre CV ou collez son contenu, puis cliquez sur <strong style="color:#4f46e5">{btn_label}</strong>.</div>
                    </div>
                </div>
                <div style="color:#94a3b8;font-size:12px;line-height:1.6;max-width:420px;margin:0 auto 10px;">{last_run_txt}</div>
                <div style="display:flex;justify-content:center;gap:32px;margin-top:20px;flex-wrap:wrap;">
                    <div style="text-align:center;"><div style="font-size:1.4rem;font-weight:800;color:#4f46e5">7</div><div style="font-size:11px;color:#94a3b8;text-transform:uppercase;letter-spacing:.5px">catégories</div></div>
                    <div style="text-align:center;"><div style="font-size:1.4rem;font-weight:800;color:#4f46e5">80+</div><div style="font-size:11px;color:#94a3b8;text-transform:uppercase;letter-spacing:.5px">mots-clés ATS</div></div>
                    <div style="text-align:center;"><div style="font-size:1.4rem;font-weight:800;color:#4f46e5">{market.total_offers}</div><div style="font-size:11px;color:#94a3b8;text-transform:uppercase;letter-spacing:.5px">offres de référence</div></div>
                </div>
            </div>
            """, unsafe_allow_html=True)

if __name__ == "__main__":
    render_app()