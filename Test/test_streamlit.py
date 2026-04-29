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
import os
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

import streamlit as st

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
    "programmation":    "💻 Programmation",
    "visualisation":    "📊 Visualisation",
    "stack_data":       "⚙️  Stack Data",
    "machine_learning": "🤖 Machine Learning",
    "infrastructure":   "☁️  Infrastructure",
    "concepts":         "🧠 Concepts Data",
    "soft_skills":      "🤝 Soft Skills",
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

def gauge_svg(score: float, size: int = 200) -> str:
    """SVG gauge demi-cercle pour le score global."""
    c = score_color(score)
    r = 80
    cx = size // 2
    cy = size // 2 + 10
    # Arc : 180° → la valeur de score
    import math
    angle = math.pi * (1 - score / 100)
    x_end = cx + r * math.cos(angle)
    y_end = cy - r * math.sin(angle)
    # Fond gris
    large_arc = 1  # toujours demi-cercle
    path_bg = f"M {cx-r},{cy} A {r},{r} 0 0,1 {cx+r},{cy}"
    path_fg = f"M {cx-r},{cy} A {r},{r} 0 0,1 {x_end:.1f},{y_end:.1f}"
    return f"""
<svg width="{size}" height="{size//2+30}" viewBox="0 0 {size} {size//2+30}" xmlns="http://www.w3.org/2000/svg">
  <path d="{path_bg}" fill="none" stroke="#e5e7eb" stroke-width="18" stroke-linecap="round"/>
  <path d="{path_fg}" fill="none" stroke="{c}" stroke-width="18" stroke-linecap="round"/>
  <text x="{cx}" y="{cy+5}" text-anchor="middle" font-size="32" font-weight="700" fill="{c}">{score:.0f}</text>
  <text x="{cx}" y="{cy+24}" text-anchor="middle" font-size="12" fill="#6b7280">/ 100</text>
</svg>"""

def horizontal_bar(score: float, height: int = 14) -> str:
    c = score_color(score)
    return f"""
<div style="background:#f3f4f6;border-radius:99px;height:{height}px;width:100%;overflow:hidden;">
  <div style="background:{c};height:100%;width:{score:.1f}%;border-radius:99px;
              transition:width .6s cubic-bezier(.4,0,.2,1);"></div>
</div>"""

def keyword_chip(kw: str, freq: float | None = None, variant: str = "match") -> str:
    colors = {
        "match":   ("background:#dcfce7;color:#166534;border:1px solid #86efac;", "✓ "),
        "missing": ("background:#fef2f2;color:#991b1b;border:1px solid #fca5a5;", "✗ "),
        "top":     ("background:#eff6ff;color:#1d4ed8;border:1px solid #93c5fd;", ""),
    }
    style, prefix = colors.get(variant, colors["match"])
    freq_txt = f" <span style='opacity:.6;font-size:10px'>({freq:.0f}%)</span>" if freq is not None else ""
    return (
        f"<span style='display:inline-block;margin:2px 3px;padding:3px 9px;"
        f"border-radius:99px;font-size:12px;font-weight:500;{style}'>"
        f"{prefix}{kw}{freq_txt}</span>"
    )


def render_app():
    # ── Page config ──
    st.set_page_config(
        page_title="ATS CV Scorer",
        page_icon="🎯",
        layout="wide",
        initial_sidebar_state="collapsed",
    )

    # ── CSS global ──
    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=DM+Mono:wght@400;500&display=swap');

    html, body, [class*="css"] { font-family: 'DM Sans', sans-serif; }

    .stApp { background: #f8fafc; }

    .hero {
        background: linear-gradient(135deg, #0f172a 0%, #1e293b 60%, #0f2847 100%);
        border-radius: 20px;
        padding: 40px 48px;
        margin-bottom: 28px;
        position: relative;
        overflow: hidden;
    }
    .hero::before {
        content: '';
        position: absolute;
        top: -60px; right: -60px;
        width: 260px; height: 260px;
        background: radial-gradient(circle, rgba(59,130,246,.18) 0%, transparent 70%);
        border-radius: 50%;
    }
    .hero h1 { color: #f8fafc; font-size: 2.2rem; font-weight: 700; margin: 0 0 6px; letter-spacing: -.5px; }
    .hero p  { color: #94a3b8; font-size: 1.05rem; margin: 0; }
    .hero .badge {
        display: inline-block; background: rgba(59,130,246,.2); color: #93c5fd;
        border: 1px solid rgba(59,130,246,.3); border-radius: 99px;
        padding: 4px 14px; font-size: 13px; font-weight: 500; margin-bottom: 16px;
    }

    .card {
        background: white;
        border-radius: 16px;
        padding: 24px 28px;
        box-shadow: 0 1px 4px rgba(0,0,0,.06);
        margin-bottom: 20px;
        border: 1px solid #e2e8f0;
    }
    .card-title {
        font-size: 14px; font-weight: 600; color: #64748b;
        text-transform: uppercase; letter-spacing: .8px;
        margin-bottom: 16px;
    }

    .metric-row { display: flex; gap: 16px; margin-bottom: 20px; }
    .metric-box {
        flex: 1; background: white; border-radius: 14px;
        padding: 18px 20px; border: 1px solid #e2e8f0;
        box-shadow: 0 1px 3px rgba(0,0,0,.05);
    }
    .metric-label { font-size: 12px; color: #94a3b8; font-weight: 500; margin-bottom: 4px; }
    .metric-value { font-size: 1.4rem; font-weight: 700; color: #0f172a; }
    .metric-sub   { font-size: 12px; color: #64748b; margin-top: 2px; }

    .rec-card {
        background: #f0fdf4; border: 1px solid #86efac;
        border-radius: 12px; padding: 14px 18px; margin-bottom: 10px;
        font-size: 14px; color: #14532d; line-height: 1.5;
    }
    .rec-card.warn  { background:#fff7ed; border-color:#fdba74; color:#7c2d12; }
    .rec-card.error { background:#fef2f2; border-color:#fca5a5; color:#7f1d1d; }
    .rec-card.info  { background:#eff6ff; border-color:#93c5fd; color:#1e3a8a; }

    .top-kw-row { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
    .top-kw-name { font-size: 14px; font-weight: 500; color: #1e293b; width: 140px; flex-shrink: 0; }
    .top-kw-bar  { flex: 1; }
    .top-kw-pct  { font-size: 13px; color: #64748b; width: 44px; text-align: right; flex-shrink: 0; }

    div[data-testid="stFileUploader"] {
        background: white; border-radius: 16px;
        border: 2px dashed #cbd5e1 !important;
        padding: 12px;
    }
    div[data-testid="stFileUploader"]:hover { border-color: #3b82f6 !important; }

    .stButton > button {
        background: #2563eb; color: white; border: none;
        border-radius: 10px; padding: 10px 28px;
        font-weight: 600; font-size: 15px;
        transition: background .2s;
    }
    .stButton > button:hover { background: #1d4ed8; }

    footer { visibility: hidden; }
    #MainMenu { visibility: hidden; }
    </style>
    """, unsafe_allow_html=True)

    # ── Hero ──
    st.markdown("""
    <div class="hero">
        <div class="badge">🎯 Simulateur ATS</div>
        <h1>CV Scorer Data</h1>
        <p>Analysez l'alignement de votre CV avec 487 offres d'emploi Data en France.<br>
        Identifiez les keywords manquants, simulez la lecture ATS, optimisez vos candidatures.</p>
    </div>
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

    # ── Layout principal ──
    col_left, col_right = st.columns([1, 1.6], gap="large")

    with col_left:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown('<div class="card-title">📄 Votre CV</div>', unsafe_allow_html=True)

        upload_mode = st.radio(
            "Source", ["📎 Fichier (PDF / DOCX / TXT)", "✏️  Texte libre"],
            horizontal=True, label_visibility="collapsed"
        )

        cv_text = ""

        if "📎" in upload_mode:
            uploaded = st.file_uploader(
                "Glissez votre CV ici",
                type=["pdf", "docx", "doc", "txt", "md"],
                label_visibility="collapsed",
            )
            if uploaded:
                with st.spinner("Extraction du texte…"):
                    cv_text = parse_uploaded_file(uploaded)
                if cv_text.strip():
                    st.success(f"✓ {len(cv_text.split())} mots extraits depuis **{uploaded.name}**")
                    with st.expander("Aperçu du texte extrait"):
                        st.text(cv_text[:2000] + ("…" if len(cv_text) > 2000 else ""))
                else:
                    st.warning("Aucun texte extrait. Essayez le mode Texte libre.")
        else:
            cv_text = st.text_area(
                "Collez le contenu de votre CV",
                height=320,
                placeholder="Formation, expériences, compétences techniques, soft skills…",
                label_visibility="collapsed",
            )

        st.markdown('</div>', unsafe_allow_html=True)

        # Métriques marché
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown('<div class="card-title">📈 Base de référence</div>', unsafe_allow_html=True)
        st.markdown(f"""
        <div class="metric-row">
          <div class="metric-box">
            <div class="metric-label">Offres analysées</div>
            <div class="metric-value">{market.total_offers}</div>
            <div class="metric-sub">WTTJ · Meteojob · Hellowork</div>
          </div>
          <div class="metric-box">
            <div class="metric-label">Contrat dominant</div>
            <div class="metric-value">{max(market.contract_dist, key=market.contract_dist.get)}</div>
            <div class="metric-sub">{market.contract_dist.get(max(market.contract_dist, key=market.contract_dist.get), 0)*100:.0f}% des offres</div>
          </div>
        </div>
        """, unsafe_allow_html=True)

        # Top 10 keywords marché
        st.markdown('<div class="card-title" style="margin-top:8px">🔑 Top keywords du marché</div>', unsafe_allow_html=True)
        for kw, freq in market.top_keywords[:10]:
            st.markdown(f"""
            <div class="top-kw-row">
              <span class="top-kw-name">{kw}</span>
              <div class="top-kw-bar">{horizontal_bar(freq*100, 10)}</div>
              <span class="top-kw-pct">{freq*100:.0f}%</span>
            </div>""", unsafe_allow_html=True)

        st.markdown('</div>', unsafe_allow_html=True)

    with col_right:
        scored = False
        result = None

        if cv_text.strip():
            if st.button("🚀  Analyser mon CV", use_container_width=True):
                with st.spinner("Scoring en cours…"):
                    result = score_cv(cv_text, market)
                    st.session_state["result"] = result
            elif "result" in st.session_state:
                result = st.session_state["result"]

        if result:
            # ── Score global ──
            overall = result["overall"]
            c = score_color(overall)
            badge = score_badge(overall)

            st.markdown('<div class="card">', unsafe_allow_html=True)
            st.markdown('<div class="card-title">🎯 Score Global ATS</div>', unsafe_allow_html=True)

            gcol1, gcol2 = st.columns([1, 1.4])
            with gcol1:
                st.markdown(gauge_svg(overall, 220), unsafe_allow_html=True)
            with gcol2:
                st.markdown(f"""
                <div style="padding-top:20px">
                  <div style="font-size:2.4rem;font-weight:800;color:{c}">{overall:.1f}<span style="font-size:1.2rem;color:#94a3b8">/100</span></div>
                  <div style="display:inline-block;background:{c}22;color:{c};border:1px solid {c}55;
                       border-radius:99px;padding:4px 14px;font-size:13px;font-weight:600;margin-bottom:14px">{badge}</div>
                  <div style="font-size:14px;color:#475569;line-height:1.6">
                    <b>Formation :</b> {result['cv_edu']}<br>
                    <b>Marché :</b> {result['dominant_edu']} dominant<br>
                    <b>Cibles :</b> {', '.join(result['contract_targets']) or 'N/A'}
                  </div>
                </div>
                """, unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)

            # ── Scores catégorie ──
            st.markdown('<div class="card">', unsafe_allow_html=True)
            st.markdown('<div class="card-title">📂 Détail par catégorie</div>', unsafe_allow_html=True)

            for cat, label in CATEGORY_LABELS.items():
                s = result["cat_scores"].get(cat, 0)
                matched_kws = result["matched"].get(cat, [])
                missing_kws = result["missing"].get(cat, [])[:3]
                c2 = score_color(s)

                with st.expander(f"{label}  —  **{s:.0f}/100**", expanded=(s < 40)):
                    st.markdown(horizontal_bar(s), unsafe_allow_html=True)
                    st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

                    chips_match = "".join(
                        keyword_chip(kw, result["market_bonus"].get(kw), "match")
                        for kw in matched_kws
                    ) if matched_kws else "<span style='color:#94a3b8;font-size:13px'>— Aucun keyword trouvé</span>"

                    chips_miss = "".join(
                        keyword_chip(kw, market.keyword_frequency.get(cat, {}).get(kw, 0) * 100, "missing")
                        for kw in missing_kws
                    ) if missing_kws else "<span style='color:#94a3b8;font-size:13px'>— Rien à ajouter</span>"

                    st.markdown(f"""
                    <div style='margin-bottom:8px'>
                      <span style='font-size:12px;font-weight:600;color:#64748b'>PRÉSENT DANS VOTRE CV</span><br>
                      {chips_match}
                    </div>
                    <div>
                      <span style='font-size:12px;font-weight:600;color:#64748b'>TOP MANQUANTS (par impact marché)</span><br>
                      {chips_miss}
                    </div>
                    """, unsafe_allow_html=True)

            st.markdown('</div>', unsafe_allow_html=True)

            # ── Recommandations ──
            st.markdown('<div class="card">', unsafe_allow_html=True)
            st.markdown('<div class="card-title">💡 Recommandations</div>', unsafe_allow_html=True)

            weak = [(c, s) for c, s in result["cat_scores"].items() if s < 40]
            weak.sort(key=lambda x: x[1])

            if weak:
                for cat, s in weak[:3]:
                    top_m = result["missing"].get(cat, [])[:3]
                    cov = market.category_coverage.get(cat, 0) * 100
                    freq_map = market.keyword_frequency.get(cat, {})
                    chips = "".join(keyword_chip(k, freq_map.get(k, 0)*100, "missing") for k in top_m)
                    st.markdown(f"""
                    <div class="rec-card warn">
                      <b>⚠️ {CATEGORY_LABELS[cat]}</b> — Score {s:.0f}/100
                      (mentionné dans {cov:.0f}% des offres)<br>
                      <div style='margin-top:6px'>Ajoutez : {chips}</div>
                    </div>""", unsafe_allow_html=True)

            dom_edu = result["dominant_edu"]
            cv_edu = result["cv_edu"]
            if cv_edu != dom_edu:
                pct = market.education_dist.get(dom_edu, 0) * 100
                st.markdown(f"""
                <div class="rec-card info">
                  📚 Le marché cible principalement <b>{dom_edu}</b> ({pct:.0f}% des offres).
                  Valorisez bien votre parcours <b>{cv_edu}</b> ou votre expérience pratique.
                </div>""", unsafe_allow_html=True)

            if overall >= 70:
                st.markdown('<div class="rec-card">✅ Excellent alignement — votre profil est très compétitif sur ce marché.</div>', unsafe_allow_html=True)
            elif overall >= 50:
                st.markdown('<div class="rec-card warn">🟡 Profil correct — quelques ajouts ciblés peuvent significativement booster votre score.</div>', unsafe_allow_html=True)
            else:
                st.markdown('<div class="rec-card error">🔴 Score insuffisant — concentrez-vous sur les catégories en rouge avant de postuler.</div>', unsafe_allow_html=True)

            st.markdown('</div>', unsafe_allow_html=True)

        else:
            # État vide
            st.markdown("""
            <div class="card" style="text-align:center;padding:60px 20px">
              <div style="font-size:3rem;margin-bottom:16px">📋</div>
              <div style="font-size:1.1rem;font-weight:600;color:#475569;margin-bottom:8px">
                Uploadez votre CV pour démarrer
              </div>
              <div style="color:#94a3b8;font-size:14px;line-height:1.6">
                Formats supportés : PDF, DOCX, TXT<br>
                Ou collez directement le texte de votre CV
              </div>
            </div>
            """, unsafe_allow_html=True)


if __name__ == "__main__":
    render_app()