import json
import re


# -------------------------------------------------
# 1️⃣ Nettoyage général
# -------------------------------------------------

def normalize_whitespace(text):
    text = re.sub(r"\n\s*\n+", "\n\n", text)  # max 2 sauts de ligne
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def remove_noise(text):
    noise_markers = [
        "Voir plus",
        "Envie d’en savoir plus",
        "Rencontrez",
        "Découvrez l'entreprise",
        "Explorez la vitrine",
        "ILS SONT SOCIABLES",
        "L'entreprise",
        "Les avantages salariés",
        "Engagements",
        "D’autres offres vous correspondent",
        "A PROPOS",
        "LA NEWSLETTER QUI FAIT LE TAF"
    ]

    text_lower = text.lower()

    for marker in noise_markers:
        idx = text_lower.find(marker.lower())
        if idx != -1:
            text = text[:idx]
            break

    return text.strip()


# -------------------------------------------------
# 2️⃣ Extraction sections fiable
# -------------------------------------------------

def extract_sections(text):

    text_lower = text.lower()

    poste_marker = "descriptif du poste"
    profil_marker = "profil recherché"

    start_poste = text_lower.find(poste_marker)
    start_profil = text_lower.find(profil_marker)

    description_poste = ""
    description_profil = ""

    # Extraire description du poste
    if start_poste != -1:
        if start_profil != -1:
            description_poste = text[start_poste + len(poste_marker):start_profil]
        else:
            description_poste = text[start_poste + len(poste_marker):]

    # Extraire profil
    if start_profil != -1:
        description_profil = text[start_profil + len(profil_marker):]

    # Nettoyage
    description_poste = remove_noise(description_poste)
    description_profil = remove_noise(description_profil)

    description_poste = normalize_whitespace(description_poste)
    description_profil = normalize_whitespace(description_profil)

    return description_poste, description_profil


# -------------------------------------------------
# 3️⃣ Extraction metadata simple
# -------------------------------------------------

def extract_metadata(text):

    metadata = {}
    text_lower = text.lower()

    if "stage" in text_lower:
        metadata["contract_type"] = "Stage"
    elif "cdi" in text_lower:
        metadata["contract_type"] = "CDI"
    elif "cdd" in text_lower:
        metadata["contract_type"] = "CDD"

    if "bac +5" in text_lower or "bac+5" in text_lower:
        metadata["education_level"] = "Bac+5"

    return metadata


# -------------------------------------------------
# 4️⃣ Pipeline principal
# -------------------------------------------------

def clean_json(input_path, output_path):

    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    for job in data["offers"]:

        raw_text = job.get("description_raw", "")

        poste, profil = extract_sections(raw_text)
        metadata = extract_metadata(raw_text)

        job["description_poste"] = poste
        job["description_profil"] = profil
        job["metadata"] = metadata

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print("✅ Nettoyage terminé :", output_path)


# -------------------------------------------------
# 5️⃣ Lancement
# -------------------------------------------------

if __name__ == "__main__":
    clean_json("job_offers_wttj_clean_v3.json", "job_offers_wttj_clean_v4.json")