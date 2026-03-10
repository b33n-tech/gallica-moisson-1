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


def get_issues_via_sru(ark_id: str, max_records: int = MAX_ISSUES) -> list[dict]:
    """
    Méthode principale : arkPress all "cb32731059c_date"
    Recommandée par la documentation BnF.
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

            identifier = first("identifier")
            if not identifier.startswith("http"):
                identifier = f"{GALLICA_BASE}/{identifier}" if identifier else ""

            m_ark = re.search(r"ark:/12148/\S+", identifier)
            clean_url = f"{GALLICA_BASE}/{m_ark.group(0)}" if m_ark else identifier

            issues.append({
                "titre":       first("title") or "(sans titre)",
                "date":        first("date"),
                "description": first("description"),
                "url":         clean_url,
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

        for m in re.finditer(r'<issue[^>]*ark="([^"]+)"[^>]*>(.*?)</issue>', r2.text, re.DOTALL):
            issue_ark = m.group(1)
            precision = re.sub(r"<[^>]+>", "", m.group(2)).strip()
            clean_url = f"{GALLICA_BASE}/{issue_ark}" if not issue_ark.startswith("http") else issue_ark
            issues.append({
                "titre":       precision or f"Numéro de {year}",
                "date":        year,
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

        df = pd.DataFrame(issues)

        if "url" in df.columns:
            df_display = df.copy()
            df_display["url"] = df_display["url"].apply(
                lambda u: f'<a href="{u}" target="_blank">🔗 Voir</a>' if u else ""
            )
            st.write(df_display.to_html(escape=False, index=False), unsafe_allow_html=True)
        else:
            st.dataframe(df, use_container_width=True)

        csv = df.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="⬇️ Télécharger en CSV",
            data=csv,
            file_name="gallica_issues.csv",
            mime="text/csv",
        )
