import streamlit as st
import requests
import re
import time
import pandas as pd

# ─── Configuration ────────────────────────────────────────────────────────────

GALLICA_SRU    = "https://gallica.bnf.fr/SRU"
GALLICA_BASE   = "https://gallica.bnf.fr"
TIMEOUT        = 15
MAX_ISSUES     = 500

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; GallicaHarvester/1.0; "
        "+https://github.com/user/gallica-harvester)"
    )
}

# ─── Utilitaires ──────────────────────────────────────────────────────────────

def extract_ark(url: str) -> str | None:
    match = re.search(r"ark:/12148/([a-z0-9]+)", url)
    if match:
        return f"ark:/12148/{match.group(1)}"
    return None


def build_gallica_url(ark_value: str) -> str:
    """
    Construit une URL Gallica valide depuis n'importe quelle forme d'ARK.
    Formes acceptées :
      - "bpt6k310197"              → https://gallica.bnf.fr/ark:/12148/bpt6k310197
      - "ark:/12148/bpt6k310197"   → https://gallica.bnf.fr/ark:/12148/bpt6k310197
      - "https://gallica.bnf.fr/…" → inchangé (déjà une URL complète valide)
    """
    ark_value = ark_value.strip()

    # Déjà une URL complète avec ark:/12148/ → OK
    if ark_value.startswith("http") and "ark:/12148/" in ark_value:
        # On nettoie les qualifiers parasites (.r=...) éventuels
        return re.sub(r"\.r=[^\s/]*", "", ark_value)

    # Forme "ark:/12148/bpt6k…"
    if ark_value.startswith("ark:/12148/"):
        short = ark_value.replace("ark:/12148/", "")
        return f"{GALLICA_BASE}/ark:/12148/{short}"

    # Forme courte "bpt6k…" ou "btv1b…" (retournée par l'API Issues)
    if re.match(r"^[a-z0-9]+$", ark_value):
        return f"{GALLICA_BASE}/ark:/12148/{ark_value}"

    # Fallback : on retourne tel quel
    return ark_value


def get_issues_via_sru(ark_id: str, max_records: int = MAX_ISSUES) -> list[dict]:
    """
    Méthode principale : arkPress all "cb32731059c_date"
    """
    short_id = ark_id.replace("ark:/12148/", "")
    ark_press = f"{short_id}_date"

    issues = []
    start_record = 1
    page_size = 50
    total = None

    while True:
        params = {
            "operation":      "searchRetrieve",
            "version":        "1.2",
            "query":          f'(dc.type all "fascicule") and arkPress all "{ark_press}"',
            "startRecord":    start_record,
            "maximumRecords": page_size,
            "collapsing":     "false",
        }

        try:
            resp = requests.get(GALLICA_SRU, params=params, headers=HEADERS, timeout=TIMEOUT)
            resp.raise_for_status()
        except requests.exceptions.Timeout:
            raise TimeoutError(f"Gallica SRU n'a pas répondu dans les {TIMEOUT}s.")
        except requests.exceptions.HTTPError as e:
            raise ConnectionError(f"Erreur HTTP {resp.status_code} : {e}")
        except requests.exceptions.RequestException as e:
            raise ConnectionError(f"Erreur réseau : {e}")

        xml = resp.text

        if start_record == 1:
            m = re.search(r"<numberOfRecords>(\d+)</numberOfRecords>", xml)
            total = int(m.group(1)) if m else 0
            if total == 0:
                break

        records = re.findall(r"<srw:record>(.*?)</srw:record>", xml, re.DOTALL)
        if not records:
            break

        for rec in records:
            def first(tag, text=rec):
                m = re.search(rf"<dc:{tag}[^>]*>(.*?)</dc:{tag}>", text, re.DOTALL)
                return m.group(1).strip() if m else ""

            raw_id = first("identifier")
            url = build_gallica_url(raw_id)

            issues.append({
                "date":        first("date"),
                "titre":       first("title") or "(sans titre)",
                "description": first("description"),
                "url":         url,
            })

        start_record += page_size
        if start_record > min(total, max_records):
            break

        time.sleep(0.3)

    return issues


def get_issues_via_issues_api(ark_id: str) -> list[dict]:
    """
    Fallback : API Issues de Gallica, récupère fascicule par fascicule.
    """
    base_url = f"{GALLICA_BASE}/services/Issues"

    try:
        r = requests.get(
            base_url,
            params={"ark": f"{ark_id}/date"},
            headers=HEADERS,
            timeout=TIMEOUT,
        )
        r.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise ConnectionError(f"Erreur API Issues (années) : {e}")

    years = re.findall(r"<year>(\d{4})</year>", r.text)
    if not years:
        return []

    issues = []

    for year in years:
        try:
            r2 = requests.get(
                base_url,
                params={"ark": f"{ark_id}/date", "date": year},
                headers=HEADERS,
                timeout=TIMEOUT,
            )
            r2.raise_for_status()
        except requests.exceptions.RequestException:
            continue

        # L'API retourne : <issue ark="bpt6k310197" dayOfYear="…">1841/01/01 (T1,N1).</issue>
        for m in re.finditer(
            r'<issue\b[^>]*\bark="([^"]+)"[^>]*>([^<]*)</issue>',
            r2.text
        ):
            raw_ark  = m.group(1).strip()   # ex: "bpt6k310197"
            label    = m.group(2).strip()   # ex: "1841/01/01 (T1,N1)."
            clean_url = build_gallica_url(raw_ark)

            issues.append({
                "date":        year,
                "titre":       label or f"Numéro de {year}",
                "description": "",
                "url":         clean_url,
            })

        time.sleep(0.2)

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
    placeholder="ex. : https://gallica.bnf.fr/ark:/12148/cb32731059c/date",
)

if url:
    ark = extract_ark(url)

    if not ark:
        st.error(
            "❌ Impossible d'extraire un identifiant ARK depuis cette URL. "
            "Vérifiez que l'URL pointe bien vers une revue Gallica "
            "(elle doit contenir `ark:/12148/…`)."
        )
        st.stop()

    st.success(f"✅ ARK détecté : `{ark}`")

    issues = []
    method_used = ""

    with st.spinner("Interrogation de l'API Gallica SRU (méthode arkPress)…"):
        try:
            issues = get_issues_via_sru(ark)
            method_used = "SRU / arkPress"
        except (TimeoutError, ConnectionError) as e:
            st.warning(f"⚠️ SRU indisponible ({e}), bascule sur l'API Issues…")
        except Exception as e:
            st.warning(f"⚠️ Erreur SRU inattendue ({e}), bascule sur l'API Issues…")

    if not issues:
        with st.spinner("Interrogation de l'API Issues de Gallica…"):
            try:
                issues = get_issues_via_issues_api(ark)
                method_used = "API Issues (fallback)"
            except (TimeoutError, ConnectionError) as e:
                st.error(f"🌐 Erreur réseau : {e}")
                st.stop()
            except Exception as e:
                st.error(f"💥 Erreur inattendue : {e}")
                st.stop()

    if not issues:
        st.warning(
            "⚠️ Aucun numéro trouvé. "
            "L'ARK ne correspond peut-être pas à une revue, "
            "ou celle-ci n'est pas indexée comme « fascicule »."
        )
    else:
        st.metric("Numéros récupérés", len(issues))
        st.caption(f"Source : {method_used}")

        df = pd.DataFrame(issues)[["date", "titre", "description", "url"]]

        df_display = df.copy()
        df_display["url"] = df_display["url"].apply(
            lambda u: f'<a href="{u}" target="_blank">🔗 Voir</a>' if u else ""
        )
        st.write(df_display.to_html(escape=False, index=False), unsafe_allow_html=True)

        # Export CSV avec URLs en clair
        csv = df.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="⬇️ Télécharger en CSV",
            data=csv,
            file_name="gallica_issues.csv",
            mime="text/csv",
        )
