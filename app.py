import streamlit as st
import requests
import re
import time
import pandas as pd
from urllib.parse import urlparse

# ─── Configuration ────────────────────────────────────────────────────────────

GALLICA_SRU    = "https://gallica.bnf.fr/SRU"
GALLICA_IIIF   = "https://gallica.bnf.fr/iiif"
GALLICA_BASE   = "https://gallica.bnf.fr"
TIMEOUT        = 15   # secondes par requête
MAX_ISSUES     = 500  # garde-fou pour éviter les boucles infinies

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; GallicaHarvester/1.0; "
        "+https://github.com/user/gallica-harvester)"
    )
}

# ─── Utilitaires ──────────────────────────────────────────────────────────────

def extract_ark(url: str) -> str | None:
    """
    Extrait l'identifiant ARK depuis une URL Gallica.
    Accepte des formes comme :
      https://gallica.bnf.fr/ark:/12148/cb32798952d/date
      https://gallica.bnf.fr/ark:/12148/cb32798952d
    Retourne None si aucun ARK n'est trouvé.
    """
    match = re.search(r"ark:/12148/([a-z0-9]+)", url)
    if match:
        return f"ark:/12148/{match.group(1)}"
    return None


def get_issues(ark: str, max_records: int = MAX_ISSUES) -> list[dict]:
    """
    Récupère la liste des numéros d'une revue via l'API SRU de Gallica.
    Retourne une liste de dicts avec au moins : ark, titre, date, url.
    Lève une exception explicite en cas d'erreur réseau ou de réponse inattendue.
    """
    # L'ARK d'une revue se termine souvent par /date — on le retire pour la requête
    base_ark = ark.split("/date")[0]

    issues = []
    start_record = 1
    page_size = 50   # Gallica accepte jusqu'à 50 par page

    while True:
        params = {
            "operation": "searchRetrieve",
            "version": "1.2",
            "query": f'dc.relation="{base_ark}" and dc.type="fascicule"',
            "startRecord": start_record,
            "maximumRecords": page_size,
            "collapsing": "false",
        }

        try:
            resp = requests.get(GALLICA_SRU, params=params, headers=HEADERS, timeout=TIMEOUT)
            resp.raise_for_status()
        except requests.exceptions.Timeout:
            raise TimeoutError(
                f"Gallica SRU n'a pas répondu dans les {TIMEOUT}s "
                f"(page {start_record}–{start_record + page_size - 1})."
            )
        except requests.exceptions.HTTPError as e:
            raise ConnectionError(f"Erreur HTTP {resp.status_code} : {e}")
        except requests.exceptions.RequestException as e:
            raise ConnectionError(f"Erreur réseau : {e}")

        # --- Parsing XML léger sans lxml ---
        xml = resp.text

        # Nombre total de résultats (première page seulement)
        if start_record == 1:
            m = re.search(r"<numberOfRecords>(\d+)</numberOfRecords>", xml)
            total = int(m.group(1)) if m else 0
            if total == 0:
                break  # aucun numéro trouvé

        # Extraction des enregistrements
        records = re.findall(r"<srw:record>(.*?)</srw:record>", xml, re.DOTALL)
        if not records:
            break

        for rec in records:
            def first(tag, text=rec):
                m = re.search(rf"<dc:{tag}[^>]*>(.*?)</dc:{tag}>", text, re.DOTALL)
                return m.group(1).strip() if m else ""

            ark_issue = first("identifier")
            # Gallica renvoie parfois des URL complètes, parfois juste l'ARK
            if not ark_issue.startswith("http"):
                ark_issue = f"{GALLICA_BASE}/{ark_issue}" if ark_issue else ""
            
            # Récupère l'ARK court pour construire l'URL propre
            m_ark = re.search(r"ark:/12148/\S+", ark_issue)
            clean_url = f"{GALLICA_BASE}/{m_ark.group(0)}" if m_ark else ark_issue

            issues.append({
                "titre":       first("title") or "(sans titre)",
                "date":        first("date"),
                "description": first("description"),
                "url":         clean_url,
            })

        start_record += page_size
        if start_record > min(total, max_records):
            break

        time.sleep(0.3)  # politesse envers l'API

    return issues


# ─── Interface Streamlit ───────────────────────────────────────────────────────

st.set_page_config(page_title="Moissonneur Gallica", page_icon="📰", layout="wide")
st.title("📰 Moissonneur Gallica")
st.caption(
    "Entrez l'URL d'une revue sur [Gallica](https://gallica.bnf.fr) "
    "pour lister ses numéros disponibles."
)

url = st.text_input(
    "URL de la revue Gallica",
    placeholder="ex. : https://gallica.bnf.fr/ark:/12148/cb32798952d/date",
)

if url:
    # ── Étape 1 : extraction de l'ARK ──────────────────────────────────────
    ark = extract_ark(url)

    if not ark:
        st.error(
            "❌ Impossible d'extraire un identifiant ARK depuis cette URL. "
            "Vérifiez que l'URL pointe bien vers une revue Gallica "
            "(elle doit contenir `ark:/12148/…`)."
        )
        st.stop()

    st.success(f"✅ ARK détecté : `{ark}`")

    # ── Étape 2 : moissonnage ───────────────────────────────────────────────
    with st.spinner("Interrogation de l'API Gallica SRU…"):
        try:
            issues = get_issues(ark)
        except TimeoutError as e:
            st.error(f"⏱️ Délai dépassé : {e}")
            st.stop()
        except ConnectionError as e:
            st.error(f"🌐 Erreur réseau : {e}")
            st.stop()
        except Exception as e:
            st.error(f"💥 Erreur inattendue : {e}")
            st.stop()

    # ── Étape 3 : affichage des résultats ───────────────────────────────────
    if not issues:
        st.warning(
            "⚠️ Aucun numéro trouvé. "
            "L'ARK détecté ne correspond peut-être pas à une revue, "
            "ou la revue n'est pas indexée via le type « fascicule »."
        )
    else:
        st.metric("Numéros récupérés", len(issues))

        df = pd.DataFrame(issues)

        # Colonne URL cliquable
        if "url" in df.columns:
            df["url"] = df["url"].apply(
                lambda u: f'<a href="{u}" target="_blank">🔗 Voir</a>' if u else ""
            )
            st.write(
                df.to_html(escape=False, index=False),
                unsafe_allow_html=True,
            )
        else:
            st.dataframe(df, use_container_width=True)

        # ── Export CSV ──────────────────────────────────────────────────────
        csv_df = pd.DataFrame(issues)  # version brute (URL en texte)
        csv = csv_df.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="⬇️ Télécharger en CSV",
            data=csv,
            file_name="gallica_issues.csv",
            mime="text/csv",
        )
